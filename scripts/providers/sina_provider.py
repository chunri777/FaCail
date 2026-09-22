from __future__ import annotations

import json


class SinaProvider:
    def __init__(self, http, ttl: int) -> None:
        self.http = http
        self.ttl = ttl

    def daily_bars(self, code: str, count: int = 60) -> list[dict]:
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        body = self.http.get(
            "sina",
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
            {"symbol": prefix + code, "scale": 240, "ma": "no", "datalen": count},
            self.ttl,
        )
        rows = json.loads(body)
        if not isinstance(rows, list) or len(rows) < count:
            raise ValueError("Sina returned insufficient daily bars")
        return [{"date": row["day"], "open": float(row["open"]), "high": float(row["high"]),
                 "low": float(row["low"]), "close": float(row["close"]), "volume": int(float(row["volume"]))}
                for row in rows[-count:]]
