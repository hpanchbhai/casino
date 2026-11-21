-- Stock Tracker durable dashboard schema.
-- SQLite-compatible. Applied by db/init_db.py.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Core reference data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS db_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbols (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL UNIQUE,
    name       TEXT,
    exchange   TEXT,
    sector     TEXT,
    industry   TEXT,
    asset_type TEXT NOT NULL DEFAULT 'equity',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_symbols_active_ticker
ON symbols (active, ticker);

CREATE TABLE IF NOT EXISTS data_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    base_url    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS index_definitions (
    key               TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    benchmark_ticker  TEXT,
    icon              TEXT,
    source_url        TEXT,
    membership_source TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_index_definitions_active_name
ON index_definitions (active, name);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    job_name      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    started_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   TEXT,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated  INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_job_started
ON ingestion_runs (job_name, started_at DESC);

-- ---------------------------------------------------------------------------
-- Price history and dashboard calculations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
    ticker    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,
    volume    INTEGER,
    source_id INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol_date
ON daily_prices (symbol_id, date DESC);

CREATE INDEX IF NOT EXISTS idx_daily_prices_ticker_date
ON daily_prices (ticker, date DESC);

CREATE TABLE IF NOT EXISTS backfill_log (
    ticker       TEXT PRIMARY KEY,
    first_date   TEXT,
    last_date    TEXT,
    row_count    INTEGER,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS technical_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id    INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    rsi_14       REAL,
    price        REAL,
    change_pct   REAL,
    source_id    INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS watchlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist_symbols (
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    symbol_id    INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    sector_key   TEXT,
    sector_name  TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    added_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    removed_at   TEXT,
    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (watchlist_id, symbol_id)
);

CREATE TABLE IF NOT EXISTS watchlist_symbol_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    symbol_id    INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ticker       TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('add', 'remove', 'sync')),
    sector_key   TEXT,
    sector_name  TEXT,
    source       TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_watchlist_symbol_events_ticker
ON watchlist_symbol_events (ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS index_memberships (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    index_key     TEXT NOT NULL,
    symbol_id     INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    effective_from TEXT,
    effective_to   TEXT,
    source_id      INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    source_note    TEXT,
    source_url     TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (index_key, symbol_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_index_memberships_index
ON index_memberships (index_key, effective_to);

CREATE TABLE IF NOT EXISTS index_additions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id        INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    ticker           TEXT NOT NULL,
    index_key        TEXT NOT NULL,
    index_name       TEXT NOT NULL,
    benchmark_ticker TEXT,
    added_date       TEXT NOT NULL,
    removed_date     TEXT,
    source_note      TEXT,
    source_url       TEXT,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, index_key, added_date)
);

CREATE INDEX IF NOT EXISTS idx_index_additions_index_date
ON index_additions (index_key, added_date DESC);

CREATE INDEX IF NOT EXISTS idx_index_additions_ticker
ON index_additions (ticker, added_date DESC);

CREATE TABLE IF NOT EXISTS index_constituent_changes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    index_key        TEXT NOT NULL,
    index_name       TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    symbol_id        INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    action           TEXT NOT NULL CHECK (action IN ('add', 'remove')),
    effective_date   TEXT NOT NULL,
    quarter          TEXT NOT NULL,
    source_note      TEXT,
    source_url       TEXT,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (index_key, ticker, action, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_index_constituent_changes_period
ON index_constituent_changes (index_key, effective_date DESC, action);

CREATE INDEX IF NOT EXISTS idx_index_constituent_changes_ticker
ON index_constituent_changes (ticker, effective_date DESC);

-- ---------------------------------------------------------------------------
-- 13F smart-money / whale data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS filers_13f (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    whale_key     TEXT NOT NULL UNIQUE,
    cik           TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    fund_name     TEXT,
    icon          TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_filers_13f_active_order
ON filers_13f (active, display_order);

CREATE TABLE IF NOT EXISTS filings_13f (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_id        INTEGER NOT NULL REFERENCES filers_13f(id) ON DELETE CASCADE,
    accession       TEXT NOT NULL UNIQUE,
    report_date     TEXT NOT NULL,
    filed_date      TEXT,
    form_type       TEXT NOT NULL DEFAULT '13F-HR',
    source_url      TEXT,
    portfolio_value REAL,
    positions_count INTEGER,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (filer_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_filings_13f_filer_report
ON filings_13f (filer_id, report_date DESC);

CREATE TABLE IF NOT EXISTS holdings_13f (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id     INTEGER NOT NULL REFERENCES filings_13f(id) ON DELETE CASCADE,
    symbol_id     INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    ticker        TEXT NOT NULL,
    cusip         TEXT,
    issuer_name   TEXT,
    shares        REAL,
    value_k       REAL,
    pct_portfolio REAL,
    put_call      TEXT,
    investment_discretion TEXT,
    voting_authority_json TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (filing_id, ticker, cusip, put_call)
);

CREATE INDEX IF NOT EXISTS idx_holdings_13f_symbol
ON holdings_13f (symbol_id);

CREATE INDEX IF NOT EXISTS idx_holdings_13f_ticker
ON holdings_13f (ticker);

CREATE TABLE IF NOT EXISTS holding_changes_13f (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_id           INTEGER NOT NULL REFERENCES filers_13f(id) ON DELETE CASCADE,
    symbol_id          INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    ticker             TEXT NOT NULL,
    current_filing_id  INTEGER NOT NULL REFERENCES filings_13f(id) ON DELETE CASCADE,
    previous_filing_id INTEGER REFERENCES filings_13f(id) ON DELETE SET NULL,
    action             TEXT NOT NULL,
    direction          TEXT NOT NULL CHECK (direction IN ('buy', 'sell', 'neutral')),
    shares             REAL,
    previous_shares    REAL,
    shares_change      REAL,
    value_k            REAL,
    previous_value_k   REAL,
    value_change_k     REAL,
    pct_change         REAL,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (filer_id, ticker, current_filing_id)
);

CREATE INDEX IF NOT EXISTS idx_holding_changes_13f_ticker
ON holding_changes_13f (ticker, direction);

CREATE INDEX IF NOT EXISTS idx_holding_changes_13f_filer
ON holding_changes_13f (filer_id, current_filing_id);

CREATE TABLE IF NOT EXISTS consensus_snapshots_13f (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date      TEXT NOT NULL,
    generated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source           TEXT NOT NULL DEFAULT 'db',
    filers_count     INTEGER NOT NULL DEFAULT 0,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    UNIQUE (report_date, generated_at)
);

CREATE INDEX IF NOT EXISTS idx_consensus_snapshots_13f_report
ON consensus_snapshots_13f (report_date DESC, generated_at DESC);

CREATE TABLE IF NOT EXISTS consensus_items_13f (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id   INTEGER NOT NULL REFERENCES consensus_snapshots_13f(id) ON DELETE CASCADE,
    symbol_id     INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    ticker        TEXT NOT NULL,
    name          TEXT,
    net_signal    TEXT NOT NULL CHECK (
        net_signal IN ('strong_buy', 'buy', 'mixed', 'sell', 'strong_sell')
    ),
    conviction    INTEGER NOT NULL DEFAULT 0,
    buyers_count  INTEGER NOT NULL DEFAULT 0,
    sellers_count INTEGER NOT NULL DEFAULT 0,
    total_whales  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_consensus_items_13f_signal
ON consensus_items_13f (net_signal, total_whales DESC);

CREATE TABLE IF NOT EXISTS consensus_item_filers_13f (
    consensus_item_id INTEGER NOT NULL REFERENCES consensus_items_13f(id) ON DELETE CASCADE,
    filer_id          INTEGER NOT NULL REFERENCES filers_13f(id) ON DELETE CASCADE,
    holding_change_id INTEGER REFERENCES holding_changes_13f(id) ON DELETE SET NULL,
    side              TEXT NOT NULL CHECK (side IN ('buyer', 'seller')),
    PRIMARY KEY (consensus_item_id, filer_id, side)
);

-- ---------------------------------------------------------------------------
-- Cumulative options data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS option_contracts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id         INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    underlying_ticker TEXT NOT NULL,
    contract_symbol   TEXT NOT NULL UNIQUE,
    option_type       TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    expiration_date   TEXT NOT NULL,
    strike            REAL NOT NULL,
    contract_size     TEXT,
    currency          TEXT,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'expired', 'delisted', 'unknown')
    ),
    first_seen_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at      TEXT,
    expired_at        TEXT,
    source_id         INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_option_contracts_underlying_expiry
ON option_contracts (underlying_ticker, expiration_date, option_type, strike);

CREATE INDEX IF NOT EXISTS idx_option_contracts_status_expiry
ON option_contracts (status, expiration_date);

CREATE TABLE IF NOT EXISTS option_chain_fetches (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying_ticker  TEXT NOT NULL,
    expiration_date    TEXT,
    fetched_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    underlying_price   REAL,
    source_id          INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    ingestion_run_id   INTEGER REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    contracts_seen     INTEGER NOT NULL DEFAULT 0,
    metadata_json      TEXT,
    UNIQUE (underlying_ticker, expiration_date, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_option_chain_fetches_underlying
ON option_chain_fetches (underlying_ticker, fetched_at DESC);

CREATE TABLE IF NOT EXISTS option_quotes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id       INTEGER NOT NULL REFERENCES option_contracts(id) ON DELETE CASCADE,
    fetch_id          INTEGER REFERENCES option_chain_fetches(id) ON DELETE SET NULL,
    quoted_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    underlying_price  REAL,
    last_trade_at     TEXT,
    last_price        REAL,
    bid               REAL,
    ask               REAL,
    mid               REAL,
    spread            REAL,
    spread_pct        REAL,
    change            REAL,
    percent_change    REAL,
    volume            INTEGER,
    open_interest     INTEGER,
    implied_volatility REAL,
    delta             REAL,
    gamma             REAL,
    theta             REAL,
    vega              REAL,
    rho               REAL,
    intrinsic_value   REAL,
    extrinsic_value   REAL,
    moneyness         REAL,
    in_the_money      INTEGER,
    source_id         INTEGER REFERENCES data_sources(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (contract_id, quoted_at)
);

CREATE INDEX IF NOT EXISTS idx_option_quotes_contract_time
ON option_quotes (contract_id, quoted_at DESC);

CREATE INDEX IF NOT EXISTS idx_option_quotes_activity
ON option_quotes (quoted_at DESC, volume DESC, open_interest DESC);

CREATE VIEW IF NOT EXISTS latest_option_quotes AS
SELECT oq.*
FROM option_quotes oq
JOIN (
    SELECT contract_id, MAX(quoted_at) AS latest_quoted_at
    FROM option_quotes
    GROUP BY contract_id
) latest
    ON latest.contract_id = oq.contract_id
   AND latest.latest_quoted_at = oq.quoted_at;

-- ---------------------------------------------------------------------------
-- Dashboard usage analytics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dashboard_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key     TEXT NOT NULL UNIQUE,
    first_seen_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_agent      TEXT,
    ip_hash         TEXT,
    referrer        TEXT,
    entry_path      TEXT,
    metadata_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_seen
ON dashboard_sessions (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_interaction_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER REFERENCES dashboard_sessions(id) ON DELETE SET NULL,
    session_key       TEXT,
    event_name        TEXT NOT NULL,
    event_type        TEXT,
    page              TEXT,
    component         TEXT,
    action            TEXT,
    ticker            TEXT,
    route             TEXT,
    client_timestamp  TEXT,
    server_timestamp  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json     TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_time
ON dashboard_interaction_events (server_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_name_time
ON dashboard_interaction_events (event_name, server_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_ticker_time
ON dashboard_interaction_events (ticker, server_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_session
ON dashboard_interaction_events (session_id, server_timestamp);

-- Seed source labels used by the first ingestion jobs.
INSERT OR REPLACE INTO db_meta (key, value, updated_at)
VALUES ('schema_version', '5', CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO data_sources (name, base_url, description)
VALUES
    ('yfinance', 'https://query1.finance.yahoo.com', 'Market price, earnings, and options data fetched through yfinance'),
    ('sec_13f', 'https://www.sec.gov', 'SEC EDGAR 13F filings and information tables');
