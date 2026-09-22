from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from providers.tencent_provider import TencentProvider
from providers.sina_provider import SinaProvider
from providers.astock_provider import AstockProvider
from providers.eastmoney_provider import EastmoneyProvider


class FakeHttp:
    def __init__(self, response: str) -> None:
        self.response = response

    def get(self, *args, **kwargs) -> str:
        return self.response


class AstockTests(unittest.TestCase):
    def test_mootdx_failure_uses_sina_without_mock_data(self) -> None:
        provider = AstockProvider(codes=["600519"])

        def fail(*args, **kwargs):
            raise RuntimeError("TCP unavailable")

        provider.mootdx.daily_bars = fail
        provider.sina.daily_bars = lambda code, count: [{"date": "2026-09-22", "open": 1.0, "high": 1.0,
                                                         "low": 1.0, "close": 1.0, "volume": 1}] * count
        provider.tencent.quote = lambda code: {"code": code, "name": "贵州茅台", "current_price": 1.0,
                                                "turnover_rate": 0.01, "market_cap": 1.0,
                                                "pe": 1.0, "pb": 1.0, "quoted_at": "2026-09-22T15:00:00"}
        provider.eastmoney.membership = lambda code: {"industry": {"code": "BK1277", "name": "白酒Ⅱ"}, "concepts": []}
        provider.eastmoney.sector_quote = lambda board: {"code": board["code"], "name": board["name"],
                                                         "return_pct": -0.0016, "source": "eastmoney"}
        result = provider.inspect_stock("600519", 60)
        self.assertEqual(result["sources"]["daily_bars"], "sina_fallback")
        self.assertEqual(result["sources"]["quote"], "tencent")
        self.assertEqual(result["sources"]["industry"], "eastmoney")
        self.assertTrue(any(item.startswith("mootdx:") for item in result["missing_data"]))

    def test_eastmoney_industry_and_concepts(self) -> None:
        rows = [
            {"BOARD_TYPE": "行业", "BOARD_LEVEL": "2", "NEW_BOARD_CODE": "BK1277", "BOARD_NAME": "白酒Ⅱ"},
            {"BOARD_TYPE": None, "IS_PRECISE": "1", "NEW_BOARD_CODE": "BK0896", "BOARD_NAME": "白酒"},
            {"BOARD_TYPE": None, "IS_PRECISE": "0", "NEW_BOARD_CODE": "BK0500", "BOARD_NAME": "HS300_"},
        ]
        body = json.dumps({"result": {"pages": 1, "data": rows}})
        membership = EastmoneyProvider(FakeHttp(body), 300, 86400).membership("600519")
        self.assertEqual(membership["industry"]["code"], "BK1277")
        self.assertEqual(membership["concepts"], [{"code": "BK0896", "name": "白酒"}])

    def test_eastmoney_percent_conversion(self) -> None:
        body = json.dumps({"data": {"f57": "BK1277", "f58": "白酒Ⅱ", "f170": -0.16}})
        quote = EastmoneyProvider(FakeHttp(body), 300, 86400).sector_quote({"code": "BK1277", "name": "白酒Ⅱ"})
        self.assertEqual(quote["return_pct"], -0.0016)

    def test_tencent_units_and_missing(self) -> None:
        fields = [""] * 53
        for index, value in {1: "贵州茅台", 2: "600519", 3: "1253.80", 4: "1252.57",
                             30: "20260922161442", 32: "0.10", 38: "0.20", 39: "19.25",
                             44: "15673.52", 46: "6.24"}.items():
            fields[index] = value
        quote = TencentProvider(FakeHttp('v_sh600519="' + "~".join(fields) + '";'), 30).quote("600519")
        self.assertEqual(quote["turnover_rate"], 0.002)
        self.assertEqual(quote["market_cap"], 1567352000000)
        self.assertEqual(quote["change_pct"], 0.001)
        self.assertIsNone(quote["open"])

    def test_sina_rejects_short_history(self) -> None:
        with self.assertRaises(ValueError):
            SinaProvider(FakeHttp('[]'), 10).daily_bars("600519", 60)

    def test_invalid_code_never_requests_network(self) -> None:
        with self.assertRaises(ValueError):
            AstockProvider().inspect_stock("600519.SH")


if __name__ == "__main__":
    unittest.main()
