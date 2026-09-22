from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_data import build_conditions, build_holdings, enrich_stock, load_config
from providers.mock_provider import load_local_user_config
from trading_calendar import SHANGHAI, daily_freshness, load_closed_days, quote_freshness, trade_stage


class RealGenerationTests(unittest.TestCase):
    def test_trading_day_freshness(self) -> None:
        closed = load_closed_days()
        cases = [
            ("2026-09-22T15:00:00", "2026-09-23T08:30:00", "last_trading_day"),
            ("2026-09-23T10:02:00", "2026-09-23T10:10:00", "current"),
            ("2026-09-23T09:30:00", "2026-09-23T10:10:00", "stale"),
            ("2026-09-23T15:00:00", "2026-09-23T16:00:00", "current"),
            ("2026-09-24T15:00:00", "2026-09-27T11:00:00", "last_trading_day"),
        ]
        for source, now, expected in cases:
            with self.subTest(source=source, now=now):
                self.assertEqual(quote_freshness(source, datetime.fromisoformat(now).replace(tzinfo=SHANGHAI), closed), expected)
        self.assertEqual(trade_stage(datetime(2026, 9, 25, 10, tzinfo=SHANGHAI), closed), "postmarket")
        self.assertIn(date(2026, 9, 25), closed)
        self.assertEqual(daily_freshness("2026-09-22", datetime(2026, 9, 23, 8, tzinfo=SHANGHAI), closed),
                         "last_trading_day")
        self.assertEqual(daily_freshness("2026-09-24", datetime(2026, 9, 27, 11, tzinfo=SHANGHAI), closed),
                         "last_trading_day")

    def test_missing_sector_never_satisfies_buy_condition(self) -> None:
        config = load_config()
        stock = {"close": 100, "ma10": 100, "ma20": 100, "ma20_distance": 0,
                 "change_pct": 0, "volume_ratio_5d": 0.7, "volume_complete": True,
                 "recent_support": 99, "previous_high": 101, "relative_sector_strength": None,
                 "sector": {"status": "数据缺失", "is_strong": False, "rank_percentile": None,
                            "return_5d": None}}
        conditions = build_conditions(stock, config)
        self.assertEqual([item["status"] for item in conditions], ["未满足"] * 3)
        self.assertTrue(all(item["missing_count"] for item in conditions))

    def test_local_holdings_list_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holdings.json"
            rows = [{"code": "600519", "name": "贵州茅台", "cost": 1500, "shares": 100}]
            path.write_text(json.dumps(rows), encoding="utf-8")
            with patch.dict(os.environ, {"FACAIL_HOLDINGS_FILE": str(path)}):
                self.assertEqual(load_local_user_config(), {"holdings": rows, "watchlist": []})

    def test_premarket_uses_previous_session_and_portfolio_uses_previous_value(self) -> None:
        config = load_config()
        bars = [{"date": (date(2026, 7, 1) + timedelta(days=index)).isoformat(),
                 "open": 100, "high": 101 if index < 59 else 121,
                 "low": 99, "close": 100 if index < 59 else 120, "volume": 1000}
                for index in range(60)]
        sector = {"return_pct": 0.01, "status": "数据缺失", "is_strong": False,
                  "rank_percentile": None, "return_5d": None}
        raw = {"code": "600519", "name": "贵州茅台", "industry": "白酒Ⅱ",
               "bars": bars, "quote": {}, "trade_stage": "postmarket"}
        current = enrich_stock(raw, sector, config)
        prior = enrich_stock({**raw, "bars": bars[:-1], "trade_stage": "premarket"}, sector, config)

        class Provider:
            id = "astock"
            label = "A股公开数据"
            _market = {"trade_date": bars[-1]["date"]}
            missing_data = []

        holdings, _, _ = build_holdings([current], [{"code": "600519", "cost": 90, "shares": 100}],
                                        [], config, Provider(), "postmarket", premarket_stocks=[prior])
        self.assertEqual(holdings["premarket"][0]["close"], 100)
        self.assertEqual(holdings["holdings"][0]["close"], 120)
        self.assertAlmostEqual(holdings["summary"]["portfolio_change_pct"], 0.2)


if __name__ == "__main__":
    unittest.main()
