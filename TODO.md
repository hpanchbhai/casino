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
