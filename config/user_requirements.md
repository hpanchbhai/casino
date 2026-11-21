# User Requirements Log

This file records product, data, workflow, and architecture requirements as they are stated or clarified.
Use it as the running source of intent for Stock Tracker. Append new entries instead of rewriting history, so the project can show how requirements evolved over time.

## How To Maintain

- Add a dated entry whenever the user states a new requirement, preference, constraint, or important clarification.
- Keep entries concise, but preserve the user's intent.
- Link to related implementation files when useful.
- Do not store secrets, credentials, private account details, or disposable debugging notes here.

## Requirements

### 2026-06-12 — Separate Dashboard And Tracker Projects

- Keep `Stocks Dashboard` and `stock-tracker` as two separate projects and separate GitHub repositories.
- Allow both projects to evolve independently in separate development silos.
- Replicate dashboard functionality into `stock-tracker` before making `stock-tracker` the durable/backend-backed version.

### 2026-06-12 — Port Convention

- Use port `5050` for `Stocks Dashboard`.
- Use port `5051` for `stock-tracker`.
- Keep docs and app defaults aligned with this convention.

### 2026-06-12 — Backend-Backed Dashboard Data

- Move expensive or slow external data fetching out of dashboard page-view/API request paths where practical.
- Store required dashboard data in a database.
- Have dashboard endpoints query stored data first.
- Keep manual refresh or fallback paths for intentional live re-fetches.
- Start backend-backed work with 13F whale/smart-money data because it is slow, external-service dependent, and naturally quarterly.

### 2026-06-12 — 13F Smart Money Storage

- Store 13F filers, filings, holdings, holding changes, and consensus snapshots.
- Update `/api/whales/consensus` to read from the database first once ingestion exists.
- Preserve live fetch as a fallback or explicit refresh path.

### 2026-06-12 — Options Data Retention

- Build cumulative options tables.
- Store one row per contract in `option_contracts`.
- Append every observed quote/chain refresh into `option_quotes`.
- Mark contracts as `expired` after expiration, but do not delete the contract or historical quote rows.
- Keep expired-contract prices and metrics so dashboard charts can show historical options behavior after expiration.
- Store values such as rho, gamma, delta, theta, vega, implied volatility, bid, ask, open interest, volume, and other useful quote metrics when available.
- Keep Greeks nullable because source coverage can vary.
- Machine-readable policy lives in `config/data_policy.json`.

### 2026-06-12 — Requirement History

- Keep adding new user requirements and requests to this log.
- Use the log to understand what the user wants as a whole, how needs evolve over time, and to support future planning even when the eventual use of the history is not yet fully clear.

### 2026-06-12 — Dashboard Interaction Logging

- Log user interactions on the dashboard somewhere durable.
- Use the interaction history to understand dashboard usage patterns and user behavior.
- Capture enough context to analyze workflows, feature usage, and navigation paths over time.
- Keep the logging privacy-conscious: avoid storing secrets, credentials, or unnecessary raw personal data.

### 2026-06-12 — Incremental Price Backfills

- Pull full historical price data once, starting from around year 2000 through the current date.
- After the initial backfill, only fetch missing/new price rows on a daily or catch-up basis.
- If the app stops for several days, the next run should fetch only the gap from the last stored date through today.
- Avoid re-pulling the full history unless explicitly requested.
- Use stored backfill metadata to reduce redundant external API calls and keep data fetching efficient.
- Implement ingestion as a reusable command, starting with `python -m etl.ingest_prices`.

### 2026-06-12 — DB-First Price History API

- Update `/api/history` in `stock-tracker` to read from `daily_prices` first when the request can be satisfied by stored daily/weekly data.
- Preserve live yfinance fallback for missing data and intraday requests.
- Keep the frontend response shape compatible with the existing chart.

### 2026-06-12 — DB-First History Verification Note

- While verifying the DB-backed `/api/history` route, direct `curl` checks from the tool sandbox were intermittently unable to connect to `127.0.0.1:5051` even when `lsof` showed Flask listening.
- The route itself was verified successfully with Flask's `test_client`, which exercised the same application code path without relying on the flaky local network check.
- Verified response for `/api/history?ticker=AAPL&period=1mo`: status `200`, source `db`, interval `1d`, 23 points.
- If this happens again, prefer Flask `test_client` for route-level verification, and separately use `lsof`/server logs to verify process state.

### 2026-06-12 — Index Addition Performance

- Add a dashboard tab for stock performance after a stock is added to an index.
- Support S&P 500, NASDAQ 100, Russell 2000, Dow Jones, and custom ETF/index-style benchmarks.
- Use stored `daily_prices` to calculate performance since the addition date.
- Track useful metrics such as starting price, latest price, total return, annualized return, days since addition, 1M/3M/1Y forward returns when available, and optional benchmark comparison.
- Allow ad hoc analysis by ticker, index/benchmark, and addition date while also supporting saved index-addition records in the database.
- Refined workflow: user should be able to enter only a ticker.
- The dashboard should find when that stock was added to which supported index, then show performance from the addition date.
- Show post-addition performance windows including 6 months, 1 year, 2 years, and since-addition return.
- Show performance grouped by monthly, quarterly, and yearly periods after the addition date.
- Do not make the user provide the addition date in the normal workflow.
- After a ticker is entered, filter the index selector to only indices/lists where that ticker is currently present, such as showing only S&P 500 when the ticker is in S&P 500.
- Keep manual date entry only as an advanced/fallback path, not the primary interaction.

### 2026-06-12 — Stepwise Execution Workflow

- Execute larger implementation or data-ingestion work one step at a time.
- After each step, verify/check the result before proceeding.
- Proceed to the next step only after the current step has been checked.
- For repetitive safe ingestion batches, do not stop to ask after every successful batch.
- Notify the user, run the next sensible batch, verify it, and continue autonomously unless an error or unusual result appears.

### 2026-06-13 — Watchlist-Wide Index Addition Data

- Implement index-addition performance for all stocks in the user's watchlist, not just individually patched tickers.
- Store watchlist membership, dashboard add/remove actions, index memberships, and index-addition events in database tables.
- Keep the database in sync when the dashboard watchlist button adds or removes tickers.
- Do not keep index-addition event data in config files; config files should record requirements/policy only.
- Build ingestion commands that can refresh watchlist-backed index data as the watchlist evolves.

### 2026-06-13 — Index Adds Section Enhancements

- Show a raw stock price chart since the stock's index inclusion date in addition to the normalized return chart.
- Add a quarterly index changes widget listing stocks added to and removed from indexes.
- Quarterly index changes should show the full index add/remove event, not only tickers that are currently in the user's watchlist.
- Quarterly changes selectors should include index and year filters.
- The year selector should be multi-selectable without turning the control into a tall scrolling box.

### 2026-06-13 — All-Index Expansion

- Expand index-addition functionality beyond the user's watchlist.
- Entering any ticker should show every ingested index/list that ticker currently belongs to.
- Do not hard-limit ticker membership lookup to only S&P 500, NASDAQ 100, Russell 2000, and Dow Jones.
- Keep index definitions in database tables so the dashboard can grow to additional indices/lists over time.
- Quarterly add/remove views should be driven by selected index and selected year(s), using full index constituent-change records from the database.
- Show quarterly stocks added/removed by default as its own section, separate from the ticker-specific stock inclusion performance filter.
