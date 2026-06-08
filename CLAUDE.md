# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A stocks RSI (Relative Strength Index) dashboard that displays oversold/overbought signals for stocks across multiple market indices. Python/Flask backend fetches live data from Yahoo Finance, computes 14-day RSI, and serves it to a vanilla HTML/CSS/JS frontend.

## Running the App

```bash
pip install -r requirements.txt
python app.py
```

The server runs on `http://localhost:5050`. There are no tests, linting, or build steps configured.

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

**Watchlist** — User's personal watchlist lives at `static/stocks_to_track` (same one-ticker-per-line format; first line "Stock List:" is skipped as a header).
