from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "strategy.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    names = ["market", "sectors", "candidates", "holdings", "intraday", "review", "watchlist", "today"]
    payloads = {name: read_json(DATA_DIR / f"{name}.json") for name in names}
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
    print("FaCail data contracts and rule outputs are valid.")


if __name__ == "__main__":
    main()
