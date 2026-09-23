# FaCail

Fixed local workspace: `/Users/lq/Developer/FaCail`.

FaCail is a personal A-share stock screening and daily holdings review tool. It turns user-defined trading rules into Python calculations, structured JSON, and repeatable review records.

It does not provide automatic trading, control brokerage accounts, predict limit-up moves, or make investment decisions for the user.

## Current Product Structure

- 今日交易驾驶舱
- 持仓：早盘计划、盘中监控、盘后复盘
- 选股：强势板块、强势个股、回踩观察
- 观察池

The development stage switch in the page header simulates pre-market, intraday, and post-market views.

## Architecture

```text
AstockProvider (production) / MockProvider (development) / ThsProvider (optional)
  -> Python indicators and rule engine
  -> JSON data layer
  -> GitHub Pages frontend
```

All strategy thresholds live in `config/strategy.json`. The frontend displays calculated results and does not calculate market indicators.

## Data Outputs

```text
data/
  market.json
  sectors.json
  candidates.json
  holdings.json
  intraday.json
  review.json
  watchlist.json
  today.json
```

## Local Run

```bash
python3 scripts/generate_data.py --provider astock --stage auto
python3 scripts/validate_data.py
python3 -m http.server 8080
```

Open `http://localhost:8080/frontend/`.

## Providers

- `MockProvider`: deterministic data for product and rule verification.
- `ThsProvider`: adapter for Tonghuashun QuantAPI/local bridge. It reads credentials only from environment variables or the local bridge, never from committed files.
- `AstockProvider`: production read-only public-source adapter. It uses mootdx daily bars with Sina fallback, Tencent stock/index quotes, and Eastmoney industry/concept membership and sector returns. Unavailable values stay `null` with `missing_data`. See [data source details](docs/astock-data-sources.md).

Validate the public-source pilot without changing published data:

```bash
python3 scripts/check_astock.py --code 600519 --days 60
```

Generate real publication JSON or switch to Mock for local development:

```bash
python3 scripts/generate_data.py --provider astock --stage auto
FACAIL_PROVIDER=mock python3 scripts/generate_data.py
FACAIL_PROVIDER=ths python3 scripts/generate_data.py
python3 scripts/generate_data.py --provider ths --stage auto
```

Check THS access before generating real data:

```bash
python3 scripts/check_ths_access.py
```

Current supported local bridge endpoints:

```text
THS_LOCAL_API_URL=http://127.0.0.1:5000

GET /health
GET /market
GET /sectors
GET /daily-bars?codes=600519,000333
GET /intraday?codes=600519,000333
GET /holdings
GET /watchlist
```

For provider-contract testing without THS authorization:

```bash
python3 scripts/dev_ths_bridge.py
THS_LOCAL_API_URL=http://127.0.0.1:5011 python3 scripts/generate_data.py --provider ths
```

This development bridge reuses Mock data and is only for verifying the local API shape.

If using the Tonghuashun SDK directly, install the SDK locally and provide one of:

```text
THS_TOKEN / THS_API_TOKEN / IFIND_TOKEN
THS_USERNAME + THS_PASSWORD
IFIND_USER + IFIND_PASSWORD
```

Do not commit tokens, passwords, or real account files.

## Personal Holdings

Copy `config/holdings.example.json` to `config/holdings.local.json`, then edit your real holdings and watchlist locally. `holdings.local.json` is ignored by Git.

You can also point to another private file:

```bash
FACAIL_HOLDINGS_FILE=/path/to/holdings.json python3 scripts/generate_data.py --provider astock
```

The local configuration file is ignored by Git, but generated `data/holdings.json`,
`data/intraday.json`, and `data/review.json` are public if committed to GitHub
Pages. Review those outputs before publishing personal positions. With no local
configuration, the real provider publishes empty holdings and watchlist states.

`config/universe.json` defines the bounded first-version scan pool. The
2026 trading calendar in `config/trading_calendar.json` drives pre-market,
intraday, post-market, weekend, and holiday freshness checks. Refresh published
JSON through the local update agent; GitHub Pages does not run Python or fetch
live quotes itself. The open page checks for a newly deployed snapshot every
five minutes.

## Automatic Updates (macOS)

`scripts/run_update.py` uses Shanghai time and the local trading calendar. The
launchd agent schedules 08:30 pre-market, 09:35-11:25 and 13:05-14:55 every
ten minutes, and 15:10 post-market. It skips weekends, configured holidays,
and lunch, and runs only the latest missed slot after wake. A missed post-market
review may run once later on the same trading day.

Each run generates to a temporary directory, validates all JSON, and requires
at least seven of the eight configured holdings and three of four indices with
stage-appropriate freshness. Only then are the eight public `data/*.json`
files copied, committed, and pushed. A failed push leaves the local commit
for retry before the next generation. GitHub Pages is checked after push.
Private holdings configuration, caches, and local logs are never staged.

```bash
python3 scripts/run_update.py --check  # schedule decision; no market requests
python3 scripts/run_update.py --force  # one real update on a trading day
```

The local agent definition is `launchd/com.facail.update.plist`. Run records
and retry state live in ignored `logs/`. The bundled trading-holiday calendar
currently covers **2026**; update `config/trading_calendar.json` before 2027.
The agent requires the Mac to be logged in, awake, and online for scheduled
runs; it cannot publish while the Mac is off.

Missing provider fields must remain explicitly missing. AI may summarize structured results, but it must not invent or calculate market values.
