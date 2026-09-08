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
    return round(mean(values[-window:]), 4) if len(values) >= window else None


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def sector_status(sector: dict[str, Any], config: dict[str, Any]) -> str:
    rules = config["sector"]
    rank_limit = rules["universe_size"] * rules["strong_rank_percentile_max"]
    if sector["return_20d"] >= rules["overheat_20d_return_min"]:
        return "高位过热"
    if sector["return_5d"] <= rules["weak_5d_return_max"] or not sector["ma20_above"]:
        return "转弱"
    strong = (
        sector["rank"] <= rank_limit
        and sector["advance_ratio"] >= rules["strong_advance_ratio_min"]
        and sector["return_5d"] >= rules["strong_5d_return_min"]
    )
    if strong and sector["turnover_change"] >= rules["turnover_expansion_min"]:
        return "持续强势"
    if strong:
        return "强势"
    if sector["return_pct"] > 0.01 and sector["turnover_change"] >= rules["turnover_expansion_min"]:
        return "刚启动"
    if sector["return_pct"] < 0 and sector["return_5d"] > 0:
        return "回踩"
    return "观察"


def build_sectors(raw_sectors: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    total = config["sector"]["universe_size"]
    rows = []
    for item in raw_sectors:
        status = sector_status(item, config)
        rows.append(
            {
                **item,
                "rank_total": total,
                "rank_percentile": rounded(item["rank"] / total),
                "ma20_status": "MA20上方" if item["ma20_above"] else "MA20下方",
                "status": status,
                "is_strong": status in {"强势", "持续强势", "刚启动", "高位过热"},
            }
        )
    return sorted(rows, key=lambda item: item["rank"])


def condition_state(checks: list[dict[str, Any]]) -> str:
    met_count = sum(1 for check in checks if check["met"])
    if met_count == len(checks):
        return "已满足"
    if met_count >= len(checks) - 1:
        return "接近满足"
    return "未满足"


def build_conditions(stock: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    sector = stock["sector"]
    pullback = config["buy_conditions"]["shrink_pullback"]
    breakout = config["buy_conditions"]["volume_breakout"]
    sector_pullback = config["buy_conditions"]["strong_sector_pullback"]
    near_ma = min(
        abs(stock["close"] - stock["ma10"]) / stock["ma10"],
        abs(stock["close"] - stock["ma20"]) / stock["ma20"],
    )

    groups = [
        {
            "id": "shrink_pullback",
            "name": "条件 A · 缩量回踩",
            "checks": [
                {"label": "价格位于 MA20 观察区", "met": pullback["ma20_distance_min"] <= stock["ma20_distance"] <= pullback["ma20_distance_max"]},
                {"label": "日涨跌位于回踩区间", "met": pullback["daily_change_min"] <= stock["change_pct"] <= pullback["daily_change_max"]},
                {"label": "量能缩至 5 日均量阈值内", "met": stock["volume_ratio_5d"] <= pullback["volume_ratio_5d_max"]},
                {"label": "所属板块处于强势区", "met": sector["is_strong"]},
                {"label": "未明显跌破近期支撑", "met": stock["close"] >= stock["recent_support"] * (1 + pullback["key_low_break_tolerance"])},
            ],
        },
        {
            "id": "volume_breakout",
            "name": "条件 B · 放量突破",
            "checks": [
                {"label": "突破近 20 日关键高点", "met": stock["close"] > stock["previous_high"] * (1 + breakout["breakout_tolerance"])},
                {"label": "成交量达到 5 日均量阈值", "met": stock["volume_ratio_5d"] >= breakout["volume_ratio_5d_min"]},
                {"label": "所属板块处于强势区", "met": sector["is_strong"]},
                {"label": "个股强于所属板块", "met": stock["relative_sector_strength"] > 0},
                {"label": "距离 MA20 未超过限制", "met": stock["ma20_distance"] <= breakout["ma20_distance_max"]},
            ],
        },
        {
            "id": "strong_sector_pullback",
            "name": "条件 C · 强势板块回踩",
            "checks": [
                {"label": "板块排名进入市场前列", "met": sector["rank_percentile"] <= sector_pullback["sector_rank_percentile_max"]},
                {"label": "板块 5 日涨幅为正", "met": sector["return_5d"] >= sector_pullback["sector_5d_return_min"]},
                {"label": "股价接近 MA10 或 MA20", "met": near_ma <= sector_pullback["ma_distance_abs_max"]},
                {"label": "量能处于收缩状态", "met": stock["volume_ratio_5d"] <= sector_pullback["volume_ratio_5d_max"]},
                {"label": "未出现趋势破坏", "met": stock["ma20_distance"] > config["trend_break_ma20_distance"]},
            ],
        },
    ]
    for group in groups:
        group["status"] = condition_state(group["checks"])
        group["met_count"] = sum(1 for check in group["checks"] if check["met"])
        group["total_count"] = len(group["checks"])
    return groups


def enrich_stock(raw: dict[str, Any], sector: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    bars = raw["bars"]
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    latest, previous = bars[-1], bars[-2]
    ma5, ma10, ma20 = (moving_average(closes, window) for window in (5, 10, 20))
    volume_ma5 = round(mean(volumes[-6:-1]), 2)
    volume_ratio = latest["volume"] / volume_ma5
    change = (latest["close"] - previous["close"]) / previous["close"]
    ma20_distance = (latest["close"] - ma20) / ma20
    recent_gain = (latest["close"] - closes[-21]) / closes[-21]
    relative_strength = change - sector["return_pct"]
    previous_high = max(bar["high"] for bar in bars[-21:-1])
    recent_support = min(bar["low"] for bar in bars[-10:-1])

    labels = ["MA20上方" if ma20_distance >= 0 else "MA20下方"]
    if ma20_distance <= config["trend_break_ma20_distance"]:
        labels.append("趋势破坏")
    if volume_ratio <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"]:
        labels.append("缩量")
    elif volume_ratio >= config["abnormal_volume_ratio_5d_min"]:
        labels.append("放量异常")
    else:
        labels.append("正常量能")
    if relative_strength >= config["industry_strength_threshold"]:
        labels.append("强于板块")
    elif relative_strength <= -config["industry_strength_threshold"]:
        labels.append("弱于板块")
    if recent_gain > config["recent_gain_20d_overheat"]:
        labels.append("过热")

    stock = {
        "code": raw["code"],
        "name": raw["name"],
        "date": latest["date"],
        "close": latest["close"],
        "previous_close": previous["close"],
        "change_pct": rounded(change),
        "open": latest["open"],
        "high": latest["high"],
        "low": latest["low"],
        "amplitude": rounded((latest["high"] - latest["low"]) / previous["close"]),
        "volume": latest["volume"],
        "volume_ma5": volume_ma5,
        "volume_ratio_5d": rounded(volume_ratio),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma20_distance": rounded(ma20_distance),
        "recent_gain_20d": rounded(recent_gain),
        "previous_high": previous_high,
        "recent_support": recent_support,
        "industry": raw["industry"],
        "industry_return": sector["return_pct"],
        "industry_rank": sector["rank"],
        "limit_up_count": sector["limit_up_count"],
        "advance_ratio": sector["advance_ratio"],
        "relative_sector_strength": rounded(relative_strength),
        "sector": sector,
        "labels": labels,
        "bars": bars,
    }
    stock["conditions"] = build_conditions(stock, config)
    return stock


def candidate_record(stock: dict[str, Any]) -> dict[str, Any]:
    sector_strong = stock["sector"]["is_strong"]
    stock_strong = stock["relative_sector_strength"] > 0 and stock["ma20_distance"] >= 0
    if sector_strong and stock_strong:
        relation = "板块强 + 个股强"
        priority = 1
    elif sector_strong:
        relation = "板块强 + 个股弱"
        priority = 2
    elif stock_strong:
        relation = "板块弱 + 个股强"
        priority = 3
    else:
        relation = "板块弱 + 个股弱"
        priority = 4

    satisfied = [item for item in stock["conditions"] if item["status"] == "已满足"]
    close_to = [item for item in stock["conditions"] if item["status"] == "接近满足"]
    reason = [
        f"所属{stock['industry']}板块今日排名 {stock['industry_rank']}/{stock['sector']['rank_total']}。",
        f"板块 5 日涨跌为 {stock['sector']['return_5d'] * 100:+.2f}%，状态为{stock['sector']['status']}。",
        f"个股位于 MA20 {'上方' if stock['ma20_distance'] >= 0 else '下方'}，距离 {stock['ma20_distance'] * 100:+.2f}%。",
        f"今日成交量为 5 日均量的 {stock['volume_ratio_5d'] * 100:.0f}%。",
    ]
    waiting = []
    cfg_checks = {
        "价格位于 MA20 观察区": f"等待价格回到 MA20 附近，当前距离 {stock['ma20_distance'] * 100:+.2f}%",
        "量能缩至 5 日均量阈值内": f"等待量能进一步收缩，当前为 5 日均量的 {stock['volume_ratio_5d'] * 100:.0f}%",
        "所属板块处于强势区": f"等待{stock['industry']}板块重新进入强势区",
        "突破近 20 日关键高点": f"等待价格突破 {stock['previous_high']:.2f}",
    }
    for condition in stock["conditions"]:
        for check in condition["checks"]:
            if not check["met"] and check["label"] in cfg_checks and cfg_checks[check["label"]] not in waiting:
                waiting.append(cfg_checks[check["label"]])

    return {
        **{key: value for key, value in stock.items() if key not in {"bars"}},
        "strength_relation": relation,
        "priority": priority,
        "selection_status": "条件已触发" if satisfied else ("接近触发" if close_to else "等待确认"),
        "selection_reasons": reason,
        "waiting_for": waiting[:3],
        "satisfied_condition_count": len(satisfied),
    }


def build_market(raw: dict[str, Any], sectors: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    market_cfg = config["market"]
    index_average = mean(item["change_pct"] for item in raw["indices"])
    labels = []
    if index_average >= market_cfg["strong_index_average_min"]:
        labels.append("偏强")
    elif index_average <= market_cfg["weak_index_average_max"]:
        labels.append("偏弱")
    else:
        labels.append("中性")
    if raw["market_amplitude"] >= market_cfg["high_volatility_amplitude_min"]:
        labels.append("高波动")
    if raw["turnover_change"] <= market_cfg["shrinking_turnover_change_max"]:
        labels.append("缩量")
    elif raw["turnover_change"] >= market_cfg["expanding_turnover_change_min"]:
        labels.append("放量")
    return {
        **raw,
        "status_labels": labels,
        "advance_ratio": rounded(raw["advance_count"] / (raw["advance_count"] + raw["decline_count"])),
        "core_sectors": sectors[:3],
        "data_source": {"id": "mock", "label": "Mock"},
        "missing_data": [],
    }


def attention_score(stock: dict[str, Any], config: dict[str, Any]) -> int:
    priority = config["attention_priority"]
    if "趋势破坏" in stock["labels"]:
        return priority["trend_break"]
    if "放量异常" in stock["labels"]:
        return priority["abnormal_volume"]
    if "弱于板块" in stock["labels"]:
        return priority["weaker_than_sector"]
    if "过热" in stock["labels"]:
        return priority["overheat"]
    return priority["normal"]


def build_holdings(
    stocks: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    intraday_raw: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    by_code = {stock["code"]: stock for stock in stocks}
    intraday_by_code = {item["code"]: item for item in intraday_raw}
    rows, live_rows, review_rows = [], [], []
    for position in positions:
        stock = by_code[position["code"]]
        score = attention_score(stock, config)
        market_value = stock["close"] * position["shares"]
        position_return = (stock["close"] - position["cost"]) / position["cost"]
        observation_levels = [
            {"label": "第一支撑", "value": stock["recent_support"]},
            {"label": "MA20", "value": stock["ma20"]},
            {"label": "昨日低点", "value": stock["low"]},
            {"label": "前高", "value": stock["previous_high"]},
            {"label": "压力位", "value": stock["previous_high"]},
        ]
        plans = []
        if abs(stock["ma20_distance"]) <= config["ma20_near_distance_abs"]:
            plans.append("若回踩 MA20 附近且成交量继续缩小，可继续观察承接。")
        if stock["ma20_distance"] < 0:
            plans.append("若量能放大且所属板块同步转弱，风险状态将进一步上升。")
        plans.append(f"若放量越过近期前高 {stock['previous_high']:.2f}，同时板块保持强势，可确认趋势是否延续。")
        row = {
            **{key: value for key, value in stock.items() if key not in {"bars", "sector", "conditions"}},
            "shares": position["shares"],
            "cost": position["cost"],
            "market_value": round(market_value, 2),
            "position_return": rounded(position_return),
            "yesterday_volume_state": next(label for label in stock["labels"] if label in {"缩量", "正常量能", "放量异常"}),
            "observation_levels": observation_levels,
            "conditional_plan": plans,
            "attention_score": score,
            "attention_level": "需要关注" if score >= config["attention_priority"]["weaker_than_sector"] else "正常",
        }
        rows.append(row)

        live = intraday_by_code[position["code"]]
        current_change = (live["current_price"] - stock["previous_close"]) / stock["previous_close"]
        amplitude = (live["high"] - live["low"]) / stock["previous_close"]
        relative = current_change - live["sector_return"]
        live_distance = (live["current_price"] - stock["ma20"]) / stock["ma20"]
        intraday_cfg = config["intraday"]
        rush_fade = (live["high"] - live["current_price"]) / live["high"] >= intraday_cfg["rush_fade_from_high_min"]
        bottom_rebound = (live["current_price"] - live["low"]) / live["low"] >= intraday_cfg["bottom_rebound_from_low_min"]
        volume_expansion = live["realtime_volume_ratio"] >= intraday_cfg["volume_expansion_ratio_min"]
        key_level_break = live["current_price"] < min(stock["ma20"], stock["recent_support"])
        volume_breakout = volume_expansion and live["current_price"] > stock["previous_high"]
        volume_stall = volume_expansion and current_change <= 0.005
        shrink_pullback = live["realtime_volume_ratio"] <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"] and abs(live_distance) <= config["ma20_near_distance_abs"]
        statuses = []
        if relative >= config["industry_strength_threshold"]:
            statuses.append("强于板块")
        elif relative <= -config["industry_strength_threshold"]:
            statuses.append("弱于板块")
        if live_distance < 0:
            statuses.append("跌破 MA20")
        if shrink_pullback:
            statuses.append("缩量回踩")
        if volume_breakout:
            statuses.append("放量突破")
        elif volume_stall:
            statuses.append("放量滞涨")
        elif volume_expansion:
            statuses.append("放量")
        if rush_fade:
            statuses.append("冲高回落")
        if bottom_rebound:
            statuses.append("探底回升")
        if amplitude >= intraday_cfg["high_volatility_amplitude_min"]:
            statuses.append("高波动")
        if abs((live["current_price"] - stock["recent_support"]) / stock["recent_support"]) <= intraday_cfg["near_level_distance_abs"]:
            statuses.append("接近支撑")
        if abs((live["current_price"] - stock["previous_high"]) / stock["previous_high"]) <= intraday_cfg["near_level_distance_abs"]:
            statuses.append("接近压力")
        if not statuses:
            statuses.append("正常")
        if "跌破 MA20" in statuses and volume_expansion:
            suggestion = "当前弱于所属板块，并出现放量跌破 MA20，风险状态较早盘上升。"
        elif "冲高回落" in statuses:
            suggestion = "当前从日内高点回落，结合量能与板块同步性继续观察结构变化。"
        elif live_distance >= 0:
            suggestion = "当前仍位于 MA20 上方，暂未出现结构性变化。"
        else:
            suggestion = "当前位于 MA20 下方，等待价格与量能重新确认结构。"
        live_rows.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                **live,
                "change_pct": rounded(current_change),
                "amplitude": rounded(amplitude),
                "ma20": stock["ma20"],
                "ma20_distance": rounded(live_distance),
                "relative_sector_strength": rounded(relative),
                "statuses": statuses,
                "flags": {
                    "rush_fade": rush_fade,
                    "bottom_rebound": bottom_rebound,
                    "volume_expansion": volume_expansion,
                    "key_level_break": key_level_break,
                },
                "conditional_note": suggestion,
                "attention_score": score + (20 if "跌破 MA20" in statuses else 0),
            }
        )

        previous_closes = [bar["close"] for bar in stock["bars"][:-1]]
        previous_ma20 = moving_average(previous_closes, 20)
        previous_distance = (stock["previous_close"] - previous_ma20) / previous_ma20
        changes = []
        if previous_distance >= 0 > stock["ma20_distance"]:
            changes.append("昨日 MA20 上方 → 今日跌破")
        elif previous_distance < 0 <= stock["ma20_distance"]:
            changes.append("昨日 MA20 下方 → 今日重新站上")
        previous_volume_ratio = stock["bars"][-2]["volume"] / mean(bar["volume"] for bar in stock["bars"][-7:-2])
        if previous_volume_ratio < config["abnormal_volume_ratio_5d_min"] <= stock["volume_ratio_5d"]:
            changes.append("昨日正常量能 → 今日放量")
        if not changes:
            changes.append("MA20 与量能状态相比昨日未发生级别变化")
        review_rows.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                "change_pct": stock["change_pct"],
                "high": stock["high"],
                "low": stock["low"],
                "ma20": stock["ma20"],
                "ma20_distance": stock["ma20_distance"],
                "volume_ratio_5d": stock["volume_ratio_5d"],
                "relative_sector_strength": stock["relative_sector_strength"],
                "labels": stock["labels"],
                "what_happened": f"今日涨跌 {stock['change_pct'] * 100:+.2f}%，日内高低 {stock['high']:.2f}/{stock['low']:.2f}；相对板块 {stock['relative_sector_strength'] * 100:+.2f}%，量能为 5 日均量的 {stock['volume_ratio_5d'] * 100:.0f}%。",
                "changes_from_yesterday": changes,
            }
        )

    rows.sort(key=lambda item: item["attention_score"], reverse=True)
    live_rows.sort(key=lambda item: item["attention_score"], reverse=True)
    total_value = sum(item["market_value"] for item in rows)
    portfolio_change = sum(item["change_pct"] * item["market_value"] for item in rows) / total_value
    summary = {
        "trade_date": rows[0]["date"] if rows else date.today().isoformat(),
        "holding_count": len(rows),
        "portfolio_change_pct": rounded(portfolio_change),
        "total_market_value": round(total_value, 2),
        "stronger_than_sector_count": sum("强于板块" in item["labels"] for item in rows),
        "weaker_than_sector_count": sum("弱于板块" in item["labels"] for item in rows),
        "above_ma20_count": sum("MA20上方" in item["labels"] for item in rows),
        "below_ma20_count": sum("MA20下方" in item["labels"] for item in rows),
        "shrink_pullback_count": sum("缩量" in item["labels"] and abs(item["ma20_distance"]) <= config["ma20_near_distance_abs"] for item in rows),
        "abnormal_volume_count": sum("放量异常" in item["labels"] for item in rows),
        "anomaly_count": sum(item["attention_level"] == "需要关注" for item in rows),
    }
    anomalies = [
        {"code": item["code"], "name": item["name"], "industry": item["industry"], "labels": [label for label in item["labels"] if label in {"趋势破坏", "MA20下方", "弱于板块", "放量异常", "过热"}]}
        for item in rows
        if item["attention_level"] == "需要关注"
    ]
    holdings_payload = {"summary": summary, "anomalies": anomalies, "premarket": rows, "holdings": rows, "data_source": "mock"}
    intraday_payload = {"summary": {"trade_date": summary["trade_date"], "holding_count": len(live_rows), "stage": "intraday"}, "holdings": live_rows, "data_source": "mock"}
    review_text = (
        f"今日组合 {portfolio_change * 100:+.2f}%。{len(rows)} 只持仓中，"
        f"{summary['above_ma20_count']} 只位于 MA20 上方，{summary['weaker_than_sector_count']} 只弱于所属板块。"
        f"缩量回踩 {summary['shrink_pullback_count']} 只，放量异常 {summary['abnormal_volume_count']} 只。"
        "整体继续以结构变化和板块同步性为观察重点。"
    )
    review_payload = {"summary": {**summary, "narrative": review_text}, "holdings": review_rows, "data_source": "mock"}
    return holdings_payload, intraday_payload, review_payload


def build_candidates(stocks: list[dict[str, Any]], sectors: list[dict[str, Any]]) -> dict[str, Any]:
    records = [candidate_record(stock) for stock in stocks]
    core = [item for item in records if item["strength_relation"] != "板块弱 + 个股弱"]
    core.sort(key=lambda item: (item["priority"], item["industry_rank"], -item["satisfied_condition_count"]))
    pullback_watch = [item for item in core if any(condition["id"] in {"shrink_pullback", "strong_sector_pullback"} and condition["status"] != "未满足" for condition in item["conditions"])]
    return {
        "summary": {
            "candidate_count": len(core),
            "strong_stock_count": sum(item["strength_relation"] == "板块强 + 个股强" for item in core),
            "pullback_count": len(pullback_watch),
            "waiting_count": sum(item["selection_status"] != "条件已触发" for item in core),
        },
        "strong_sectors": [sector for sector in sectors if sector["is_strong"]],
        "strong_stocks": [item for item in core if item["strength_relation"] in {"板块强 + 个股强", "板块弱 + 个股强"}],
        "pullback_watch": pullback_watch,
        "waiting": [item for item in core if item["selection_status"] != "条件已触发"],
        "all": core,
        "data_source": "mock",
    }


def build_watchlist(raw_stocks: list[dict[str, Any]], stocks: list[dict[str, Any]], watch_items: list[dict[str, Any]]) -> dict[str, Any]:
    raw_by_code = {stock["code"]: stock for stock in raw_stocks}
    by_code = {stock["code"]: stock for stock in stocks}
    records = []
    for item in watch_items:
        stock = by_code[item["code"]]
        bars = [bar for bar in raw_by_code[item["code"]]["bars"] if bar["date"] >= item["added_date"]]
        if not bars:
            bars = raw_by_code[item["code"]]["bars"][-5:]
        returns = [(bar["close"] - item["added_price"]) / item["added_price"] for bar in bars]
        condition_statuses = [condition["status"] for condition in stock["conditions"]]
        overall = "已满足" if "已满足" in condition_statuses else ("接近满足" if "接近满足" in condition_statuses else "未满足")
        records.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                "added_date": item["added_date"],
                "added_price": item["added_price"],
                "current_price": stock["close"],
                "return_since_added": rounded((stock["close"] - item["added_price"]) / item["added_price"]),
                "observed_trading_days": len(bars),
                "max_gain": rounded(max(returns)),
                "max_drawdown": rounded(min(returns)),
                "buy_condition_status": overall,
                "current_triggered_conditions": [condition["name"] for condition in stock["conditions"] if condition["status"] == "已满足"],
                "original_reason": item["triggered_conditions"],
                "ma20_at_added": stock["ma20"],
                "current_ma20_distance": stock["ma20_distance"],
                "current_labels": stock["labels"],
            }
        )
    return {"summary": {"watch_count": len(records), "data_source": "mock"}, "watchlist": records}


def build_today(market: dict[str, Any], holdings: dict[str, Any], candidates: dict[str, Any]) -> dict[str, Any]:
    summary = holdings["summary"]
    attention = [
        f"{summary['below_ma20_count']} 只持仓位于 MA20 下方",
        f"{summary['weaker_than_sector_count']} 只持仓弱于所属板块",
        f"{candidates['summary']['pullback_count']} 只候选进入回踩观察区",
        f"今日放量异常持仓 {summary['abnormal_volume_count']} 只",
    ]
    return {
        "summary": {
            "trade_date": market["trade_date"],
            "candidate_count": candidates["summary"]["candidate_count"],
            "market_status": market["status_labels"],
            "data_source": "mock",
            "missing_data": [],
        },
        "attention": attention,
        "top_candidates": candidates["all"][:5],
        "holding_summary": summary,
        "holding_anomalies": holdings["anomalies"],
    }


def write_json(name: str, payload: dict[str, Any]) -> None:
    (DATA_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    config = load_config()
    provider = MockProvider()
    raw_stocks = provider.get_daily_bars()
    sectors = build_sectors(provider.get_sectors(), config)
    sector_by_name = {sector["name"]: sector for sector in sectors}
    stocks = [enrich_stock(stock, sector_by_name[stock["industry"]], config) for stock in raw_stocks]
    market = build_market(provider.get_market_snapshot(), sectors, config)
    candidates = build_candidates(stocks, sectors)
    holdings, intraday, review = build_holdings(stocks, provider.get_holdings(), provider.get_intraday_snapshots(), config)
    watchlist = build_watchlist(raw_stocks, stocks, provider.get_watchlist())
    today = build_today(market, holdings, candidates)

    DATA_DIR.mkdir(exist_ok=True)
    write_json("market.json", market)
    write_json("sectors.json", {"summary": {"sector_count": len(sectors)}, "sectors": sectors, "data_source": "mock"})
    write_json("candidates.json", candidates)
    write_json("holdings.json", holdings)
    write_json("intraday.json", intraday)
    write_json("review.json", review)
    write_json("watchlist.json", watchlist)
    write_json("today.json", today)
    print("Generated FaCail phase-two JSON from MockProvider")


if __name__ == "__main__":
    main()
