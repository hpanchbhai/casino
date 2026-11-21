"""Create or upgrade the local SQLite database for Stock Tracker."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "stocks.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent read performance
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    return {row["name"] for row in cur.execute(f"PRAGMA table_info({table_name})")}


def _add_column_if_missing(cur: sqlite3.Cursor, table_name: str, column_name: str, ddl: str) -> None:
    if column_name not in _table_columns(cur, table_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def _apply_compatibility_upgrades(cur: sqlite3.Cursor) -> None:
    """Add non-destructive columns when upgrading an older local DB."""
    _add_column_if_missing(cur, "daily_prices", "symbol_id", "symbol_id INTEGER")
    _add_column_if_missing(cur, "daily_prices", "source_id", "source_id INTEGER")
    _add_column_if_missing(
        cur,
        "daily_prices",
        "updated_at",
        "updated_at TEXT",
    )
    for table_name, columns in {
        "watchlist_symbols": [
            ("sector_key", "sector_key TEXT"),
            ("sector_name", "sector_name TEXT"),
            ("active", "active INTEGER NOT NULL DEFAULT 1"),
            ("removed_at", "removed_at TEXT"),
            ("updated_at", "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ],
        "index_memberships": [
            ("source_note", "source_note TEXT"),
            ("source_url", "source_url TEXT"),
            ("active", "active INTEGER NOT NULL DEFAULT 1"),
            ("updated_at", "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ],
        "index_additions": [
            ("source_url", "source_url TEXT"),
        ],
    }.items():
        for column_name, ddl in columns:
            _add_column_if_missing(cur, table_name, column_name, ddl)
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchlist_symbols_active
        ON watchlist_symbols (watchlist_id, active)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_index_memberships_symbol
        ON index_memberships (symbol_id, active)
        """
    )


def init():
    conn = get_conn()
    cur = conn.cursor()

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    cur.executescript(SCHEMA_PATH.read_text())
    _apply_compatibility_upgrades(cur)

    conn.commit()
    conn.close()
    print(f"✅  Database initialized at: {DB_PATH}")
    print(f"    Size: {DB_PATH.stat().st_size / 1024:.1f} KB" if DB_PATH.exists() else "")


if __name__ == "__main__":
    init()
