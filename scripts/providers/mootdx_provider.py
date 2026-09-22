from __future__ import annotations

import socket
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class MootdxProvider:
    def __init__(self, servers: list, timeout: float = 5, cache_dir: Path | None = None,
                 cache_seconds: int = 21600, negative_cache_seconds: int = 300) -> None:
        self.servers = servers
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.cache_seconds = cache_seconds
        self.negative_cache_seconds = negative_cache_seconds
        self.stats = {"requests": 0, "cache_hits": 0, "negative_cache_hits": 0, "failures": 0}
        self.last_retrieved_at: str | None = None

    def daily_bars(self, code: str, count: int = 60) -> list[dict]:
        cache_path = self.cache_dir / f"mootdx_{code}_{count}.json" if self.cache_dir else None
        failure_path = self.cache_dir / "mootdx_unavailable.json" if self.cache_dir else None
        if cache_path and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if time.time() - cached["time"] < self.cache_seconds and len(cached["bars"]) == count:
                    self.stats["cache_hits"] += 1
                    self.last_retrieved_at = datetime.fromtimestamp(cached["time"], ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
                    return cached["bars"]
            except (OSError, ValueError, KeyError):
                pass
        if failure_path and failure_path.exists():
            try:
                cached = json.loads(failure_path.read_text(encoding="utf-8"))
                if time.time() - cached["time"] < self.negative_cache_seconds:
                    self.stats["negative_cache_hits"] += 1
                    raise RuntimeError(cached["error"])
            except (OSError, ValueError, KeyError):
                pass
        try:
            from mootdx.quotes import Quotes
            from mootdx import config as mootdx_config
        except ImportError as exc:
            raise RuntimeError("mootdx is not installed") from exc
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            config_path = self.cache_dir / "mootdx_config.json"
            if not config_path.exists():
                config_path.write_text(json.dumps(mootdx_config.settings), encoding="utf-8")
            mootdx_config.CONF = str(config_path)
        errors = []
        for host, port in self.servers:
            self.stats["requests"] += 1
            client = None
            try:
                with socket.create_connection((host, port), timeout=self.timeout):
                    pass
                client = Quotes.factory(market="std", server=(host, port), timeout=self.timeout, auto_retry=False)
                frame = client.bars(symbol=code, frequency=9, offset=count)
                if frame is None or len(frame) < count:
                    raise ValueError("mootdx returned insufficient daily bars")
                rows = frame.tail(count).to_dict("records")
                bars = []
                for row in rows:
                    day = row.get("datetime") or row.get("date")
                    bars.append({"date": str(day)[:10], "open": float(row["open"]),
                                 "high": float(row["high"]), "low": float(row["low"]),
                                 "close": float(row["close"]), "volume": int(float(row["vol"]))})
                if len({bar["date"] for bar in bars}) != count:
                    raise ValueError("mootdx returned duplicate dates")
                if cache_path:
                    fetched_at = time.time()
                    cache_path.write_text(json.dumps({"time": fetched_at, "bars": bars}), encoding="utf-8")
                    self.last_retrieved_at = datetime.fromtimestamp(fetched_at, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
                if failure_path:
                    failure_path.unlink(missing_ok=True)
                return bars
            except Exception as exc:
                self.stats["failures"] += 1
                errors.append(f"{host}:{port}: {exc}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        message = "mootdx daily bars unavailable: " + "; ".join(errors)
        if failure_path:
            failure_path.write_text(json.dumps({"time": time.time(), "error": message}), encoding="utf-8")
        raise RuntimeError(message)
