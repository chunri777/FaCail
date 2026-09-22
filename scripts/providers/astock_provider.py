from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_calendar import SHANGHAI, daily_freshness, is_trading_day, load_closed_days, quote_freshness, trade_stage

from .astock_http import AstockHttp
from .base_provider import MarketDataProvider, ProviderUnavailable
from .eastmoney_provider import EastmoneyProvider
from .mock_provider import load_local_user_config
from .mootdx_provider import MootdxProvider
from .sina_provider import SinaProvider
from .tencent_provider import TencentProvider

ROOT = Path(__file__).resolve().parents[2]


class AstockProvider(MarketDataProvider):
    """Real, bounded public-data adapter. Missing values never become mock data."""

    id = "astock"
    label = "A股公开数据"

    def __init__(self, codes: list[str] | None = None, config_path: Path | None = None) -> None:
        self.config = json.loads((config_path or ROOT / "config" / "astock_sources.json").read_text(encoding="utf-8"))
        local = load_local_user_config()
        universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))["codes"]
        local_codes = [item["code"] for item in local.get("holdings", []) + local.get("watchlist", []) if item.get("code")]
        self.codes = list(dict.fromkeys(codes if codes is not None else universe + local_codes))
        if any(len(code) != 6 or not code.isdigit() for code in self.codes):
            raise ValueError("Universe and local stock codes must be six digits")
        self.local_config = local
        self.closed_days = load_closed_days()
        self.http = AstockHttp(self.config, ROOT)
        self.mootdx = MootdxProvider(self.config["mootdx_servers"], self.config["timeout_seconds"],
                                    self.http.cache_dir, self.config["cache_seconds"]["daily_bars"],
                                    self.config["mootdx_negative_cache_seconds"])
        self.sina = SinaProvider(self.http, self.config["cache_seconds"]["daily_bars"])
        self.tencent = TencentProvider(self.http, self.config["cache_seconds"]["quote"])
        self.eastmoney = EastmoneyProvider(self.http, self.config["cache_seconds"]["sector"],
                                          self.config["cache_seconds"]["membership"],
                                          self.config["cache_seconds"]["sector_history"])
        self.missing_data: list[str] = []
        self.fallback_count = 0
        self._stocks: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._market: dict[str, Any] | None = None

    def _missing(self, item: str) -> None:
        if item not in self.missing_data:
            self.missing_data.append(item)

    def inspect_stock(self, code: str = "600519", count: int = 60,
                      concept_quote_limit: int | None = None) -> dict[str, Any]:
        if len(code) != 6 or not code.isdigit():
            raise ValueError("Stock code must be six digits")
        limit = self.config["max_concept_quotes_per_stock"] if concept_quote_limit is None else concept_quote_limit
        key = (code, count, limit)
        if key in self._stocks:
            return self._stocks[key]
        missing: list[str] = []
        sources: dict[str, str | None] = {"daily_bars": None, "quote": None, "industry": None, "concepts": None}
        bars: list[dict] = []
        bars_retrieved_at = None
        try:
            bars = self.mootdx.daily_bars(code, count)
            bars_retrieved_at = self.mootdx.last_retrieved_at
            sources["daily_bars"] = "mootdx"
        except (RuntimeError, ValueError) as exc:
            missing.append(f"mootdx: {exc}")
            try:
                bars = self.sina.daily_bars(code, count)
                bars_retrieved_at = self.http.last_result.get("retrieved_at")
                sources["daily_bars"] = "sina_fallback"
                self.fallback_count += 1
            except (RuntimeError, ValueError, KeyError) as fallback_exc:
                missing.append(f"daily_bars: {fallback_exc}")
        try:
            quote = self.tencent.quote(code)
            sources["quote"] = "tencent"
        except (RuntimeError, ValueError, IndexError) as exc:
            quote = {"code": code, "name": None, "current_price": None, "previous_close": None,
                     "open": None, "high": None, "low": None, "volume": None, "change_pct": None,
                     "turnover_rate": None, "market_cap": None, "pe": None, "pb": None,
                     "source_timestamp": None, "retrieved_at": None, "quoted_at": None, "source": None}
            missing.append(f"quote: {exc}")
        for field in ("current_price", "high", "low", "volume", "turnover_rate", "market_cap", "pe", "pb"):
            if quote.get(field) is None:
                missing.append(f"quote.{field}: missing")
        quote["freshness"] = quote_freshness(quote.get("source_timestamp"), datetime.now(SHANGHAI),
                                             self.closed_days, self.config["intraday_stale_after_minutes"],
                                             self.config["postmarket_close_minute"])
        quote["is_stale"] = quote["freshness"] == "stale"
        if quote["is_stale"]:
            missing.append("quote_freshness: stale")
        try:
            membership = self.eastmoney.membership(code)
            sources["industry"] = "eastmoney"
            sources["concepts"] = "eastmoney"
        except (RuntimeError, ValueError, KeyError) as exc:
            membership = {"industry": None, "concepts": [], "retrieved_at": None}
            missing.append(f"membership: {exc}")
        if membership["industry"] is None:
            missing.append("industry: missing")
        sectors = []
        concepts = membership["concepts"][:limit]
        if len(membership["concepts"]) > limit:
            missing.append("concept_quotes: not requested under rate budget")
        boards = ([membership["industry"]] if membership["industry"] else []) + concepts
        for board in boards:
            try:
                sector = self.eastmoney.sector_quote(board)
            except (RuntimeError, ValueError, KeyError) as exc:
                sector = {"code": board["code"], "name": board["name"], "return_pct": None,
                          "source": "eastmoney", "source_timestamp": None, "retrieved_at": None}
                missing.append(f"sector_return:{board['code']}: {exc}")
            history = {}
            if board == membership["industry"]:
                try:
                    history = self.eastmoney.sector_history(board)
                except (RuntimeError, ValueError, KeyError) as exc:
                    missing.append(f"sector_history:{board['code']}: {exc}")
            now = datetime.now(SHANGHAI)
            history_freshness = daily_freshness(history.get("source_timestamp"), now, self.closed_days)
            history_is_current_quote = history_freshness == "current" or (
                history_freshness == "last_trading_day" and
                (not is_trading_day(now.date(), self.closed_days) or trade_stage(now, self.closed_days) == "premarket")
            )
            if sector["return_pct"] is None and history.get("return_pct") is not None and history_is_current_quote:
                sector["return_pct"] = history["return_pct"]
                sector["source"] = "eastmoney_history_fallback"
                sector["source_timestamp"] = history["source_timestamp"]
                sector["retrieved_at"] = history["retrieved_at"]
                self.fallback_count += 1
            sector.update({"return_5d": history.get("return_5d"), "return_20d": history.get("return_20d"),
                           "previous_return_pct": history.get("previous_return_pct"),
                           "ma20_above": history.get("ma20_above"),
                           "turnover_change": history.get("turnover_change"),
                           "turnover_5d_change": history.get("turnover_5d_change"),
                           "history_source_timestamp": history.get("source_timestamp"),
                           "history_retrieved_at": history.get("retrieved_at"),
                           "freshness": "unknown" if sector.get("source_timestamp") is None else
                           (daily_freshness(sector["source_timestamp"], now, self.closed_days)
                            if sector["source"] == "eastmoney_history_fallback" else
                            quote_freshness(sector["source_timestamp"], datetime.now(SHANGHAI), self.closed_days))})
            sectors.append(sector)
        result = {"code": code, "name": quote.get("name"),
                  "trade_date": quote["source_timestamp"][:10] if quote.get("source_timestamp") else
                  (bars[-1]["date"] if bars else None),
                  "bars": bars, "quote": quote, "membership": membership, "sectors": sectors,
                  "sources": sources, "bars_source_timestamp": bars[-1]["date"] if bars else None,
                  "bars_retrieved_at": bars_retrieved_at, "missing_data": missing}
        self._stocks[key] = result
        for item in missing:
            self._missing(f"{code}.{item}")
        return result

    def _published_stock(self, code: str) -> dict[str, Any]:
        return self.inspect_stock(code, concept_quote_limit=self.config["publish_concept_quote_limit"])

    def get_daily_bars(self) -> list[dict[str, Any]]:
        rows = []
        for code in self.codes:
            result = self._published_stock(code)
            if len(result["bars"]) < 21:
                self._missing(f"{code}.daily_bars: insufficient history")
                continue
            industry = result["membership"]["industry"]
            sector = next((item for item in result["sectors"] if industry and item["code"] == industry["code"]), None)
            rows.append({"code": code, "name": result["name"] or code,
                         "industry": industry["name"] if industry else None,
                         "industry_return": sector["return_pct"] if sector else None,
                         "industry_rank": None, "limit_up_count": None, "advance_ratio": None,
                         "bars": result["bars"], "quote": result["quote"],
                         "concepts": result["membership"]["concepts"],
                         "data_source": result["sources"], "source": result["sources"]["daily_bars"],
                         "source_timestamp": result["bars_source_timestamp"],
                         "retrieved_at": result["bars_retrieved_at"],
                         "freshness": result["quote"]["freshness"],
                         "trade_stage": trade_stage(datetime.now(SHANGHAI), self.closed_days),
                         "missing_data": result["missing_data"]})
        return rows

    def get_sectors(self) -> list[dict[str, Any]]:
        sectors = {}
        for code in self.codes:
            result = self._published_stock(code)
            for sector in result["sectors"]:
                missing = [key for key in ("return_pct", "return_5d", "return_20d", "ma20_above",
                                           "turnover_change", "turnover_5d_change") if sector.get(key) is None]
                missing.extend(["advance_ratio", "limit_up_count", "rank"])
                sectors[sector["code"]] = {**sector, "advance_ratio": None, "limit_up_count": None,
                                            "rank": None, "missing_data": missing}
        return list(sectors.values())

    def get_market_snapshot(self) -> dict[str, Any]:
        if self._market is not None:
            return self._market
        try:
            indices = self.tencent.indices(self.config["market_indices"])
        except (RuntimeError, ValueError, IndexError) as exc:
            raise ProviderUnavailable("Four real index quotes are required for publication.", [str(exc)]) from exc
        if len(indices) != 4 or any(item["value"] is None or item["source_timestamp"] is None for item in indices):
            raise ProviderUnavailable("Four real index quotes are incomplete.", ["indices"])
        timestamps = [item["source_timestamp"] for item in indices]
        latest = max(timestamps)
        now = datetime.now(SHANGHAI)
        freshness = quote_freshness(latest, now, self.closed_days,
                                    self.config["intraday_stale_after_minutes"],
                                    self.config["postmarket_close_minute"])
        for item in indices:
            item["freshness"] = quote_freshness(item["source_timestamp"], now, self.closed_days,
                                                self.config["intraday_stale_after_minutes"],
                                                self.config["postmarket_close_minute"])
        turnover_parts = [indices[0]["turnover"], indices[1]["turnover"]]
        turnover = sum(turnover_parts) if all(value is not None for value in turnover_parts) else None
        missing = ["advance_count", "decline_count", "flat_count", "limit_up_count", "limit_down_count",
                   "turnover_change", "market_amplitude"]
        if turnover is None:
            missing.append("turnover")
        if freshness == "stale":
            missing.append("market_quote_freshness: stale")
        self._market = {"trade_date": latest[:10], "stage_options": [
            {"id": "premarket", "label": "盘前", "time": "09:20"},
            {"id": "intraday", "label": "盘中", "time": "10:30"},
            {"id": "postmarket", "label": "已收盘", "time": "15:30"}],
            "indices": indices, "turnover": turnover, "turnover_change": None,
            "advance_count": None, "decline_count": None, "flat_count": None,
            "limit_up_count": None, "limit_down_count": None, "market_amplitude": None,
            "source": "tencent", "source_timestamp": latest,
            "retrieved_at": indices[0]["retrieved_at"], "freshness": freshness,
            "last_trading_day": latest[:10], "missing_data": missing}
        for item in missing:
            self._missing(f"market.{item}")
        return self._market

    def get_intraday_snapshots(self) -> list[dict[str, Any]]:
        rows = []
        for code in self.codes:
            result = self._published_stock(code)
            quote = result["quote"]
            industry = result["membership"]["industry"]
            sector = next((item for item in result["sectors"] if industry and item["code"] == industry["code"]), None)
            rows.append({"code": code, "current_price": quote.get("current_price"),
                         "open": quote.get("open"), "high": quote.get("high"), "low": quote.get("low"),
                         "volume": quote.get("volume"), "turnover_rate": quote.get("turnover_rate"),
                         "pe": quote.get("pe"), "pb": quote.get("pb"), "market_cap": quote.get("market_cap"),
                         "realtime_volume_ratio": None, "vwap": None,
                         "sector_return": sector["return_pct"] if sector else None,
                         "source": quote.get("source"), "source_timestamp": quote.get("source_timestamp"),
                         "retrieved_at": quote.get("retrieved_at"), "freshness": quote["freshness"],
                         "missing_data": [key for key in ("realtime_volume_ratio", "vwap", "sector_return")
                                          if key != "sector_return" or not sector or sector["return_pct"] is None]})
        return rows

    def get_holdings(self) -> list[dict[str, Any]]:
        return self.local_config.get("holdings", [])

    def get_watchlist(self) -> list[dict[str, Any]]:
        return self.local_config.get("watchlist", [])

    def stats(self) -> dict[str, int]:
        stats = dict(self.http.stats)
        stats["mootdx_requests"] = self.mootdx.stats["requests"]
        stats["mootdx_failures"] = self.mootdx.stats["failures"]
        stats["cache_hits"] += self.mootdx.stats["cache_hits"]
        stats["negative_cache_hits"] += self.mootdx.stats["negative_cache_hits"]
        stats["fallback_count"] = self.fallback_count
        stats["request_failures"] += self.mootdx.stats["failures"]
        stats["total_requests"] += self.mootdx.stats["requests"]
        return stats
