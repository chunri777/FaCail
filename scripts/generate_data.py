from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from providers import ProviderUnavailable, create_provider
from trading_calendar import trade_stage


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "strategy.json"
DATA_DIR = ROOT / "data"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def moving_average(values: list[float], window: int) -> float | None:
    return round(mean(values[-window:]), 4) if len(values) >= window else None


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def has_value(value: Any) -> bool:
    return value is not None


def pct_text(value: float | None) -> str:
    return "数据缺失" if value is None else f"{value * 100:+.2f}%"


def safe_gt(left: float | None, right: float) -> bool:
    return left is not None and left > right


def safe_gte(left: float | None, right: float) -> bool:
    return left is not None and left >= right


def safe_lte(left: float | None, right: float) -> bool:
    return left is not None and left <= right


def safe_between(value: float | None, low: float, high: float) -> bool:
    return value is not None and low <= value <= high


def detect_trade_stage(now=None) -> str:
    current = now or datetime_now()
    minutes = current.hour * 60 + current.minute
    if minutes < 9 * 60 + 30:
        return "premarket"
    if minutes <= 15 * 60:
        return "intraday"
    return "postmarket"


def datetime_now():
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def source_meta(provider, stage: str, missing: list[str] | None = None) -> dict[str, Any]:
    market = getattr(provider, "_market", None) or {}
    all_missing = sorted(set((missing or []) + getattr(provider, "missing_data", [])))
    return {
        "id": provider.id,
        "label": provider.label,
        "stage": stage,
        "generated_at": datetime_now().isoformat(timespec="seconds"),
        "source": market.get("source"),
        "source_timestamp": market.get("source_timestamp"),
        "retrieved_at": market.get("retrieved_at"),
        "freshness": market.get("freshness"),
        "last_trading_day": market.get("last_trading_day"),
        "fallback": getattr(provider, "fallback_count", 0) > 0,
        "missing_data": all_missing,
        "request_stats": provider.stats() if hasattr(provider, "stats") else None,
    }


def sector_status(sector: dict[str, Any], config: dict[str, Any]) -> str:
    rules = config["sector"]
    if sector.get("source") == "eastmoney" or sector.get("source") == "eastmoney_history_fallback":
        if any(sector.get(key) is None for key in ("return_pct", "return_5d", "return_20d", "ma20_above")):
            return "数据缺失"
        if sector["return_20d"] >= rules["overheat_20d_return_min"]:
            return "高位过热"
        if sector["return_5d"] <= rules["weak_5d_return_max"] or not sector["ma20_above"]:
            return "转弱"
        if sector["return_pct"] < 0 and sector["return_5d"] > 0:
            return "回踩"
        if sector["return_5d"] >= rules["strong_5d_return_min"] and sector["ma20_above"]:
            if sector.get("turnover_change") is not None and sector["turnover_change"] >= rules["turnover_expansion_min"]:
                return "持续强势"
            return "强势"
        return "观察"
    if any(sector.get(key) is None for key in ("return_pct", "return_5d", "return_20d", "advance_ratio", "turnover_change", "ma20_above", "rank")):
        return "数据缺失"
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
                "rank_total": total if item.get("rank") is not None else None,
                "rank_percentile": rounded(item["rank"] / total) if item.get("rank") is not None else None,
                "ma20_status": "数据缺失" if item.get("ma20_above") is None else ("MA20上方" if item["ma20_above"] else "MA20下方"),
                "status": status,
                "is_strong": status in {"强势", "持续强势", "刚启动", "高位过热"} or
                (status == "回踩" and item.get("source") == "eastmoney" and item.get("ma20_above") is True),
            }
        )
    return sorted(rows, key=lambda item: item["rank"] if item["rank"] is not None else total + 1)


def condition_state(checks: list[dict[str, Any]]) -> str:
    if any(check["met"] is None for check in checks):
        return "未满足"
    met_count = sum(check["met"] is True for check in checks)
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
    sector_strong = None if sector["status"] == "数据缺失" else sector["is_strong"]
    rank_percentile = sector.get("rank_percentile")
    return_5d = sector.get("return_5d")
    volume_ratio = stock.get("volume_ratio_5d") if stock.get("volume_complete", True) else None

    groups = [
        {
            "id": "shrink_pullback",
            "name": "条件 A · 缩量回踩",
            "checks": [
                {"label": "价格位于 MA20 观察区", "met": safe_between(stock["ma20_distance"], pullback["ma20_distance_min"], pullback["ma20_distance_max"])},
                {"label": "日涨跌位于回踩区间", "met": safe_between(stock["change_pct"], pullback["daily_change_min"], pullback["daily_change_max"])},
                {"label": "量能缩至 5 日均量阈值内", "met": None if volume_ratio is None else safe_lte(volume_ratio, pullback["volume_ratio_5d_max"])},
                {"label": "所属板块处于强势区", "met": sector_strong},
                {"label": "未明显跌破近期支撑", "met": stock["close"] >= stock["recent_support"] * (1 + pullback["key_low_break_tolerance"])},
            ],
        },
        {
            "id": "volume_breakout",
            "name": "条件 B · 放量突破",
            "checks": [
                {"label": "突破近 20 日关键高点", "met": stock["close"] > stock["previous_high"] * (1 + breakout["breakout_tolerance"])},
                {"label": "成交量达到 5 日均量阈值", "met": None if volume_ratio is None else safe_gte(volume_ratio, breakout["volume_ratio_5d_min"])},
                {"label": "所属板块处于强势区", "met": sector_strong},
                {"label": "个股强于所属板块", "met": None if stock["relative_sector_strength"] is None else safe_gt(stock["relative_sector_strength"], 0)},
                {"label": "距离 MA20 未超过限制", "met": safe_lte(stock["ma20_distance"], breakout["ma20_distance_max"])},
            ],
        },
        {
            "id": "strong_sector_pullback",
            "name": "条件 C · 强势板块回踩",
            "checks": [
                {"label": "板块排名进入市场前列" if rank_percentile is not None else "所属板块保持强势",
                 "met": safe_lte(rank_percentile, sector_pullback["sector_rank_percentile_max"]) if rank_percentile is not None else sector_strong},
                {"label": "板块 5 日涨幅为正", "met": None if return_5d is None else safe_gte(return_5d, sector_pullback["sector_5d_return_min"])},
                {"label": "股价接近 MA10 或 MA20", "met": near_ma <= sector_pullback["ma_distance_abs_max"]},
                {"label": "量能处于收缩状态", "met": None if volume_ratio is None else safe_lte(volume_ratio, sector_pullback["volume_ratio_5d_max"])},
                {"label": "未出现趋势破坏", "met": safe_gt(stock["ma20_distance"], config["trend_break_ma20_distance"])},
            ],
        },
    ]
    for group in groups:
        group["status"] = condition_state(group["checks"])
        group["met_count"] = sum(check["met"] is True for check in group["checks"])
        group["total_count"] = len(group["checks"])
        group["missing_count"] = sum(check["met"] is None for check in group["checks"])
    return groups


def enrich_stock(raw: dict[str, Any], sector: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    bars = [dict(bar) for bar in raw["bars"]]
    quote = raw.get("quote") or {}
    quote_date = (quote.get("source_timestamp") or "")[:10]
    if quote_date >= bars[-1]["date"] and all(quote.get(key) is not None for key in ("current_price", "open", "high", "low", "volume")):
        live_bar = {"date": quote_date, "open": quote["open"], "high": quote["high"],
                    "low": quote["low"], "close": quote["current_price"], "volume": quote["volume"]}
        if quote_date == bars[-1]["date"]:
            bars[-1] = live_bar
        else:
            bars.append(live_bar)
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    latest, previous = bars[-1], bars[-2]
    ma5, ma10, ma20 = (moving_average(closes, window) for window in (5, 10, 20))
    volume_ma5 = round(mean(volumes[-6:-1]), 2)
    volume_ratio = latest["volume"] / volume_ma5 if volume_ma5 else None
    change = (latest["close"] - previous["close"]) / previous["close"]
    ma20_distance = (latest["close"] - ma20) / ma20
    recent_gain = (latest["close"] - closes[-21]) / closes[-21]
    relative_strength = change - sector["return_pct"] if sector.get("return_pct") is not None else None
    previous_high = max(bar["high"] for bar in bars[-21:-1])
    recent_support = min(bar["low"] for bar in bars[-10:-1])
    recent_high_20d = max(bar["high"] for bar in bars[-20:])
    recent_low_20d = min(bar["low"] for bar in bars[-20:])
    previous_ma20 = moving_average(closes[:-1], 20)
    crossed_below_ma20 = previous_ma20 is not None and previous["close"] >= previous_ma20 and latest["close"] < ma20

    volume_complete = raw.get("trade_stage") != "intraday"
    labels = ["MA20上方" if ma20_distance >= 0 else "MA20下方"]
    if crossed_below_ma20:
        labels.append("跌破 MA20")
    if ma20_distance <= config["trend_break_ma20_distance"]:
        labels.append("趋势破坏")
    if ma20_distance >= config["high_ma20_distance_min"]:
        labels.append("高位偏离")
    if recent_support and abs(latest["close"] / recent_support - 1) <= config["intraday"]["near_level_distance_abs"]:
        labels.append("接近支撑")
    if volume_complete and volume_ratio is not None:
        if volume_ratio <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"]:
            labels.append("缩量")
        elif volume_ratio >= config["abnormal_volume_ratio_5d_min"]:
            labels.append("放量异常")
        else:
            labels.append("正常量能")
    if relative_strength is not None and relative_strength >= config["industry_strength_threshold"]:
        labels.append("强于板块")
    elif relative_strength is not None and relative_strength <= -config["industry_strength_threshold"]:
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
        "volume_complete": volume_complete,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma20_distance": rounded(ma20_distance),
        "recent_gain_20d": rounded(recent_gain),
        "previous_high": previous_high,
        "recent_support": recent_support,
        "recent_high_20d": recent_high_20d,
        "recent_low_20d": recent_low_20d,
        "industry": raw["industry"],
        "industry_return": sector.get("return_pct"),
        "industry_rank": sector.get("rank"),
        "limit_up_count": sector.get("limit_up_count"),
        "advance_ratio": sector.get("advance_ratio"),
        "relative_sector_strength": rounded(relative_strength),
        "turnover_rate": quote.get("turnover_rate"),
        "pe": quote.get("pe"), "pb": quote.get("pb"), "market_cap": quote.get("market_cap"),
        "concepts": raw.get("concepts", []),
        "source": quote.get("source") or raw.get("source"),
        "sources": raw.get("data_source", {}),
        "source_timestamp": quote.get("source_timestamp") or raw.get("source_timestamp"),
        "retrieved_at": quote.get("retrieved_at") or raw.get("retrieved_at"),
        "freshness": raw.get("freshness"),
        "missing_data": raw.get("missing_data", []),
        "sector": sector,
        "labels": labels,
        "bars": bars,
    }
    stock["conditions"] = build_conditions(stock, config)
    return stock


def candidate_record(stock: dict[str, Any]) -> dict[str, Any]:
    sector_strong = stock["sector"]["is_strong"]
    stock_strong = safe_gt(stock["relative_sector_strength"], 0) and safe_gte(stock["ma20_distance"], 0)
    if sector_strong and stock_strong:
        relation = "板块强 + 个股强"
        priority = 1
    elif sector_strong:
        relation = "板块强 + 个股弱"
        priority = 2
    elif stock_strong and stock["sector"]["status"] == "数据缺失":
        relation = "板块数据不足 + 个股强"
        priority = 3
    elif stock_strong:
        relation = "板块弱 + 个股强"
        priority = 3
    else:
        relation = "板块数据不足" if stock["sector"]["status"] == "数据缺失" else "板块弱 + 个股弱"
        priority = 4

    satisfied = [item for item in stock["conditions"] if item["status"] == "已满足"]
    close_to = [item for item in stock["conditions"] if item["status"] == "接近满足"]
    reason = [f"所属{stock['industry'] or '未知'}板块今日涨跌 {pct_text(stock['sector'].get('return_pct'))}。"]
    if stock["industry_rank"] is not None:
        reason.append(f"板块今日排名 {stock['industry_rank']}/{stock['sector']['rank_total']}。")
    if stock["sector"].get("return_5d") is not None:
        reason.append(f"板块 5 日涨跌 {pct_text(stock['sector']['return_5d'])}，状态为{stock['sector']['status']}。")
    reason.append(f"个股今日涨跌 {pct_text(stock['change_pct'])}，位于 MA20 {'上方' if stock['ma20_distance'] >= 0 else '下方'} {pct_text(stock['ma20_distance'])}。")
    if stock["volume_ratio_5d"] is not None:
        reason.append(f"当日累计成交量为前 5 个交易日均量的 {stock['volume_ratio_5d'] * 100:.0f}%{'（盘中未完成）' if not stock.get('volume_complete', True) else ''}。")
    waiting = []
    cfg_checks = {
        "价格位于 MA20 观察区": f"等待价格回到 MA20 附近，当前距离 {pct_text(stock['ma20_distance'])}",
        "量能缩至 5 日均量阈值内": f"等待量能确认，当前为 5 日均量的 {stock['volume_ratio_5d'] * 100:.0f}%" if stock["volume_ratio_5d"] is not None else "等待完整成交量数据",
        "所属板块处于强势区": f"等待{stock['industry'] or '所属'}板块进入强势区",
        "所属板块保持强势": f"等待{stock['industry'] or '所属'}板块维持强势",
        "突破近 20 日关键高点": f"等待价格突破 {stock['previous_high']:.2f}",
    }
    for condition in stock["conditions"]:
        for check in condition["checks"]:
            if check["met"] is not True and check["label"] in cfg_checks and cfg_checks[check["label"]] not in waiting:
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


def build_market(
    raw: dict[str, Any],
    sectors: list[dict[str, Any]],
    config: dict[str, Any],
    provider,
    stage: str,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    market_cfg = config["market"]
    index_changes = [item["change_pct"] for item in raw["indices"] if item.get("change_pct") is not None]
    index_average = mean(index_changes) if index_changes else None
    labels = []
    if index_average is None:
        labels.append("数据缺失")
    elif index_average >= market_cfg["strong_index_average_min"]:
        labels.append("偏强")
    elif index_average <= market_cfg["weak_index_average_max"]:
        labels.append("偏弱")
    else:
        labels.append("中性")
    if raw.get("market_amplitude") is not None and raw["market_amplitude"] >= market_cfg["high_volatility_amplitude_min"]:
        labels.append("高波动")
    if raw.get("turnover_change") is not None and raw["turnover_change"] <= market_cfg["shrinking_turnover_change_max"]:
        labels.append("缩量")
    elif raw.get("turnover_change") is not None and raw["turnover_change"] >= market_cfg["expanding_turnover_change_min"]:
        labels.append("放量")
    advance_total = raw.get("advance_count") + raw.get("decline_count") if raw.get("advance_count") is not None and raw.get("decline_count") is not None else None
    return {
        **raw,
        "default_stage": stage,
        "status_labels": labels,
        "advance_ratio": rounded(raw["advance_count"] / advance_total) if advance_total else None,
        "core_sectors": sorted(sectors, key=lambda item: item.get("return_5d") if item.get("return_5d") is not None else item.get("return_pct") if item.get("return_pct") is not None else -999, reverse=True)[:3] if provider.id == "astock" else sectors[:3],
        "data_source": source_meta(provider, stage, sorted(set((missing or []) + getattr(provider, "missing_data", [])))),
        "missing_data": sorted(set((missing or []) + getattr(provider, "missing_data", []) + raw.get("missing_data", []))),
    }


def attention_score(stock: dict[str, Any], config: dict[str, Any]) -> int:
    priority = config["attention_priority"]
    if "跌破 MA20" in stock["labels"]:
        return priority["ma20_cross_below"]
    if "趋势破坏" in stock["labels"]:
        return priority["trend_break"]
    if "MA20下方" in stock["labels"]:
        return priority["ma20_below"]
    if "放量异常" in stock["labels"]:
        return priority["abnormal_volume"]
    if "弱于板块" in stock["labels"]:
        return priority["weaker_than_sector"]
    if "高位偏离" in stock["labels"] or "过热" in stock["labels"]:
        return priority["high_ma20_deviation"]
    if stock.get("recent_support") and abs(stock["close"] / stock["recent_support"] - 1) <= config["intraday"]["near_level_distance_abs"]:
        return priority["near_support"]
    return priority["normal"]


def holding_observation_levels(stock: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "第一支撑", "value": stock["recent_support"]},
        {"label": "MA20", "value": stock["ma20"]},
        {"label": "昨日低点", "value": stock["low"]},
        {"label": "昨日高点", "value": stock["high"]},
        {"label": "近期前高", "value": stock["previous_high"]},
        {"label": "20日高点", "value": stock["recent_high_20d"]},
        {"label": "20日低点", "value": stock["recent_low_20d"]},
    ]


def holding_volume_state(ratio: float | None, config: dict[str, Any]) -> str:
    if ratio is None:
        return "数据缺失"
    if ratio <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"]:
        return "缩量"
    if ratio >= config["abnormal_volume_ratio_5d_min"]:
        return "放量异常"
    return "正常量能"


def build_holdings(
    stocks: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    intraday_raw: list[dict[str, Any]],
    config: dict[str, Any],
    provider,
    stage: str,
    missing: list[str] | None = None,
    premarket_stocks: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    by_code = {stock["code"]: stock for stock in stocks}
    premarket_by_code = {stock["code"]: stock for stock in (stocks if premarket_stocks is None else premarket_stocks)}
    intraday_by_code = {item["code"]: item for item in intraday_raw}
    rows, premarket_rows, live_rows, review_rows = [], [], [], []
    for position in positions:
        if position.get("code") not in by_code:
            if provider.id == "astock":
                (missing if missing is not None else provider.missing_data).append(
                    f"{position.get('code', 'unknown')}.holding: verified daily history unavailable")
                continue
            raise ProviderUnavailable("Holding lacks verified daily history.", [position.get("code", "unknown")])
        if provider.id == "astock" and position["code"] not in premarket_by_code:
            (missing if missing is not None else provider.missing_data).append(
                f"{position['code']}.holding: premarket history unavailable")
            continue
        stock = by_code[position["code"]]
        plan_stock = premarket_by_code.get(position["code"], stock)
        if position.get("shares") is None or position.get("cost") is None:
            raise ProviderUnavailable("Holding requires shares and cost.", [position["code"]])
        score = attention_score(stock, config)
        market_value = stock["close"] * position["shares"]
        cost_basis = position["cost"] * position["shares"]
        unrealized_pnl = market_value - cost_basis
        position_return = unrealized_pnl / cost_basis if cost_basis else None
        plans = []
        if abs(plan_stock["ma20_distance"]) <= config["ma20_near_distance_abs"]:
            plans.append("若回踩 MA20 附近且量能继续收缩，维持承接观察。")
        if plan_stock["ma20_distance"] < 0:
            plans.append("若放量跌破 MA20 且所属行业同步转弱，风险状态上升。")
        if plan_stock["ma20_distance"] >= config["high_ma20_distance_min"]:
            plans.append("若距离 MA20 持续过远，保持高位偏离观察。")
        plans.append(f"若放量越过近期前高 {plan_stock['previous_high']:.2f} 且强于所属行业，观察趋势状态是否增强。")
        row = {
            **{key: value for key, value in stock.items() if key not in {"bars", "sector", "conditions"}},
            "shares": position["shares"],
            "cost": position["cost"],
            "current_price": stock["close"],
            "market_value": round(market_value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "position_return": rounded(position_return),
            "volume_state": holding_volume_state(stock["volume_ratio_5d"] if stock.get("volume_complete", True) else None, config),
            "yesterday_volume_state": next((label for label in stock["labels"] if label in {"缩量", "正常量能", "放量异常"}), "暂无数据"),
            "observation_levels": holding_observation_levels(stock),
            "conditional_plan": plans,
            "attention_score": score,
            "attention_level": "需要关注" if score > config["attention_priority"]["normal"] else "正常",
        }
        rows.append(row)
        plan_score = attention_score(plan_stock, config)
        premarket_rows.append({
            **row,
            **{key: value for key, value in plan_stock.items() if key not in {"bars", "sector", "conditions"}},
            "market_value": round(plan_stock["close"] * position["shares"], 2),
            "current_price": plan_stock["close"],
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(plan_stock["close"] * position["shares"] - cost_basis, 2),
            "position_return": rounded((plan_stock["close"] - position["cost"]) / position["cost"]) if position["cost"] else None,
            "volume_state": holding_volume_state(plan_stock["volume_ratio_5d"], config),
            "yesterday_volume_state": next((label for label in plan_stock["labels"] if label in {"缩量", "正常量能", "放量异常"}), "暂无数据"),
            "observation_levels": holding_observation_levels(plan_stock),
            "attention_score": plan_score,
            "attention_level": "需要关注" if plan_score > config["attention_priority"]["normal"] else "正常",
        })

        live = intraday_by_code.get(position["code"], {"code": position["code"], "current_price": None,
                                                      "high": None, "low": None, "volume": None,
                                                      "realtime_volume_ratio": None, "vwap": None,
                                                      "sector_return": None})
        current_price, high, low = live.get("current_price"), live.get("high"), live.get("low")
        current_change = (current_price - stock["previous_close"]) / stock["previous_close"] if current_price is not None else None
        amplitude = (high - low) / stock["previous_close"] if high is not None and low is not None else None
        relative = current_change - live["sector_return"] if current_change is not None and live.get("sector_return") is not None else None
        live_distance = (current_price - stock["ma20"]) / stock["ma20"] if current_price is not None else None
        intraday_cfg = config["intraday"]
        rush_fade = (high - current_price) / high >= intraday_cfg["rush_fade_from_high_min"] if high and current_price is not None else None
        bottom_rebound = (current_price - low) / low >= intraday_cfg["bottom_rebound_from_low_min"] if low and current_price is not None else None
        live_volume_ratio = live.get("realtime_volume_ratio")
        if live_volume_ratio is None and stock.get("volume_complete"):
            live_volume_ratio = stock["volume_ratio_5d"]
        volume_expansion = live_volume_ratio is not None and live_volume_ratio >= intraday_cfg["volume_expansion_ratio_min"]
        key_level_break = current_price < min(stock["ma20"], stock["recent_support"]) if current_price is not None else None
        volume_breakout = volume_expansion and current_price is not None and current_price > stock["previous_high"]
        volume_stall = volume_expansion and current_change is not None and current_change <= intraday_cfg["volume_stall_change_max"]
        shrink_pullback = live_volume_ratio is not None and live_distance is not None and live_volume_ratio <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"] and abs(live_distance) <= config["ma20_near_distance_abs"]
        statuses = []
        if live_distance is not None:
            statuses.append("MA20上方" if live_distance >= 0 else "MA20下方")
            if plan_stock["ma20_distance"] >= 0 > live_distance:
                statuses.append("跌破 MA20")
            if live_distance <= config["trend_break_ma20_distance"]:
                statuses.append("趋势破坏")
            if live_distance >= config["high_ma20_distance_min"]:
                statuses.append("高位偏离")
        if relative is not None and relative >= config["industry_strength_threshold"]:
            statuses.append("强于板块")
        elif relative is not None and relative <= -config["industry_strength_threshold"]:
            statuses.append("弱于板块")
        if shrink_pullback:
            statuses.append("缩量回踩")
        if live_volume_ratio is not None and live_volume_ratio <= config["buy_conditions"]["shrink_pullback"]["volume_ratio_5d_max"]:
            statuses.append("缩量")
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
        if amplitude is not None and amplitude >= intraday_cfg["high_volatility_amplitude_min"]:
            statuses.append("高波动")
        if current_price is not None and abs((current_price - stock["recent_support"]) / stock["recent_support"]) <= intraday_cfg["near_level_distance_abs"]:
            statuses.append("接近支撑")
        if current_price is not None and abs((current_price - stock["previous_high"]) / stock["previous_high"]) <= intraday_cfg["near_level_distance_abs"]:
            statuses.append("接近压力")
        if not statuses:
            statuses.append("数据缺失")
        elif statuses == ["MA20上方"]:
            statuses.append("正常")
        if current_price is not None:
            live_market_value = current_price * position["shares"]
            live_pnl = live_market_value - cost_basis
        else:
            live_market_value = live_pnl = None
        if current_price is None:
            suggestion = "当前报价暂无数据，暂不能判断盘中结构。"
        elif stage == "premarket":
            if live_distance is not None and live_distance < 0:
                suggestion = "盘前仅有上一交易日行情；若开盘后继续位于 MA20 下方且行业同步转弱，观察风险状态是否上升。"
            elif live_distance is not None and live_distance >= config["high_ma20_distance_min"]:
                suggestion = "盘前仅有上一交易日行情；若盘中继续远离 MA20，保持高位偏离观察。"
            else:
                suggestion = "盘前仅有上一交易日行情；若盘中回踩 MA20 且量能收缩，等待承接确认。"
        elif live.get("freshness") == "stale":
            suggestion = "实时行情暂未更新，以下状态仅供核对历史数据。"
        elif "跌破 MA20" in statuses and "弱于板块" in statuses:
            suggestion = "当前弱于所属行业且跌破 MA20，风险状态较早盘上升，继续观察量能确认。"
        elif "跌破 MA20" in statuses and volume_expansion:
            suggestion = "当前放量跌破 MA20，风险状态较早盘上升，继续观察行业是否同步转弱。"
        elif "缩量回踩" in statuses:
            suggestion = "当前回踩 MA20 且量能收缩，暂处观察区，等待结构确认。"
        elif "高位偏离" in statuses:
            suggestion = "当前距离 MA20 较远，保持高位偏离观察，不以单日上涨推断后续走势。"
        elif "冲高回落" in statuses:
            suggestion = "当前从日内高点回落，结合量能与板块同步性继续观察结构变化。"
        elif "弱于板块" in statuses and live_distance is not None and live_distance < 0:
            suggestion = "当前弱于所属行业且位于 MA20 下方，若继续放量跌破观察位，风险状态上升。"
        elif live_distance is not None and live_distance >= 0:
            suggestion = "当前仍位于 MA20 上方，量能状态以已取得的数据为准，暂未出现结构性变化。"
        else:
            suggestion = "当前位于 MA20 下方，等待价格与量能重新确认结构。"
        live_rows.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                **live,
                "shares": position["shares"],
                "cost": position["cost"],
                "cost_basis": round(cost_basis, 2),
                "market_value": round(live_market_value, 2) if live_market_value is not None else None,
                "unrealized_pnl": round(live_pnl, 2) if live_pnl is not None else None,
                "position_return": rounded(live_pnl / cost_basis) if live_pnl is not None and cost_basis else None,
                "change_pct": rounded(current_change),
                "amplitude": rounded(amplitude),
                "ma20": stock["ma20"],
                "ma5": stock["ma5"],
                "ma10": stock["ma10"],
                "ma20_distance": rounded(live_distance),
                "volume_ma5": stock["volume_ma5"],
                "volume_ratio_5d": stock["volume_ratio_5d"],
                "volume_complete": stock.get("volume_complete", True),
                "volume_state": holding_volume_state(live_volume_ratio, config) if live_volume_ratio is not None else "数据缺失",
                "concepts": stock.get("concepts", []),
                "sources": stock.get("sources", {}),
                "relative_sector_strength": rounded(relative),
                "statuses": statuses,
                "flags": {
                    "rush_fade": rush_fade,
                    "bottom_rebound": bottom_rebound,
                    "volume_expansion": volume_expansion,
                    "key_level_break": key_level_break,
                },
                "conditional_note": suggestion,
                "attention_score": score,
                "missing_data": sorted(set(stock.get("missing_data", []) + live.get("missing_data", []) +
                                           (["volume_ratio_5d: intraday partial"] if not stock.get("volume_complete", True) else []))),
            }
        )

        previous_closes = [bar["close"] for bar in stock["bars"][:-1]]
        previous_ma20 = moving_average(previous_closes, 20)
        previous_distance = (stock["previous_close"] - previous_ma20) / previous_ma20
        changes = [
            f"收盘由 {stock['previous_close']:.2f} 变为 {stock['close']:.2f}，涨跌 {pct_text(stock['change_pct'])}",
            f"距 MA20 由 {pct_text(previous_distance)} 变为 {pct_text(stock['ma20_distance'])}",
        ]
        if previous_distance >= 0 > stock["ma20_distance"]:
            changes.append("昨日 MA20 上方 → 今日跌破")
        elif previous_distance < 0 <= stock["ma20_distance"]:
            changes.append("昨日 MA20 下方 → 今日重新站上")
        previous_volume_average = mean(bar["volume"] for bar in stock["bars"][-7:-2])
        previous_volume_ratio = stock["bars"][-2]["volume"] / previous_volume_average if previous_volume_average else None
        current_volume_ratio = stock["volume_ratio_5d"] if stock.get("volume_complete", True) else None
        if previous_volume_ratio is not None and current_volume_ratio is not None:
            changes.append(f"量能比由 {previous_volume_ratio:.2f} 倍变为 {current_volume_ratio:.2f} 倍")
            previous_volume_state = holding_volume_state(previous_volume_ratio, config)
            current_volume_state = holding_volume_state(current_volume_ratio, config)
            if previous_volume_state != current_volume_state:
                changes.append(f"昨日{previous_volume_state} → 今日{current_volume_state}")
        if previous_distance >= config["high_ma20_distance_min"] and stock["ma20_distance"] < previous_distance:
            changes.append("昨日高位偏离 MA20 → 今日偏离收窄，进入回踩观察")
        previous_sector_return = stock["sector"].get("previous_return_pct")
        if previous_sector_return is not None and len(stock["bars"]) >= 3 and stock["relative_sector_strength"] is not None:
            previous_stock_return = (stock["bars"][-2]["close"] - stock["bars"][-3]["close"]) / stock["bars"][-3]["close"]
            previous_relative = previous_stock_return - previous_sector_return
            threshold = config["industry_strength_threshold"]
            if previous_relative >= threshold and stock["relative_sector_strength"] <= -threshold:
                changes.append("上一交易日强于板块 → 最新交易日弱于板块")
            elif previous_relative <= -threshold and stock["relative_sector_strength"] >= threshold:
                changes.append("上一交易日弱于板块 → 最新交易日强于板块")
        day_structure = "未触发特定日内结构规则"
        if stock["high"] and (stock["high"] - stock["close"]) / stock["high"] >= config["intraday"]["rush_fade_from_high_min"]:
            day_structure = "冲高回落"
        elif stock["low"] and (stock["close"] - stock["low"]) / stock["low"] >= config["intraday"]["bottom_rebound_from_low_min"]:
            day_structure = "探底回升"
        volume_description = (
            f"{holding_volume_state(current_volume_ratio, config)}，为前 5 日均量的 {current_volume_ratio * 100:.0f}%"
            if current_volume_ratio is not None else "量能状态暂无完整数据"
        )
        what_happened = (
            f"最近交易日涨跌 {pct_text(stock['change_pct'])}，高低 {stock['high']:.2f}/{stock['low']:.2f}，"
            f"振幅 {pct_text(stock['amplitude'])}；位于 MA20 {'上方' if stock['ma20_distance'] >= 0 else '下方'}，"
            f"距 MA20 {pct_text(stock['ma20_distance'])}。行业涨跌 {pct_text(stock['industry_return'])}，"
            f"个股相对行业 {pct_text(stock['relative_sector_strength'])}；{volume_description}，{day_structure}。"
        )
        review_rows.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                "shares": position["shares"],
                "cost": position["cost"],
                "cost_basis": round(cost_basis, 2),
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "position_return": rounded(position_return),
                "change_pct": stock["change_pct"],
                "high": stock["high"],
                "low": stock["low"],
                "amplitude": stock["amplitude"],
                "ma5": stock["ma5"],
                "ma10": stock["ma10"],
                "ma20": stock["ma20"],
                "ma20_distance": stock["ma20_distance"],
                "volume": stock["volume"],
                "volume_ma5": stock["volume_ma5"],
                "volume_ratio_5d": stock["volume_ratio_5d"],
                "volume_state": holding_volume_state(current_volume_ratio, config),
                "turnover_rate": stock["turnover_rate"],
                "industry_return": stock["industry_return"],
                "relative_sector_strength": stock["relative_sector_strength"],
                "concepts": stock.get("concepts", []),
                "labels": stock["labels"],
                "source": stock.get("source"),
                "sources": stock.get("sources", {}),
                "source_timestamp": stock.get("source_timestamp"),
                "retrieved_at": stock.get("retrieved_at"),
                "freshness": stock.get("freshness"),
                "missing_data": stock.get("missing_data", []),
                "what_happened": what_happened,
                "changes_from_yesterday": changes,
            }
        )

    rows.sort(key=lambda item: item["attention_score"], reverse=True)
    premarket_rows.sort(key=lambda item: item["attention_score"], reverse=True)
    live_rows.sort(key=lambda item: item["attention_score"], reverse=True)
    scores_by_code = {item["code"]: item["attention_score"] for item in rows}
    review_rows.sort(key=lambda item: scores_by_code[item["code"]], reverse=True)
    total_value = sum(item["market_value"] for item in rows)
    total_cost_basis = sum(item["cost_basis"] for item in rows)
    total_unrealized_pnl = total_value - total_cost_basis
    previous_value = sum(item["previous_close"] * item["shares"] for item in rows)
    portfolio_change = sum((item["close"] - item["previous_close"]) * item["shares"] for item in rows) / previous_value if previous_value else None
    market_date = getattr(provider, "_market", None)
    summary = {
        "trade_date": rows[0]["date"] if rows else (market_date["trade_date"] if market_date else date.today().isoformat()),
        "holding_count": len(rows),
        "portfolio_change_pct": rounded(portfolio_change),
        "total_market_value": round(total_value, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "total_unrealized_pnl_pct": rounded(total_unrealized_pnl / total_cost_basis) if total_cost_basis else None,
        "stronger_than_sector_count": sum("强于板块" in item["labels"] for item in rows),
        "weaker_than_sector_count": sum("弱于板块" in item["labels"] for item in rows),
        "above_ma20_count": sum("MA20上方" in item["labels"] for item in rows),
        "below_ma20_count": sum("MA20下方" in item["labels"] for item in rows),
        "shrink_pullback_count": sum("缩量" in item["labels"] and abs(item["ma20_distance"]) <= config["ma20_near_distance_abs"] for item in rows),
        "abnormal_volume_count": sum("放量异常" in item["labels"] for item in rows),
        "trend_break_count": sum("趋势破坏" in item["labels"] for item in rows),
        "anomaly_count": sum(item["attention_level"] == "需要关注" for item in rows),
    }
    anomalies = [
        {"code": item["code"], "name": item["name"], "industry": item["industry"], "labels": [label for label in item["labels"] if label in {"跌破 MA20", "趋势破坏", "MA20下方", "弱于板块", "放量异常", "高位偏离", "接近支撑", "过热"}]}
        for item in rows
        if item["attention_level"] == "需要关注"
    ]
    data_source = source_meta(provider, stage, missing)
    holdings_payload = {"summary": summary, "anomalies": anomalies, "premarket": premarket_rows, "holdings": rows, "data_source": data_source}
    intraday_payload = {"summary": {"trade_date": summary["trade_date"], "holding_count": len(live_rows), "stage": stage}, "holdings": live_rows, "data_source": data_source}
    review_text = (
        f"最近交易日组合 {portfolio_change * 100:+.2f}%，当前市值 {total_value:,.2f} 元，"
        f"相对持仓成本浮动盈亏 {total_unrealized_pnl:+,.2f} 元。{len(rows)} 只持仓中，"
        f"{summary['above_ma20_count']} 只位于 MA20 上方，{summary['weaker_than_sector_count']} 只弱于所属板块。"
        f"缩量回踩 {summary['shrink_pullback_count']} 只，放量异常 {summary['abnormal_volume_count']} 只，"
        f"趋势破坏 {summary['trend_break_count']} 只。"
        "整体继续以结构变化和板块同步性为观察重点。"
    ) if portfolio_change is not None else "未配置真实持仓，暂无组合复盘。"
    review_payload = {"summary": {**summary, "narrative": review_text}, "holdings": review_rows, "data_source": data_source}
    return holdings_payload, intraday_payload, review_payload


def build_candidates(stocks: list[dict[str, Any]], sectors: list[dict[str, Any]], provider, stage: str, missing: list[str] | None = None) -> dict[str, Any]:
    records = [candidate_record(stock) for stock in stocks]
    core = [item for item in records if item["strength_relation"] not in {"板块弱 + 个股弱", "板块数据不足"}]
    core.sort(key=lambda item: (item["priority"], item["industry_rank"] if item["industry_rank"] is not None else 9999, -item["satisfied_condition_count"]))
    pullback_watch = [item for item in core if any(condition["id"] in {"shrink_pullback", "strong_sector_pullback"} and condition["status"] != "未满足" for condition in item["conditions"])]
    return {
        "summary": {
            "candidate_count": len(core),
            "strong_stock_count": sum(item["strength_relation"] == "板块强 + 个股强" for item in core),
            "pullback_count": len(pullback_watch),
            "waiting_count": sum(item["selection_status"] != "条件已触发" for item in core),
        },
        "strong_sectors": [sector for sector in sectors if sector["is_strong"]],
        "strong_stocks": [item for item in core if item["strength_relation"] in {"板块强 + 个股强", "板块弱 + 个股强", "板块数据不足 + 个股强"}],
        "pullback_watch": pullback_watch,
        "waiting": [item for item in core if item["selection_status"] != "条件已触发"],
        "all": core,
        "data_source": source_meta(provider, stage, missing),
    }


def build_watchlist(
    raw_stocks: list[dict[str, Any]],
    stocks: list[dict[str, Any]],
    watch_items: list[dict[str, Any]],
    provider,
    stage: str,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    raw_by_code = {stock["code"]: stock for stock in raw_stocks}
    by_code = {stock["code"]: stock for stock in stocks}
    records = []
    for item in watch_items:
        if item.get("code") not in by_code:
            continue
        stock = by_code[item["code"]]
        all_bars = raw_by_code[item["code"]]["bars"]
        added_date, added_price = item.get("added_date"), item.get("added_price")
        bars = [bar for bar in all_bars if added_date and bar["date"] >= added_date]
        entry_bars = [bar for bar in all_bars if added_date and bar["date"] <= added_date]
        entry_ma20 = moving_average([bar["close"] for bar in entry_bars], 20)
        gains = [(bar["high"] - added_price) / added_price for bar in bars] if added_price else []
        drawdowns = [(bar["low"] - added_price) / added_price for bar in bars] if added_price else []
        condition_statuses = [condition["status"] for condition in stock["conditions"]]
        overall = "已满足" if "已满足" in condition_statuses else ("接近满足" if "接近满足" in condition_statuses else "未满足")
        records.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                "added_date": added_date,
                "added_price": added_price,
                "current_price": stock["close"],
                "return_since_added": rounded((stock["close"] - added_price) / added_price) if added_price else None,
                "observed_trading_days": len(bars) if added_date else None,
                "max_gain": rounded(max(gains)) if gains else None,
                "max_drawdown": rounded(min(drawdowns)) if drawdowns else None,
                "buy_condition_status": overall,
                "current_triggered_conditions": [condition["name"] for condition in stock["conditions"] if condition["status"] == "已满足"],
                "original_reason": item.get("triggered_conditions", []),
                "ma20_at_added": entry_ma20,
                "current_ma20_distance": stock["ma20_distance"],
                "current_labels": stock["labels"],
                "source": stock.get("source"), "source_timestamp": stock.get("source_timestamp"),
                "retrieved_at": stock.get("retrieved_at"), "freshness": stock.get("freshness"),
                "missing_data": stock.get("missing_data", []),
            }
        )
    return {"summary": {"watch_count": len(records), "data_source": source_meta(provider, stage, missing)}, "watchlist": records}


def build_today(market: dict[str, Any], holdings: dict[str, Any], candidates: dict[str, Any], provider, stage: str, missing: list[str] | None = None) -> dict[str, Any]:
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
            "data_source": source_meta(provider, stage, sorted(set((missing or []) + market.get("missing_data", [])))),
            "missing_data": sorted(set((missing or []) + market.get("missing_data", []))),
        },
        "attention": attention,
        "top_candidates": candidates["all"][:5],
        "holding_summary": summary,
        "holding_anomalies": holdings["anomalies"],
    }


def write_json(name: str, payload: dict[str, Any], output_dir: Path = DATA_DIR) -> None:
    target = output_dir / name
    temporary = output_dir / f".{name}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)


def parse_args() -> Any:
    parser = ArgumentParser(description="Generate FaCail JSON data.")
    parser.add_argument("--provider", choices=["mock", "ths", "astock"], default=os.getenv("FACAIL_PROVIDER", "astock"))
    parser.add_argument("--stage", choices=["auto", "premarket", "intraday", "postmarket"], default=os.getenv("FACAIL_STAGE", "auto"))
    parser.add_argument("--allow-fallback", action="store_true", help="fall back to MockProvider only when THS is unavailable")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR, help="write generated JSON to this directory")
    return parser.parse_args()


def write_ths_status(provider) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if hasattr(provider, "check_access"):
        write_json("ths_status.json", provider.check_access())


def main() -> None:
    args = parse_args()
    config = load_config()
    provider = create_provider(args.provider)
    if provider.id == "astock" and args.allow_fallback:
        raise SystemExit("Mock fallback is forbidden for --provider astock.")
    stage = (trade_stage(datetime_now(), provider.closed_days) if provider.id == "astock" else detect_trade_stage()) if args.stage == "auto" else args.stage
    missing: list[str] = []
    if provider.id == "ths":
        write_ths_status(provider)
    try:
        raw_market = provider.get_market_snapshot()
        raw_stocks = provider.get_daily_bars()
        raw_sectors = provider.get_sectors()
        raw_holdings = provider.get_holdings()
        raw_intraday = provider.get_intraday_snapshots()
        raw_watchlist = provider.get_watchlist()
    except ProviderUnavailable as exc:
        if not args.allow_fallback or provider.id != "ths":
            print(f"{provider.label} provider unavailable: {exc}")
            for item in exc.missing:
                print(f"- {item}")
            raise SystemExit(2) from exc
        missing = [str(exc), *exc.missing]
        provider = create_provider("mock")
        raw_stocks = provider.get_daily_bars()
        raw_sectors = provider.get_sectors()
        raw_market = provider.get_market_snapshot()
        raw_holdings = provider.get_holdings()
        raw_intraday = provider.get_intraday_snapshots()
        raw_watchlist = provider.get_watchlist()
    sectors = build_sectors(raw_sectors, config)
    sector_by_name = {sector["name"]: sector for sector in sectors}
    unknown_sector = {"name": None, "return_pct": None, "return_5d": None, "return_20d": None,
                      "advance_ratio": None, "limit_up_count": None, "turnover_change": None,
                      "turnover_5d_change": None, "ma20_above": None, "rank": None,
                      "rank_total": None, "rank_percentile": None, "status": "数据缺失", "is_strong": False}
    stocks = [enrich_stock(stock, sector_by_name.get(stock["industry"], unknown_sector), config) for stock in raw_stocks]
    premarket_stocks = []
    if provider.id == "astock":
        current_trade_day = datetime_now().date().isoformat()
        for raw in raw_stocks:
            sector = sector_by_name.get(raw["industry"], unknown_sector)
            prior_bars = [bar for bar in raw["bars"] if bar["date"] < current_trade_day]
            if len(prior_bars) < 21:
                missing.append(f"{raw['code']}.premarket_history: insufficient previous-session history")
                continue
            plan_raw = {**raw, "bars": prior_bars, "quote": {}, "trade_stage": "premarket",
                        "source_timestamp": prior_bars[-1]["date"]}
            plan_sector = sector if raw["bars"][-1]["date"] < current_trade_day else {
                **sector, "return_pct": sector.get("previous_return_pct"),
                "status": "数据缺失", "is_strong": False,
            }
            premarket_stocks.append(enrich_stock(plan_raw, plan_sector, config))
    market = build_market(raw_market, sectors, config, provider, stage, missing)
    candidates = build_candidates(stocks, sectors, provider, stage, missing)
    holdings, intraday, review = build_holdings(stocks, raw_holdings, raw_intraday, config, provider, stage, missing,
                                                premarket_stocks=premarket_stocks if provider.id == "astock" else None)
    watchlist = build_watchlist(raw_stocks, stocks, raw_watchlist, provider, stage, missing)
    today = build_today(market, holdings, candidates, provider, stage, missing)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json("market.json", market, args.output_dir)
    write_json("sectors.json", {"summary": {"sector_count": len(sectors)}, "sectors": sectors, "data_source": source_meta(provider, stage, missing)}, args.output_dir)
    write_json("candidates.json", candidates, args.output_dir)
    write_json("holdings.json", holdings, args.output_dir)
    write_json("intraday.json", intraday, args.output_dir)
    write_json("review.json", review, args.output_dir)
    write_json("watchlist.json", watchlist, args.output_dir)
    write_json("today.json", today, args.output_dir)
    print(f"Generated FaCail JSON from {provider.label} provider for {stage}")
    if provider.id == "astock":
        print(json.dumps({"request_stats": provider.stats(), "missing_data": sorted(set(provider.missing_data))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
