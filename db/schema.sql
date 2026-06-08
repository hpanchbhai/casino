-- =============================================================
-- Bronze Layer — raw price data exactly as received from yfinance
-- =============================================================
-- Run this once to set up the table:
--   psql stocks < db/schema.sql

-- DROP TABLE IF EXISTS prices_raw;   ← uncomment only when you want a clean slate

CREATE TABLE IF NOT EXISTS prices_raw (

    -- ── Identity columns ──────────────────────────────────────
    ticker      TEXT        NOT NULL,
    -- TEXT vs VARCHAR(10): in Postgres, TEXT and VARCHAR are identical
    -- under the hood. TEXT is preferred — no arbitrary length limit to bike-shed.

    price_date  DATE        NOT NULL,
    -- DATE stores YYYY-MM-DD with no time component — perfect for daily OHLCV.
    -- TIMESTAMPTZ would add timezone awareness, but daily bars don't need it.
    -- yfinance returns dates in the index — we strip the time part on insert.

    -- ── OHLCV columns ─────────────────────────────────────────
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    -- NUMERIC(12,4) = up to 12 total digits, 4 after the decimal.
    -- Why NOT FLOAT? FLOAT is a binary approximation — 0.1 becomes
    -- 0.10000000000000001. For financial data, NUMERIC is exact.
    -- NUMERIC(12,4) handles prices up to $99,999,999.9999 — covers BRK.A.

    volume      BIGINT,
    -- BIGINT = 64-bit integer, range ±9.2 quintillion.
    -- Apple trades ~60M shares/day. INT (32-bit) maxes at ~2.1B — fine today,
    -- but BIGINT costs nothing extra and will never overflow.

    adj_close   NUMERIC(12,4),
    -- yfinance returns "Close" already split/dividend adjusted since ~2020,
    -- but we store adj_close explicitly so the column's intent is unambiguous.

    -- ── Audit column ──────────────────────────────────────────
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Records WHEN this row was written to our DB.
    -- TIMESTAMPTZ = timestamp WITH time zone — always stored in UTC internally,
    -- displayed in your local timezone. Better than plain TIMESTAMP for audit trails.

    -- ── Constraints ───────────────────────────────────────────
    CONSTRAINT prices_raw_pk PRIMARY KEY (ticker, price_date)
    -- Composite PK: one row per (ticker, date). Postgres automatically
    -- creates a unique B-tree index on (ticker, price_date) for free.
    -- This also prevents accidental duplicate inserts.
);

-- ── Index ─────────────────────────────────────────────────────
-- The PK index covers (ticker, price_date) — perfect for queries like:
--   WHERE ticker = 'AAPL' AND price_date >= '2023-01-01'
-- Postgres scans only AAPL's rows, not the entire table.
-- We'll add more indexes in later milestones when we need them.

-- Confirm the table was created:
\d prices_raw
