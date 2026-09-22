from __future__ import annotations

import json
from argparse import ArgumentParser

from providers.astock_provider import AstockProvider


def main() -> None:
    parser = ArgumentParser(description="Read-only A-share source validation; does not update published JSON.")
    parser.add_argument("--code", default="600519")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    result = AstockProvider(codes=[args.code]).inspect_stock(args.code, args.days)
    sample = {**result, "bars": {"count": len(result["bars"]), "first": result["bars"][0] if result["bars"] else None,
                                     "last": result["bars"][-1] if result["bars"] else None}}
    print(json.dumps(sample, ensure_ascii=False, indent=2))
    required = (len(result["bars"]) == args.days, result["quote"]["current_price"] is not None,
                result["quote"]["turnover_rate"] is not None, result["quote"]["market_cap"] is not None,
                result["membership"]["industry"] is not None,
                any(row["return_pct"] is not None for row in result["sectors"]))
    if not all(required):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
