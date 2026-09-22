from __future__ import annotations

from argparse import ArgumentParser
import json
from math import isclose
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "strategy.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = ArgumentParser(description="Validate FaCail published data.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    names = ["market", "sectors", "candidates", "holdings", "intraday", "review", "watchlist", "today"]
    payloads = {name: read_json(args.data_dir / f"{name}.json") for name in names}
    config = read_json(CONFIG_PATH)

    assert set(config["buy_conditions"]) == {"shrink_pullback", "volume_breakout", "strong_sector_pullback"}
    assert [item["id"] for item in payloads["market"]["stage_options"]] == ["premarket", "intraday", "postmarket"]
    holding_count = payloads["holdings"]["summary"]["holding_count"]
    assert len(payloads["holdings"]["premarket"]) == holding_count
    assert len(payloads["intraday"]["holdings"]) == holding_count
    assert len(payloads["review"]["holdings"]) == holding_count
    assert all(len(item["conditions"]) == 3 for item in payloads["candidates"]["all"])
    assert all(
        condition["status"] in {"未满足", "接近满足", "已满足"}
        for item in payloads["candidates"]["all"]
        for condition in item["conditions"]
    )
    assert all("flags" in item for item in payloads["intraday"]["holdings"])
    assert payloads["holdings"]["premarket"] == sorted(
        payloads["holdings"]["premarket"],
        key=lambda item: item["attention_score"],
        reverse=True,
    )
    assert payloads["candidates"]["summary"]["pullback_count"] == len(payloads["candidates"]["pullback_watch"])
    if payloads["market"]["data_source"]["id"] == "astock":
        assert all((payload.get("data_source") or payload.get("summary", {}).get("data_source"))["id"] == "astock"
                   for payload in payloads.values())
        holdings = payloads["holdings"]["holdings"]
        summary = payloads["holdings"]["summary"]
        assert len({item["code"] for item in holdings}) == holding_count
        assert all(item.get("source") != "mock" and item.get("freshness") in {"current", "last_trading_day", "stale", "missing"}
                   for item in holdings)
        assert isclose(sum(item["market_value"] for item in holdings), summary["total_market_value"], abs_tol=0.01)
        assert isclose(sum(item["cost_basis"] for item in holdings), summary["total_cost_basis"], abs_tol=0.01)
        assert isclose(sum(item["unrealized_pnl"] for item in holdings), summary["total_unrealized_pnl"], abs_tol=0.01)
        local_path = ROOT / "config" / "holdings.local.json"
        if local_path.exists():
            local = read_json(local_path)
            configured = local if isinstance(local, list) else local.get("holdings", [])
            assert {item["code"] for item in holdings} <= {item["code"] for item in configured}
            assert len(holdings) >= max(1, len(configured) - 1)
            assert all(any(item["code"] == row["code"] and item["cost"] == row["cost"] and
                           item["shares"] == row["shares"] for row in holdings) for item in configured)
    print("FaCail data contracts and rule outputs are valid.")


if __name__ == "__main__":
    main()
