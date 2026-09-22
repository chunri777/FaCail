from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from trading_calendar import SHANGHAI, is_trading_day, latest_trading_day, load_closed_days, trade_stage


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_PATH = LOG_DIR / "update-state.json"
DATA_NAMES = ("market", "sectors", "candidates", "holdings", "intraday", "review", "watchlist", "today")
DATA_PATHS = tuple(f"data/{name}.json" for name in DATA_NAMES)
PAGES_MARKET_URL = "https://chunri777.github.io/FaCail/data/market.json"


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def scheduled_slot(now: datetime, closed_days: set, completed: set[str]) -> tuple[str, str] | None:
    local = now.astimezone(SHANGHAI)
    day = local.date()
    if not is_trading_day(day, closed_days):
        return None
    minute = local.hour * 60 + local.minute
    if minute >= 15 * 60 + 10:
        stage, slot = "postmarket", "15:10"
    elif 13 * 60 + 5 <= minute < 15 * 60:
        offset = (minute - (13 * 60 + 5)) // 10
        total = 13 * 60 + 5 + 10 * offset
        stage, slot = "intraday", f"{total // 60:02d}:{total % 60:02d}"
    elif 9 * 60 + 35 <= minute <= 11 * 60 + 30:
        offset = (minute - (9 * 60 + 35)) // 10
        total = 9 * 60 + 35 + 10 * offset
        stage, slot = "intraday", f"{total // 60:02d}:{total % 60:02d}"
    elif 8 * 60 + 30 <= minute < 9 * 60 + 30:
        stage, slot = "premarket", "08:30"
    else:
        return None
    key = f"{day.isoformat()} {slot} {stage}"
    return None if key in completed else (stage, key)


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"completed_slots": [], "pending_commit": None, "closed_day_checked": None}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def append_log(record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"update-{shanghai_now():%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def safe_error(message: str) -> str:
    redacted = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[redacted]@", message)
    redacted = re.sub(r"(?i)(token|cookie|password|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", redacted)
    return redacted[-400:]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)


def checked_git(*args: str) -> str:
    result = git(*args)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {safe_error(result.stderr or result.stdout)}")
    return result.stdout.strip()


def pending_commit_count() -> int:
    return int(checked_git("rev-list", "--count", "origin/main..HEAD"))


def retry_pending_push(state: dict[str, Any], record: dict[str, Any]) -> None:
    pending = state.get("pending_commit")
    ahead = pending_commit_count()
    if not pending:
        if ahead:
            raise RuntimeError("Unrecognized unpublished local commits; automatic push stopped.")
        return
    if checked_git("rev-parse", "HEAD") != pending or ahead != 1:
        raise RuntimeError("Pending commit no longer matches HEAD; automatic push stopped.")
    result = git("push", "origin", "main")
    if result.returncode:
        record["push_success"] = False
        record["retry_needed"] = True
        record["commit"] = pending
        raise RuntimeError(f"Pending push failed: {safe_error(result.stderr or result.stdout)}")
    state["pending_commit"] = None
    save_state(state)
    record["push_success"] = True
    record["retried_commit"] = pending


def parse_request_stats(output: str) -> dict[str, int]:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("request_stats"), dict):
            return payload["request_stats"]
    return {}


def read_payloads(directory: Path) -> dict[str, dict[str, Any]]:
    return {name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8")) for name in DATA_NAMES}


def publication_quality(payloads: dict[str, dict[str, Any]], stage: str, now: datetime,
                        configured_codes: set[str], closed_days: set) -> dict[str, Any]:
    market = payloads["market"]
    rows = payloads["holdings"]["holdings"]
    source = market["data_source"]
    if any((payload.get("data_source") or payload.get("summary", {}).get("data_source"))["id"] != "astock"
           for payload in payloads.values()):
        raise ValueError("A non-astock source cannot be published.")
    if source["stage"] != stage:
        raise ValueError("Generated stage does not match the scheduled stage.")
    expected_day = latest_trading_day(now.date(), closed_days, include_today=stage != "premarket").isoformat()
    if market["trade_date"] != expected_day or source["last_trading_day"] != expected_day:
        raise ValueError(f"Market date is not the expected trading day {expected_day}.")
    expected_freshness = "last_trading_day" if stage == "premarket" else "current"
    if source["freshness"] != expected_freshness:
        raise ValueError(f"Market freshness is {source['freshness']}, expected {expected_freshness}.")
    valid_indices = [item for item in market["indices"] if item.get("value") is not None and
                     item.get("source_timestamp") and item.get("freshness") == expected_freshness]
    valid_holdings = [item for item in rows if item.get("code") in configured_codes and
                      item.get("source") == "tencent" and item.get("close") is not None and
                      item.get("source_timestamp") and item.get("freshness") == expected_freshness]
    quality = {"configured_holdings": len(configured_codes), "successful_holdings": len(valid_holdings),
               "successful_indices": len(valid_indices),
               "holding_success_rate": round(len(valid_holdings) / len(configured_codes), 4)}
    if len(configured_codes) != 8 or len(valid_holdings) < 7 or len(valid_indices) < 3:
        raise ValueError(f"Minimum publication quality not met: {quality}")
    if len({item["code"] for item in valid_holdings}) != len(rows):
        raise ValueError("Published holdings include an unverified or duplicate position.")
    return quality


def publish_files(staging: Path) -> bool:
    changed = any((ROOT / path).read_bytes() != (staging / Path(path).name).read_bytes() for path in DATA_PATHS)
    if not changed:
        return False
    originals = {path: (ROOT / path).read_bytes() for path in DATA_PATHS}
    try:
        for path in DATA_PATHS:
            target = ROOT / path
            temporary = target.with_name(f".{target.name}.update-tmp")
            temporary.write_bytes((staging / target.name).read_bytes())
            temporary.replace(target)
    except OSError:
        for path, contents in originals.items():
            (ROOT / path).write_bytes(contents)
        raise
    return True


def wait_for_pages(generated_at: str, timeout_seconds: int = 120) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            request = Request(PAGES_MARKET_URL + f"?v={int(time.time())}", headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=12) as response:
                live = json.load(response)
            if live.get("data_source", {}).get("generated_at") == generated_at:
                return True
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(5)
    return False


def run_update(now: datetime, force: bool = False, wait_pages: bool = True) -> dict[str, Any]:
    LOG_DIR.mkdir(exist_ok=True)
    closed_days = load_closed_days()
    local = now.astimezone(SHANGHAI)
    state = read_state()
    record: dict[str, Any] = {"started_at": local.isoformat(timespec="seconds"), "provider": "astock",
                              "is_trading_day": is_trading_day(local.date(), closed_days), "stage": None,
                              "requests": 0, "cache_hits": 0, "negative_cache_hits": 0,
                              "fallback_count": 0, "request_failures": 0,
                              "quality": None, "json_valid": False, "new_data": False,
                              "committed": False, "push_success": None, "error": None}
    try:
        checked_git("status", "--short")
        retry_pending_push(state, record)
        if not record["is_trading_day"]:
            if state.get("closed_day_checked") != local.date().isoformat():
                state["closed_day_checked"] = local.date().isoformat()
                save_state(state)
                record["result"] = "closed_day"
            else:
                record["result"] = "closed_day_already_checked"
            return record
        slot = scheduled_slot(local, closed_days, set(state.get("completed_slots", [])))
        if force:
            stage = trade_stage(local, closed_days)
            slot = (stage, f"{local.date().isoformat()} manual {local:%H:%M} {stage}")
        if slot is None:
            record["result"] = "not_due"
            return record
        stage, key = slot
        record["stage"] = stage
        record["slot"] = key
        if git("status", "--porcelain", "--", "data/").stdout.strip():
            raise RuntimeError("Published data has pre-existing local edits; automatic overwrite stopped.")
        holdings_path = ROOT / "config" / "holdings.local.json"
        if not holdings_path.exists():
            raise RuntimeError("Local holdings configuration is missing.")
        local_holdings = json.loads(holdings_path.read_text(encoding="utf-8"))
        configured = local_holdings if isinstance(local_holdings, list) else local_holdings.get("holdings", [])
        configured_codes = {item["code"] for item in configured}
        with tempfile.TemporaryDirectory(prefix="facail-stage-", dir=LOG_DIR) as temporary:
            staging = Path(temporary)
            command = [sys.executable, str(ROOT / "scripts" / "generate_data.py"), "--provider", "astock",
                       "--stage", "auto", "--output-dir", str(staging)]
            generated = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            stats = parse_request_stats(generated.stdout)
            record["requests"] = stats.get("total_requests", 0)
            record["cache_hits"] = stats.get("cache_hits", 0)
            record["negative_cache_hits"] = stats.get("negative_cache_hits", 0)
            record["fallback_count"] = stats.get("fallback_count", 0)
            record["request_failures"] = stats.get("request_failures", 0)
            if generated.returncode:
                raise RuntimeError(f"Generation failed: {safe_error(generated.stderr or generated.stdout)}")
            validated = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_data.py"),
                                        "--data-dir", str(staging)], cwd=ROOT, text=True, capture_output=True, check=False)
            if validated.returncode:
                raise RuntimeError(f"JSON validation failed: {safe_error(validated.stderr or validated.stdout)}")
            record["json_valid"] = True
            payloads = read_payloads(staging)
            record["quality"] = {"configured_holdings": len(configured_codes),
                                 "generated_holdings": len(payloads["holdings"]["holdings"]),
                                 "indices_with_values": sum(item.get("value") is not None
                                                            for item in payloads["market"]["indices"])}
            record["quality"] = publication_quality(payloads, stage, local, configured_codes, closed_days)
            record["new_data"] = publish_files(staging)
            generated_at = payloads["market"]["data_source"]["generated_at"]
        if record["new_data"]:
            checked_git("add", "--", *DATA_PATHS)
        diff_status = git("diff", "--cached", "--quiet", "--", *DATA_PATHS).returncode
        if diff_status not in {0, 1}:
            raise RuntimeError("Could not inspect staged data changes.")
        if diff_status == 0:
            record["result"] = "unchanged"
        else:
            slot_time = key.split()[1] if not force else f"{local:%H:%M}"
            message = f"facail: {stage} update {local:%Y-%m-%d} {slot_time}"
            checked_git("commit", "-m", message, "--", *DATA_PATHS)
            commit = checked_git("rev-parse", "HEAD")
            record["committed"] = True
            record["commit"] = commit
            state["pending_commit"] = commit
            save_state(state)
            result = git("push", "origin", "main")
            if result.returncode:
                record["push_success"] = False
                record["retry_needed"] = True
                raise RuntimeError(f"Push failed: {safe_error(result.stderr or result.stdout)}")
            state["pending_commit"] = None
            save_state(state)
            record["push_success"] = True
            record["pages_confirmed"] = wait_for_pages(generated_at) if wait_pages else None
            record["result"] = "published"
        state["completed_slots"] = (state.get("completed_slots", []) + [key])[-200:]
        save_state(state)
        return record
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        record["error"] = safe_error(str(exc))
        record["result"] = "failed"
        return record
    finally:
        record["finished_at"] = shanghai_now().isoformat(timespec="seconds")
        append_log(record)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely update and publish real FaCail data.")
    parser.add_argument("--force", action="store_true", help="run once now on a trading day")
    parser.add_argument("--check", action="store_true", help="show schedule decision without network or writes")
    parser.add_argument("--no-wait-pages", action="store_true", help="skip GitHub Pages polling after push")
    args = parser.parse_args()
    now = shanghai_now()
    if args.check:
        closed = load_closed_days()
        print(json.dumps({"now": now.isoformat(timespec="seconds"), "is_trading_day": is_trading_day(now.date(), closed),
                          "due": scheduled_slot(now, closed, set(read_state().get("completed_slots", [])))}, ensure_ascii=False))
        return
    with (LOG_DIR / "update.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("FaCail update already running.")
            return
        result = run_update(now, force=args.force, wait_pages=not args.no_wait_pages)
    print(json.dumps(result, ensure_ascii=False))
    if result["result"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    LOG_DIR.mkdir(exist_ok=True)
    main()
