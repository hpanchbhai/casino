# Stocks RSI Dashboard

A lightweight Flask dashboard that calculates RSI across multiple indices/watchlists and provides a charting widget for a single ticker with SMA overlays plus a most‑traded options table.

## What this app does

- **RSI dashboard**: Top 5 lowest/highest RSI for each index/watchlist.
- **Ticker chart widget**: Search a ticker, choose a timeframe, and view a Robinhood‑style line chart.
- **SMA overlays**: Toggle SMA 50/100/200 on the chart.
- **Options table**: Top 5 options (calls + puts) with **Short / Medium / Long Term** expiry selector and configurable sort metric.

## High‑level architecture

- **Backend**: Flask (`app.py`)
  - Fetches market data via **yfinance** per‑ticker to avoid bulk download failures.
  - Computes RSI using **Wilder's smoothing** (exponential moving average, α = 1/14) — the same method used by Robinhood, TradingView, and most professional charting platforms.
  - Fetches 1 year of daily data per ticker for proper EMA warm‑up.
  - Drops NaN close prices before JSON serialization to prevent invalid JSON.
  - Caches expensive responses for 5 minutes.
- **Frontend**: Static HTML/CSS/JS in `static/`
  - Uses fetch() to load data from `/api/*` endpoints.
  - Uses **Chart.js** for the line chart.

### Data flow (RSI)
1. Browser calls `/api/indices`.
2. User selects a tab → browser calls `/api/rsi/<index_key>`.
3. Backend loads tickers from `data/*.txt` or `static/stocks_to_track`, fetches history per ticker, computes RSI, returns sorted results.

### Data flow (Chart)
1. User enters a ticker and timeframe.
2. Browser calls `/api/history?ticker=...&period=...`.
3. Backend returns close prices, frontend renders a line chart.
4. SMA toggles compute averages in the browser (no extra API call).

### Data flow (Options)
1. User selects an expiry term: **Short Term** (all), **Medium Term** (3–6 months), or **Long Term** (6 months – 2 years).
2. User optionally selects a sort metric: Volume, Open Interest, Expiry, Implied Volatility, or Tightest Spread.
3. Browser calls `/api/options?ticker=...&term=...&sort_by=...`.
4. Backend gathers option chains for matching expiries, merges calls+puts, returns top 5 by the chosen metric.

## Project structure

```
Stocks Dashboard/
├─ app.py                  # Flask API + RSI/options logic
├─ requirements.txt        # Python dependencies
├─ data/                   # Index ticker lists
├─ static/
│  ├─ index.html            # UI
│  ├─ style.css             # Styling
│  ├─ script.js             # Frontend logic
│  └─ stocks_to_track       # User watchlist
└─ README.md
```

## API endpoints

- `GET /api/indices` → list of index tabs
- `GET /api/rsi/<index_key>` → RSI results for an index
- `GET /api/history?ticker=TSLA&period=1mo` → chart data (NaN‑safe)
- `GET /api/options?ticker=TSLA&term=long&sort_by=volume` → top 5 options (calls + puts)

### Options `term` parameter
| Term | Expiry Window | Description |
|------|--------------|-------------|
| `short` | 0 – ∞ (all) | All available expirations, no filter |
| `medium` | 90 – 180 days | 3–6 months out |
| `long` (default) | 180 – 730 days | 6 months – 2 years out |

### Options `sort_by` parameter
| Value | Description |
|-------|-------------|
| `volume` (default) | Highest trading volume |
| `open_interest` | Highest open interest |
| `expiry` | Nearest expiry first |
| `iv` | Highest implied volatility |
| `spread` | Tightest bid‑ask spread |

The response includes: option type (Call/Put), expiry, strike, last price, bid, ask, volume, open interest, IV %, and spread.

## Data storage requirements

Durable dashboard data requirements are recorded in `config/data_policy.json` and implemented through the SQLite schema in `db/schema.sql`.
User-stated product and workflow requirements are tracked over time in `config/user_requirements.md`.

### Options retention policy

- Store one row per contract in `option_contracts`.
- Append every observed chain quote into `option_quotes`; do not overwrite historical quote rows.
- Mark contracts as `expired` after expiration, but keep the contract and all quote history indefinitely.
- Preserve expired-contract prices so the dashboard can show historical charts after expiration.
- Store options metrics when available: underlying price, last price, bid, ask, mid, spread, volume, open interest, implied volatility, delta, gamma, theta, vega, rho, intrinsic/extrinsic value, moneyness, and ITM status.
- Keep Greeks nullable because data sources may not provide every value on every fetch.

### Dashboard interaction logging

- Log dashboard usage events into `dashboard_interaction_events`.
- Track sessions in `dashboard_sessions`.
- Use interaction history to understand dashboard usage patterns, feature adoption, user behavior, and navigation flows.
- Keep analytics privacy-conscious: do not store secrets, credentials, tokens, or unnecessary raw personal data.
- Prefer hashed/coarse identifiers such as `ip_hash` over raw IP addresses.

### Price history ingestion

- Initial price ingestion backfills daily OHLCV from `2000-01-01` by default.
- Later runs use `backfill_log.last_date` to fetch only missing rows.
- If the app is stopped for several days, the next ingestion run catches up only the date gap.
- Full re-fetches should be explicit with `--force-full`.
- `/api/history` reads from `daily_prices` first for stored daily/weekly history, then falls back to yfinance for missing data or intraday intervals.

### Index addition performance

- The `Index Adds` tab analyzes how stocks performed after being added to an index.
- Primary workflow is ticker-first: enter a ticker and the app filters the index selector to the current index lists that contain that ticker, then looks up saved index-addition records.
- Saved events live in `index_additions`; ad hoc analysis can still be run with ticker, index, added date, and optional benchmark ETF.
- Watchlist membership lives in `watchlist_symbols`; dashboard add/remove actions keep it synced with `data/watchlist.json`.
- Index memberships live in `index_memberships`; index-addition events live in `index_additions`.
- Refresh watchlist-wide index data with `python -m etl.ingest_index_data`.
- Calculations read from `daily_prices` and include total return, annualized return, 6M/1Y/2Y forward returns, monthly/quarterly/yearly breakdowns, and optional benchmark/excess return.
- Ticker-only lookup requires real index-addition dates to be ingested into `index_additions`.
- API: `GET /api/index-additions/performance?ticker=AAPL&index_key=sp500&added_date=2020-01-01`

Dry-run the fetch plan:

```bash
python -m etl.ingest_prices --dry-run
```

Ingest specific tickers:

```bash
python -m etl.ingest_prices --tickers AAPL MSFT NVDA
```

Ingest the full configured universe:

```bash
python -m etl.ingest_prices --universe all
```

## Local setup

### Requirements
- Python 3.10+

### Install
```
pip install -r requirements.txt
```

### Run
```
python app.py
```
Open http://127.0.0.1:5051 in your browser.

## Notes & tradeoffs

- **RSI method**: Uses Wilder's smoothing (EMA with α = 1/14), matching Robinhood and TradingView. Earlier versions used a simple moving average which gave significantly different (and incorrect) RSI values.
- **Data period**: Fetches 1 year of daily history per ticker to give the EMA enough warm‑up. This makes RSI fetches slightly slower than the old 1‑month fetch, but accuracy is dramatically improved.
- Yahoo Finance can return NaN for some trading days. The history API drops these rows before JSON serialization.
- Yahoo Finance can return empty data for delisted or restricted tickers. Those are skipped.
- Options volume from Yahoo is a **current snapshot**, not historical week‑by‑week volume.
- The term‑based expiry filter lets you focus on short, medium, or long‑dated options depending on your strategy.

## Customization

- Edit `static/stocks_to_track` to change your watchlist.
- Replace or expand ticker lists in `data/`.

---

## Changelog

### v1.1.0 (March 2026)
- **RSI accuracy fix**: Switched from simple moving average to **Wilder's smoothing** (EMA α=1/14), matching Robinhood and TradingView exactly. Increased data fetch from 1 month to 1 year for proper warm‑up.
- **NaN guard**: History API now drops rows with NaN close prices before JSON serialization, preventing `Unexpected token 'N'` parse errors on the frontend.
- **Options sort selector**: Added a "Sort by" dropdown — choose between Volume, Open Interest, Expiry, Implied Volatility, or Tightest Spread. New IV % and Spread columns added to the table.
- **Short / Medium / Long term selector**: Replaced the old 1D/1W/3M/6M/1Y timeframe buttons with three clear term buttons:
  - **Short Term** — all expirations (no filter)
  - **Medium Term** — 90–180 days out (3–6 months)
  - **Long Term** — 180–730 days out (6 months – 2 years, default)

### v1.0.0 (Initial release)
- Multi‑index RSI dashboard with Top 5 lowest/highest tables.
- Ticker chart widget with SMA 50/100/200 overlays.
- Options table showing top 5 by volume for long‑dated expirations.
- In‑memory 5‑minute cache for all API responses.

---

## Appendix — Codebase Flowchart

### Backend (`app.py`)

```mermaid
flowchart TD
    subgraph Routes["Flask Routes"]
        R1["GET /"]
        R2["GET /api/indices"]
        R3["GET /api/rsi/&lt;index_key&gt;"]
        R4["GET /api/history"]
        R5["GET /api/options"]
    end

    subgraph Core["Core Functions"]
        LT["load_tickers(file_path)"]
        RSI["compute_rsi(data, period=14)"]
        FSS["fetch_single_stock(ticker)"]
        FRT["fetch_rsi_for_tickers(tickers)"]
        GID["get_index_data(index_key)"]
        FH["_fetch_history()  (closure)"]
    end

    subgraph External["External / Infra"]
        YF["yfinance API"]
        CACHE["_cache  (in-memory dict)"]
        FILES["data/*.txt  /  stocks_to_track"]
    end

    R1 -->|"send_static_file('index.html')"| StaticHTML["static/index.html"]

    R2 -->|"reads INDICES dict"| INDICES["INDICES config"]

    R3 --> GID
    GID -->|"cache hit?"| CACHE
    GID -->|"miss → load file"| LT
    LT --> FILES
    GID --> FRT
    FRT -->|"ThreadPoolExecutor\n(10 workers)"| FSS
    FSS -->|"yf.Ticker.history()"| YF
    FSS --> RSI
    GID -->|"store result"| CACHE

    R4 -->|"cache hit?"| CACHE
    R4 -->|"miss"| FH
    FH -->|"yf.Ticker.history()\nor yf.download()"| YF
    R4 -->|"store result"| CACHE

    R5 -->|"cache hit?"| CACHE
    R5 -->|"miss → yf.Ticker.options\n+ option_chain()"| YF
    R5 -->|"filter by term\n(short/medium/long),\nmerge calls+puts,\nsort by chosen metric,\ntop 5"| CACHE
```

### Frontend (`static/script.js`)

```mermaid
flowchart TD
    subgraph Init["Initialization"]
        DOMReady["DOMContentLoaded"]
        INIT["init()"]
        SCW["setupChartWidget()"]
    end

    subgraph RSI_Panel["RSI Panel Flow"]
        ST["switchTab(key)"]
        FI["fetchIndex(key, force)"]
        BPH["buildPanelHTML(key)"]
        PT["populateTable(bodyId, stocks)"]
        BR["buildRow(stock, index)"]
        RA["refreshAll()"]
    end

    subgraph Chart["Chart Widget Flow"]
        FRH["fetchAndRenderHistory(ticker, period)"]
        CC["createChart(ctx)"]
        CSMA["computeSMA(values, window)"]
        USMA["updateSmaDatasets(values)"]
        FDL["formatDateLabel(value)"]
    end

    subgraph Options["Options Table Flow"]
        FRO["fetchAndRenderOptions(ticker, timeframe)"]
    end

    subgraph Helpers["Helper Functions"]
        GRC["getRsiClass(rsi)"]
        GS["getSignal(rsi)"]
        FC["formatChange(pct)"]
        BTHTML["buildTableHTML(bodyId)"]
        TAS["toggleAllStocks(key)"]
    end

    subgraph API["API Calls"]
        A1["/api/indices"]
        A2["/api/rsi/:key"]
        A3["/api/history"]
        A4["/api/options"]
    end

    DOMReady --> INIT
    INIT --> SCW
    INIT -->|"fetch"| A1
    INIT -->|"build tabs & panels"| BPH
    INIT --> ST

    ST --> FI
    FI -->|"fetch"| A2
    FI --> PT
    PT --> BR
    BR --> GRC
    BR --> GS
    BR --> FC

    RA -->|"clears loadedPanels"| FI

    SCW -->|"timeframe btn click"| FRH
    SCW -->|"fetch btn / Enter"| FRH
    SCW -->|"SMA checkbox change"| USMA
    SCW -->|"term btn click"| FRO
    SCW -->|"sort dropdown change"| FRO

    FRH -->|"fetch"| A3
    FRH -->|"first call"| CC
    FRH --> USMA
    FRH --> FRO

    USMA --> CSMA

    CC --> FDL

    FRO -->|"fetch"| A4
```

### End‑to‑End Request Flow

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant JS as script.js
    participant FL as Flask (app.py)
    participant YF as yfinance

    Note over U,YF: Page Load
    U->>JS: DOMContentLoaded
    JS->>FL: GET /api/indices
    FL-->>JS: [{key, name, icon}, ...]
    JS->>JS: buildPanelHTML() for each index
    JS->>FL: GET /api/rsi/watchlist
    FL->>FL: get_index_data() → load_tickers()
    FL->>YF: fetch_single_stock() × N  (parallel)
    YF-->>FL: price history per ticker
    FL->>FL: compute_rsi() per ticker
    FL-->>JS: {lowest_rsi, highest_rsi, all_stocks}
    JS->>JS: populateTable()

    Note over U,YF: Chart Lookup
    U->>JS: Enter ticker + click Fetch
    JS->>FL: GET /api/history?ticker=AAPL&period=1mo
    FL->>YF: yf.Ticker("AAPL").history()
    YF-->>FL: OHLCV data
    FL-->>JS: {data: [{date, close}, ...]}
    JS->>JS: createChart() / updateSmaDatasets()

    Note over U,YF: Options Table
    JS->>FL: GET /api/options?ticker=AAPL&term=long&sort_by=volume
    FL->>YF: yf.Ticker.options + option_chain()
    YF-->>FL: calls + puts DataFrames
    FL->>FL: filter by term window, merge, sort, top 5
    FL-->>JS: {top_options: [...], term_label, sort_label}
    JS->>JS: render options table
```

---

If you want a production‑ready deployment (gunicorn, caching, scheduled refresh), I can add that too.
