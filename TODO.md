# TODO

## Backend-Backed Dashboard Data Layer

- Keep `stocks-dashboard` and `stock-tracker` as separate repos and development silos.
- Use `stock-tracker` as the durable/backend-backed version of the dashboard.
- Move data fetching out of page-view/API request paths where practical.
- Add ingestion jobs that fetch and normalize external data into a database.
- Have dashboard API endpoints read from stored data using queries.
- Keep manual refresh routes/actions for intentional re-fetches.

Initial implementation slice:

- Start with 13F whale data because it is slow, external-service dependent, and naturally quarterly.
- Add database schema for filers, filings, holdings, holding changes, and consensus snapshots.
- Add an ingestion command, e.g. `python -m etl.ingest_13f`.
- Update `/api/whales/consensus` to read from DB first.
- Preserve live fetch as a fallback or explicit refresh path.

Candidate later slices:

- Politician trades
- Earnings/calendar data
- Price history and index membership
- Options activity
  - Retention/config requirement is recorded in `config/data_policy.json`.
  - Store one row per contract in `option_contracts`.
  - Append every observed quote/chain refresh into `option_quotes`.
  - Mark contracts expired when expiration passes, but retain all historical quote rows.
  - Keep Greeks/metrics when available: delta, gamma, theta, vega, rho, IV, bid, ask, open interest, volume.
- Dashboard usage analytics
  - Retention/config requirement is recorded in `config/data_policy.json`.
  - Store sessions in `dashboard_sessions`.
  - Append interactions into `dashboard_interaction_events`.
  - Use this data to understand dashboard usage patterns, user behavior, feature adoption, and navigation flows.
  - Keep logs privacy-conscious and avoid secrets or unnecessary raw personal data.
- Price history ingestion
  - Retention/config requirement is recorded in `config/data_policy.json`.
  - Use `python -m etl.ingest_prices`.
  - Initial run backfills daily OHLCV from `2000-01-01`.
  - Later runs use `backfill_log.last_date` and fetch only missing rows.
  - Use `--force-full` only for explicit full refreshes.
- Index addition performance
  - Requirement/config is recorded in `config/data_policy.json`.
  - Saved addition events live in `index_additions`.
  - Watchlist and current index memberships are synced into `watchlist_symbols` and `index_memberships`.
  - Use `python -m etl.ingest_index_data` to refresh watchlist-wide index addition data.
  - Tab and API support ticker-only lookup against saved records and filter index choices by current ticker membership.
  - Tab and API support ad hoc ticker/index/date analysis as fallback.
  - Next: add broader official/source coverage for Dow/Russell and any current-index members whose historical add dates are unavailable from the first source pass.
