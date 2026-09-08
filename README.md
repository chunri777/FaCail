# FaCail

FaCail is a personal A-share stock screening and holdings daily review tool. It helps turn user-defined trading rules into code, structured JSON, and repeatable daily reviews.

FaCail is not stock recommendation software, does not provide automated trading, does not predict limit-up moves, and does not make investment decisions for the user.

## MVP Scope

- 今日总览
- 今日候选
- 我的持仓
- 今日复盘
- 观察池

## Architecture

```text
GitHub Pages frontend
Python data pipeline
JSON data layer
Provider adapter layer
```

Current provider:

- Mock Provider

Reserved provider:

- Tonghuashun QuantAPI / local API provider

## Project Structure

```text
config/
  strategy.json
data/
  holdings.json
  today.json
  watchlist.json
frontend/
  index.html
  app.js
  styles.css
scripts/
  generate_data.py
  providers/
    base_provider.py
    mock_provider.py
    ths_provider.py
```

## Local Run

Generate JSON data:

```bash
python3 scripts/generate_data.py
```

Preview locally from the repository root:

```bash
python3 -m http.server 8080
```

Then open:

```text
http://localhost:8080/frontend/
```

## Data Principle

Key market metrics are calculated by Python before JSON is written. AI-generated summaries may explain structured results, but must not invent missing market data or calculate indicators from guesses.
