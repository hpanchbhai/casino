# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A stocks RSI (Relative Strength Index) dashboard that displays oversold/overbought signals for stocks across multiple market indices. Python/Flask backend fetches live data from Yahoo Finance, computes 14-day RSI, and serves it to a vanilla HTML/CSS/JS frontend.

## Running the App

```bash
pip install -r requirements.txt
python app.py
```

The server runs on `http://localhost:5051`. There are no tests, linting, or build steps configured.

## Architecture

**Backend (`app.py`)** — Single-file Flask app serving both the API and static files:
- `GET /` — Serves `static/index.html`
- `GET /api/indices` — Returns list of available indices (watchlist, S&P 500, NASDAQ 100, etc.)
- `GET /api/rsi/<index_key>` — Returns RSI data for all stocks in the given index, sorted by RSI. Uses `ThreadPoolExecutor(max_workers=10)` for parallel yfinance calls and an in-memory cache with 5-minute TTL.
- Index definitions in `INDICES` dict map keys to ticker list files and display metadata.

**Frontend (`static/`)** — Vanilla JS single-page app with tab-based navigation:
- `script.js` — Lazy-loads data per tab on first click; panels are built dynamically from API responses. Shows top 5 lowest RSI (oversold) and top 5 highest RSI (overbought), with a collapsible full list.
- `style.css` — All styling (no framework).

**Data files (`data/`)** — Plain text files with one ticker symbol per line for each index (sp500.txt, nasdaq100.txt, dowjones.txt, russell2000.txt, spdr_etfs.txt).

**Watchlist** — User's personal watchlist is edited through `data/watchlist.json` and dashboard add/remove APIs. `static/stocks_to_track` is a flat compatibility mirror. Active/historical watchlist membership is synced into `watchlist_symbols` and `watchlist_symbol_events`.

## Durable Data Requirements

Project data-retention requirements live in `config/data_policy.json`; database tables live in `db/schema.sql`.
User-stated product and workflow requirements live in `config/user_requirements.md`. Append to that file whenever the user gives a new requirement, preference, constraint, or important clarification.

Options data must be cumulative:
- `option_contracts` stores one row per contract and keeps expired contracts.
- `option_quotes` appends every observed quote snapshot and keeps history indefinitely.
- Expired contracts should be marked with status `expired`, but historical prices and Greeks remain queryable for dashboard charts.
- Required options fields include bid, ask, last price, IV, open interest, volume, delta, gamma, theta, vega, and rho when available. Greeks are nullable because source coverage varies.

Dashboard interaction logging is required:
- Store sessions in `dashboard_sessions`.
- Append user interaction events to `dashboard_interaction_events`.
- Use logs to understand usage patterns, feature adoption, user behavior, and navigation flows.
- Keep analytics privacy-conscious; never store secrets, credentials, tokens, or unnecessary raw personal data.

Price history ingestion must be incremental:
- Use `python -m etl.ingest_prices`.
- First run per ticker backfills daily OHLCV from `2000-01-01`.
- Later runs read `backfill_log.last_date` and fetch only the missing range.
- Do not re-fetch full history unless the user explicitly asks for a forced full refresh.
- `/api/history` should read from `daily_prices` first for daily/weekly history and preserve yfinance fallback for intraday or missing data.

Index addition performance is a required dashboard surface:
- The `Index Adds` tab compares stock performance after index addition dates.
- Primary workflow is ticker-only lookup against saved `index_additions` records.
- Use `python -m etl.ingest_index_data` to sync watchlist membership, current index memberships, and watchlist-relevant index-addition events.
- Ad hoc analysis remains available with ticker, index key, addition date, and optional benchmark ticker.
- Use `daily_prices` for calculations and preserve benchmark fields as optional because benchmark ETFs may not be ingested yet.
- Ticker-only lookup should not invent dates; add ingestion/source discovery for real index-addition dates.
