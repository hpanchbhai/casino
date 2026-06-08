"""
db/init_db.py
─────────────
Creates (or upgrades) the local SQLite database for the Stocks Dashboard.

Run once:
    .venv/bin/python3 db/init_db.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "stocks.db"


def get_conn() -> sqlite3.Connection:
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent read performance
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    conn = get_conn()
    cur = conn.cursor()

    # ── Daily OHLCV prices ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker    TEXT    NOT NULL,
            date      TEXT    NOT NULL,   -- ISO format: YYYY-MM-DD
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            adj_close REAL,
            volume    INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)

    # Composite index for fast range queries per ticker
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_dp_ticker_date
        ON daily_prices (ticker, date DESC)
    """)

    # ── Metadata / backfill tracking ────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backfill_log (
            ticker       TEXT    PRIMARY KEY,
            first_date   TEXT,          -- earliest date stored
            last_date    TEXT,          -- latest date stored
            row_count    INTEGER,
            last_updated TEXT           -- timestamp of last successful update
        )
    """)

    # ── DB version for future migrations ────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        INSERT OR IGNORE INTO db_meta (key, value) VALUES ('schema_version', '1')
    """)

    conn.commit()
    conn.close()
    print(f"✅  Database initialised at: {DB_PATH}")
    print(f"    Size: {DB_PATH.stat().st_size / 1024:.1f} KB" if DB_PATH.exists() else "")


if __name__ == "__main__":
    init()
