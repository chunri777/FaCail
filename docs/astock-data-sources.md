# A-share public data provider

`AstockProvider` generates the complete read-only FaCail publication JSON:
`python3 scripts/generate_data.py --provider astock --stage auto`.
Mock data is never used as a fallback in this mode.

Run `python3 scripts/check_astock.py --code 600519 --days 60`. Install mootdx
separately (`pip install 'mootdx>=0.11,<0.12'`) for the preferred K-line source.
If mootdx is unavailable, Sina daily K-lines are used and the fallback is
recorded in `sources` and `missing_data`.
Failed mootdx host probes are cached briefly to avoid repeated TCP scans.

| Source | Fields | FaCail mapping |
| --- | --- | --- |
| mootdx (Sina fallback) | daily date, OHLCV | `bars[]` in `get_daily_bars()` |
| Tencent quote | price, turnover, PE, PB, market cap, timestamp | `quote` in `inspect_stock()` |
| Eastmoney F10 and board quote | industry/concept membership, board change | `industry`, `industry_return`, `get_sectors()` |

Percentage fields in FaCail are fractions: Tencent `0.20` turnover becomes
`0.002`, and Eastmoney `-0.16` board change becomes `-0.0016`. Tencent market
capitalization in 100-million CNY is converted to CNY. Quote timestamps are
preserved separately from retrieval times. Freshness uses the Shanghai trading
calendar and market stage, so the previous close is valid before the next open
and on holidays, while delayed intraday quotes are marked stale.

Timeouts, retries, cache TTL, endpoint pacing, and the cap on concept quote
requests are in `config/astock_sources.json`. Eastmoney calls are serialized
and spaced at least 1.5 seconds apart. If Eastmoney sector history is
unavailable, 5/20-day sector trends remain `null`; ranking, market breadth,
intraday VWAP, and other unverified fields also remain `null` with
`missing_data`. Missing inputs cannot satisfy a buy-condition rule.
The Eastmoney board quote endpoint used here does not provide a verified quote
timestamp, so sector returns are presented as the latest returned values, not
silently labeled as today's live prices.

Implementation was informed by the data-source routing in
[TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock/blob/main/tradingagents/dataflows/a_stock.py),
which is Apache-2.0 licensed. No source code from that repository was copied.
Its [LICENSE](https://github.com/simonlin1212/TradingAgents-astock/blob/main/LICENSE)
and [NOTICE](https://github.com/simonlin1212/TradingAgents-astock/blob/main/NOTICE)
remain with that project. mootdx is a separate optional dependency with its
own [MIT license](https://github.com/mootdx/mootdx/blob/master/LICENSE).
