from __future__ import annotations

from datetime import datetime
import re


def to_float(value: str) -> float | None:
    try:
        return float(value) if value and value != "--" else None
    except ValueError:
        return None


class TencentProvider:
    def __init__(self, http, ttl: int) -> None:
        self.http = http
        self.ttl = ttl

    def quote(self, code: str) -> dict:
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        body = self.http.get("tencent", "https://qt.gtimg.cn/q=" + prefix + code, {}, self.ttl, "gbk")
        fields = self._fields(body, code)
        if len(fields) <= 46 or fields[2] != code:
            raise ValueError("Tencent quote missing expected fields")
        stamp = fields[30]
        quoted_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").isoformat() if len(stamp) == 14 else None
        retrieved_at = getattr(self.http, "last_result", {}).get("retrieved_at")
        return {
            "code": code,
            "name": fields[1],
            "current_price": to_float(fields[3]),
            "previous_close": to_float(fields[4]),
            "open": to_float(fields[5]),
            "high": to_float(fields[33]),
            "low": to_float(fields[34]),
            "volume": int(float(fields[6]) * 100) if to_float(fields[6]) is not None else None,
            "change_pct": to_float(fields[32]) / 100 if to_float(fields[32]) is not None else None,
            "turnover_rate": to_float(fields[38]) / 100 if to_float(fields[38]) is not None else None,
            "pe": to_float(fields[39]),
            "market_cap": to_float(fields[44]) * 100000000 if to_float(fields[44]) is not None else None,
            "pb": to_float(fields[46]),
            "quoted_at": quoted_at,
            "source_timestamp": quoted_at,
            "retrieved_at": retrieved_at,
            "source": "tencent",
        }

    @staticmethod
    def _fields(body: str, code: str) -> list[str]:
        match = re.search(rf'v_[a-z]{{2}}{re.escape(code)}="([^"]*)"', body)
        if not match:
            raise ValueError(f"Tencent quote format unavailable for {code}")
        return match.group(1).split("~")

    def indices(self, symbols: list[str]) -> list[dict]:
        body = self.http.get("tencent", "https://qt.gtimg.cn/q=" + ",".join(symbols), {}, self.ttl, "gbk")
        retrieved_at = getattr(self.http, "last_result", {}).get("retrieved_at")
        rows = []
        for symbol in symbols:
            code = symbol[2:]
            fields = self._fields(body, code)
            if len(fields) <= 35 or fields[2] != code:
                raise ValueError(f"Tencent index fields missing for {symbol}")
            stamp = fields[30]
            timestamp = datetime.strptime(stamp, "%Y%m%d%H%M%S").isoformat() if len(stamp) == 14 else None
            turnover_text = fields[35].split("/")[-1]
            rows.append({"code": f"{code}.{'SH' if symbol.startswith('sh') else 'SZ'}",
                         "name": fields[1], "value": to_float(fields[3]),
                         "change_pct": to_float(fields[32]) / 100 if to_float(fields[32]) is not None else None,
                         "turnover": to_float(turnover_text), "source": "tencent",
                         "source_timestamp": timestamp, "retrieved_at": retrieved_at})
        return rows
