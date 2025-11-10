-- db/bootstrap.sql
-- Idempotent bootstrap for Stock Tracker (Postgres 12+)

BEGIN;

-- 1) Core table: tickers
CREATE TABLE IF NOT EXISTS tickers (
  id           BIGSERIAL PRIMARY KEY,
  symbol       TEXT NOT NULL UNIQUE,
  name         TEXT,
  sector       TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) OHLCV bars: price_bars
CREATE TABLE IF NOT EXISTS price_bars (
  id              BIGSERIAL PRIMARY KEY,
  ticker_id       BIGINT NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  date            DATE   NOT NULL,
  open            NUMERIC(18,6),
  high            NUMERIC(18,6),
  low             NUMERIC(18,6),
  close           NUMERIC(18,6),
  adjusted_close  NUMERIC(18,6),
  volume          BIGINT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- (Optional) ensure columns exist if table pre-existed (no-op if already present)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='price_bars' AND column_name='adjusted_close'
  ) THEN
    ALTER TABLE price_bars ADD COLUMN adjusted_close NUMERIC(18,6);
  END IF;
END $$;

-- 2a) Remove duplicates BEFORE adding unique constraint (safe to re-run)
-- Keep the highest id row per (ticker_id, date)
WITH d AS (
  SELECT ticker_id, date, MAX(id) AS keep_id
  FROM price_bars
  GROUP BY ticker_id, date
),
dups AS (
  SELECT pb.id
  FROM price_bars pb
  JOIN d ON d.ticker_id = pb.ticker_id AND d.date = pb.date
  WHERE pb.id <> d.keep_id
)
DELETE FROM price_bars
WHERE id IN (SELECT id FROM dups);

-- 2b) Add unique constraint for upsert target: (ticker_id, date)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_price_bars_ticker_date'
      AND conrelid = 'price_bars'::regclass
  ) THEN
    ALTER TABLE price_bars
      ADD CONSTRAINT uq_price_bars_ticker_date UNIQUE (ticker_id, date);
  END IF;
END $$;

-- 2c) Helpful indexes
CREATE INDEX IF NOT EXISTS idx_price_bars_ticker_date
  ON price_bars (ticker_id, date);
CREATE INDEX IF NOT EXISTS idx_price_bars_date
  ON price_bars (date);
CREATE INDEX IF NOT EXISTS idx_price_bars_ticker
  ON price_bars (ticker_id);

-- 3) Options flow table (minimal but practical)
CREATE TABLE IF NOT EXISTS option_flow (
  id              BIGSERIAL PRIMARY KEY,
  ticker_id       BIGINT NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  trade_date      DATE   NOT NULL,
  option_symbol   TEXT,                -- vendor’s option code (if available)
  direction       TEXT,                -- 'CALL' or 'PUT'
  expiry          DATE,
  strike          NUMERIC(18,6),
  option_type     TEXT,                -- 'C' or 'P' (if you prefer compact)
  side            TEXT,                -- 'BUY', 'SELL', or NULL if unknown
  underlying_px   NUMERIC(18,6),
  volume          INTEGER,
  open_interest   INTEGER,
  premium_usd     NUMERIC(18,6),
  source          TEXT,                -- e.g., 'yfinance','tradier'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Optional: lightweight checks (only added if not present)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='option_flow' AND column_name='direction'
  ) THEN
    ALTER TABLE option_flow ADD COLUMN direction TEXT;
  END IF;
END $$;

-- 3a) Helpful indexes for option_flow
CREATE INDEX IF NOT EXISTS idx_option_flow_ticker_date
  ON option_flow (ticker_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_option_flow_symbol_date
  ON option_flow (ticker_id, expiry, strike, trade_date);
CREATE INDEX IF NOT EXISTS idx_option_flow_direction_date
  ON option_flow (direction, trade_date);

COMMIT;

-- Sanity prints (optional; visible in psql)
-- SELECT 'tickers rows', COUNT(*) FROM tickers;
-- SELECT 'price_bars rows', COUNT(*) FROM price_bars;
-- SELECT 'option_flow rows', COUNT(*) FROM option_flow;

