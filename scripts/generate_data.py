from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from providers.mock_provider import MockProvider


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "strategy.json"
DATA_DIR = ROOT / "data"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(mean(values[-window:]), 4)


def pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def enrich_stock(stock: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    bars = stock["bars"]
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    latest = bars[-1]
    previous = bars[-2]

    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    avg_volume_5d = round(mean(volumes[-6:-1]), 2)
    volume_ratio_5d = latest["volume"] / avg_volume_5d if avg_volume_5d else None
    change_pct = (latest["close"] - previous["close"]) / previous["close"]
    amplitude = (latest["high"] - latest["low"]) / previous["close"]
    ma20_distance = (latest["close"] - ma20) / ma20 if ma20 else None
    recent_gain_20d = (latest["close"] - closes[-21]) / closes[-21] if len(closes) >= 21 else None
    relative_industry_strength = change_pct - stock["industry_return"]

    labels = []
    if ma20_distance is not None and ma20_distance >= 0:
        labels.append("MA20上方")
    if ma20_distance is not None and ma20_distance < 0:
        labels.append("MA20下方")
    if ma20_distance is not None and ma20_distance <= config["trend_break_ma20_distance"]:
        labels.append("趋势破坏")
    if volume_ratio_5d is not None and volume_ratio_5d <= config["shrink_volume_ratio_5d_max"]:
        labels.append("缩量")
    elif volume_ratio_5d is not None and volume_ratio_5d >= config["abnormal_volume_ratio_5d_min"]:
        labels.append("放量异常")
    else:
        labels.append("正常量能")
    if relative_industry_strength >= config["industry_strength_threshold"]:
        labels.append("强于板块")
    elif relative_industry_strength <= -config["industry_strength_threshold"]:
        labels.append("弱于板块")
    if recent_gain_20d is not None and recent_gain_20d > config["recent_gain_20d_overheat"]:
        labels.append("过热")

    is_pullback = (
        config["pullback_change_pct_min"] <= change_pct <= config["pullback_change_pct_max"]
        and volume_ratio_5d is not None
        and volume_ratio_5d <= config["shrink_volume_ratio_5d_max"]
        and ma20_distance is not None
        and ma20_distance >= -config["ma20_near_distance_abs"]
    )
    if is_pullback:
        labels.insert(0, "观察")
    else:
        labels.insert(0, "等待确认")

    return {
        "code": stock["code"],
        "name": stock["name"],
        "date": latest["date"],
        "close": latest["close"],
        "change_pct": pct(change_pct),
        "high": latest["high"],
        "low": latest["low"],
        "amplitude": pct(amplitude),
        "volume": latest["volume"],
        "avg_volume_5d": avg_volume_5d,
        "volume_ratio_5d": pct(volume_ratio_5d),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma20_distance": pct(ma20_distance),
        "recent_gain_20d": pct(recent_gain_20d),
        "industry": stock["industry"],
        "industry_return": pct(stock["industry_return"]),
        "industry_rank": stock["industry_rank"],
        "limit_up_count": stock["limit_up_count"],
        "advance_ratio": pct(stock["advance_ratio"]),
        "relative_industry_strength": pct(relative_industry_strength),
        "labels": labels,
        "is_pullback_candidate": is_pullback,
    }


def build_today(stocks: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    enriched = [enrich_stock(stock, config) for stock in stocks]
    candidates = [
        stock
        for stock in enriched
        if stock["is_pullback_candidate"] and "过热" not in stock["labels"] and "趋势破坏" not in stock["labels"]
    ]
    candidates.sort(key=lambda item: (item["industry_rank"], -(item["relative_industry_strength"] or 0)))

    summary = {
        "trade_date": enriched[0]["date"] if enriched else date.today().isoformat(),
        "candidate_count": len(candidates),
        "market_note": "Mock 数据显示候选集中在回踩与缩量状态，当前输出仅用于本地开发验证。",
        "data_source": "mock",
        "missing_data": [],
    }
    return {"summary": summary, "candidates": candidates, "universe": enriched}


def build_holdings(stocks: list[dict[str, Any]], holdings: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    by_code = {stock["code"]: enrich_stock(stock, config) for stock in stocks}
    rows = []
    for holding in holdings:
        info = by_code[holding["code"]]
        market_value = round(info["close"] * holding["shares"], 2)
        cost_value = round(holding["cost"] * holding["shares"], 2)
        rows.append(
            {
                **info,
                "shares": holding["shares"],
                "cost": holding["cost"],
                "market_value": market_value,
                "position_return": pct((market_value - cost_value) / cost_value),
            }
        )

    total_market_value = sum(row["market_value"] for row in rows)
    stronger = sum(1 for row in rows if "强于板块" in row["labels"])
    weaker = sum(1 for row in rows if "弱于板块" in row["labels"])
    above_ma20 = sum(1 for row in rows if "MA20上方" in row["labels"])
    below_ma20 = sum(1 for row in rows if "MA20下方" in row["labels"])
    shrink_pullback = sum(1 for row in rows if row["is_pullback_candidate"])
    abnormal_volume = sum(1 for row in rows if "放量异常" in row["labels"])
    weighted_change = (
        sum(row["change_pct"] * row["market_value"] for row in rows if row["change_pct"] is not None) / total_market_value
        if total_market_value
        else 0
    )

    return {
        "summary": {
            "trade_date": rows[0]["date"] if rows else date.today().isoformat(),
            "holding_count": len(rows),
            "portfolio_change_pct": pct(weighted_change),
            "total_market_value": round(total_market_value, 2),
            "stronger_than_industry_count": stronger,
            "weaker_than_industry_count": weaker,
            "above_ma20_count": above_ma20,
            "below_ma20_count": below_ma20,
            "shrink_pullback_count": shrink_pullback,
            "abnormal_volume_count": abnormal_volume,
            "review": "今日持仓主要关注 MA20 位置、相对板块强弱和量能变化；所有结论来自 Mock 数据计算结果。",
        },
        "holdings": rows,
    }


def build_watchlist(stocks: list[dict[str, Any]], watchlist: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    by_code = {stock["code"]: enrich_stock(stock, config) for stock in stocks}
    records = []
    for item in watchlist:
        info = by_code[item["code"]]
        records.append(
            {
                "code": info["code"],
                "name": info["name"],
                "added_date": item["added_date"],
                "added_price": item["added_price"],
                "current_price": info["close"],
                "price_change_since_added": pct((info["close"] - item["added_price"]) / item["added_price"]),
                "ma20_at_added": info["ma20"],
                "current_ma20_distance": info["ma20_distance"],
                "volume_state": next((label for label in info["labels"] if label in ["缩量", "正常量能", "明显放量", "放量异常"]), None),
                "industry": info["industry"],
                "industry_strength": info["industry_return"],
                "triggered_conditions": item["triggered_conditions"],
                "current_labels": info["labels"],
            }
        )
    return {"summary": {"watch_count": len(records), "data_source": "mock"}, "watchlist": records}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    config = load_config()
    provider = MockProvider()
    stocks = provider.get_daily_bars()
    DATA_DIR.mkdir(exist_ok=True)
    write_json(DATA_DIR / "today.json", build_today(stocks, config))
    write_json(DATA_DIR / "holdings.json", build_holdings(stocks, provider.get_holdings(), config))
    write_json(DATA_DIR / "watchlist.json", build_watchlist(stocks, provider.get_watchlist(), config))
    print("Generated data/today.json, data/holdings.json, data/watchlist.json from MockProvider")


if __name__ == "__main__":
    main()
