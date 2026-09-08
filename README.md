# FaCail

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
MockProvider / future ThsProvider
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
python3 scripts/generate_data.py
python3 scripts/validate_data.py
python3 -m http.server 8080
```

Open `http://localhost:8080/frontend/`.

## Providers

- `MockProvider`: deterministic data for product and rule verification.
- `ThsProvider`: reserved adapter for Tonghuashun QuantAPI or a local bridge. It is intentionally not connected in this phase.

Missing provider fields must remain explicitly missing. AI may summarize structured results, but it must not invent or calculate market values.
