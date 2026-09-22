from __future__ import annotations

import json


class EastmoneyProvider:
    def __init__(self, http, sector_ttl: int, membership_ttl: int, history_ttl: int | None = None) -> None:
        self.http = http
        self.sector_ttl = sector_ttl
        self.membership_ttl = membership_ttl
        self.history_ttl = history_ttl or sector_ttl

    def membership(self, code: str) -> dict:
        suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        rows = []
        page = 1
        while page <= 3:
            body = self.http.get(
                "eastmoney", "https://datacenter.eastmoney.com/securities/api/data/v1/get",
                {"reportName": "RPT_F10_CORETHEME_BOARDTYPE", "columns": "SECUCODE,BOARD_CODE,BOARD_NAME,BOARD_TYPE,BOARD_LEVEL,NEW_BOARD_CODE,IS_PRECISE",
                 "filter": f'(SECUCODE="{code}.{suffix}")', "pageNumber": page, "pageSize": 50},
                self.membership_ttl,
            )
            result = json.loads(body).get("result") or {}
            if result.get("pages", 0) > 3:
                raise ValueError("Eastmoney membership exceeds bounded page budget")
            rows.extend(result.get("data") or [])
            if page >= result.get("pages", 0):
                break
            page += 1
        industries = [row for row in rows if row.get("BOARD_TYPE") == "行业"]
        industry = next((row for row in industries if str(row.get("BOARD_LEVEL")) == "2"), None)
        if industry is None:
            industry = next((row for row in industries if str(row.get("BOARD_LEVEL")) == "1"), None)
        concepts = [{"code": row["NEW_BOARD_CODE"], "name": row["BOARD_NAME"]}
                    for row in rows if row.get("BOARD_TYPE") is None and row.get("IS_PRECISE") == "1"]
        return {"industry": {"code": industry["NEW_BOARD_CODE"], "name": industry["BOARD_NAME"]} if industry else None,
                "concepts": concepts, "retrieved_at": getattr(self.http, "last_result", {}).get("retrieved_at")}

    def sector_quote(self, board: dict) -> dict:
        body = self.http.get(
            "eastmoney", "https://push2.eastmoney.com/webguest/api/qt/stock/get",
            {"secid": "90." + board["code"], "fields": "f57,f58,f43,f60,f170,f104,f105", "fltt": 2},
            self.sector_ttl,
        )
        data = json.loads(body).get("data") or {}
        if data.get("f57") != board["code"] or data.get("f170") is None:
            raise ValueError(f"Eastmoney sector quote missing for {board['code']}")
        return {"code": board["code"], "name": data.get("f58") or board["name"],
                "return_pct": round(float(data["f170"]) / 100, 6),
                "source": "eastmoney", "source_timestamp": None,
                "retrieved_at": getattr(self.http, "last_result", {}).get("retrieved_at")}

    def sector_history(self, board: dict, count: int = 25) -> dict:
        body = self.http.get(
            "eastmoney", "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {"secid": "90." + board["code"], "fields1": "f1,f2,f3,f4,f5,f6",
             "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
             "klt": 101, "fqt": 0, "lmt": count, "end": "20500101"},
            self.history_ttl,
        )
        data = json.loads(body).get("data") or {}
        lines = data.get("klines") or []
        if data.get("code") != board["code"] or len(lines) < 21:
            raise ValueError(f"Eastmoney sector history insufficient for {board['code']}")
        rows = []
        for line in lines[-count:]:
            fields = line.split(",")
            rows.append({"date": fields[0], "close": float(fields[2]), "turnover": float(fields[6])})
        closes = [row["close"] for row in rows]
        amounts = [row["turnover"] for row in rows]
        ma20 = sum(closes[-20:]) / 20
        previous5 = sum(amounts[-6:-1]) / 5
        return {"return_pct": round(closes[-1] / closes[-2] - 1, 6),
                "return_5d": round(closes[-1] / closes[-6] - 1, 6),
                "return_20d": round(closes[-1] / closes[-21] - 1, 6),
                "previous_return_pct": round(closes[-2] / closes[-3] - 1, 6),
                "ma20_above": closes[-1] >= ma20,
                "turnover_change": round(amounts[-1] / amounts[-2] - 1, 6) if amounts[-2] else None,
                "turnover_5d_change": round(amounts[-1] / previous5 - 1, 6) if previous5 else None,
                "source_timestamp": rows[-1]["date"],
                "retrieved_at": getattr(self.http, "last_result", {}).get("retrieved_at")}
