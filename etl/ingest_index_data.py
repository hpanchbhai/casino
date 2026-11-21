"""Ingest watchlist-backed index membership and index-addition events.

This keeps durable dashboard data in SQLite instead of config files:

    python -m etl.ingest_index_data --dry-run
    python -m etl.ingest_index_data
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.init_db import get_conn, init as init_db  # noqa: E402


INDEX_DEFS = {
    "sp500": {
        "name": "S&P 500",
        "benchmark": "SPY",
        "icon": "🏛️",
        "file": DATA_DIR / "sp500.txt",
        "source_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    },
    "nasdaq100": {
        "name": "NASDAQ 100",
        "benchmark": "QQQ",
        "icon": "💻",
        "file": DATA_DIR / "nasdaq100.txt",
        "source_url": "https://en.wikipedia.org/wiki/Nasdaq-100",
    },
    "dowjones": {
        "name": "Dow Jones",
        "benchmark": "DIA",
        "icon": "🏭",
        "file": DATA_DIR / "dowjones.txt",
        "source_url": None,
    },
    "russell2000": {
        "name": "Russell 2000",
        "benchmark": "IWM",
        "icon": "📊",
        "file": DATA_DIR / "russell2000.txt",
        "source_url": None,
    },
}


@dataclass(frozen=True)
class WatchlistEntry:
    ticker: str
    sector_key: str | None
    sector_name: str | None


def normalize_ticker(value: str) -> str:
    return value.strip().upper().replace(".", "-")


def read_watchlist_entries() -> list[WatchlistEntry]:
    path = DATA_DIR / "watchlist.json"
    data = json.loads(path.read_text())
    seen: set[str] = set()
    entries: list[WatchlistEntry] = []
    for sector in data.get("sectors", []):
        for raw_ticker in sector.get("tickers", []):
            ticker = normalize_ticker(str(raw_ticker))
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            entries.append(
                WatchlistEntry(
                    ticker=ticker,
                    sector_key=sector.get("key"),
                    sector_name=sector.get("name"),
                )
            )
    return entries


def read_ticker_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    tickers: list[str] = []
    for line in path.read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.lower() == "stock list:":
            continue
        tickers.append(normalize_ticker(value))
    return sorted(set(tickers))


def fetch_source_tables(source_url: str) -> list[pd.DataFrame]:
    response = requests.get(
        source_url,
        headers={"User-Agent": "stock-tracker/0.1 index-data-ingestion"},
        timeout=30,
    )
    response.raise_for_status()
    return [flatten_columns(table) for table in pd.read_html(io.StringIO(response.text))]


def _ticker_column_candidates(df: pd.DataFrame) -> list[str]:
    candidates = []
    for column in df.columns:
        low = column.lower()
        if any(key in low for key in ("ticker", "symbol")):
            candidates.append(column)
    return candidates


def current_tickers_from_source(index_key: str) -> list[str]:
    source_url = INDEX_DEFS[index_key].get("source_url")
    if not source_url:
        return []

    best: set[str] = set()
    for df in fetch_source_tables(source_url):
        if df.empty:
            continue
        # Historical change tables also contain ticker columns, but current
        # constituent tables are much larger and usually do not lead with dates.
        dateish_columns = [col for col in df.columns if "date" in col.lower()]
        for column in _ticker_column_candidates(df):
            values = set()
            for value in df[column].dropna().tolist():
                values.update(extract_any_tickers(value))
            if len(values) > len(best) and len(values) >= 20:
                if len(dateish_columns) and len(values) < 200:
                    continue
                best = values
    return sorted(best)


def sync_index_definitions(conn: sqlite3.Connection, dry_run: bool) -> int:
    touched = 0
    for index_key, meta in INDEX_DEFS.items():
        touched += 1
        if dry_run:
            continue
        source_url = meta.get("source_url")
        membership_source = "source_url" if source_url else "local_file"
        conn.execute(
            """
            INSERT INTO index_definitions (
                key, name, benchmark_ticker, icon, source_url, membership_source, active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                benchmark_ticker = excluded.benchmark_ticker,
                icon = excluded.icon,
                source_url = excluded.source_url,
                membership_source = excluded.membership_source,
                active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                index_key,
                meta["name"],
                meta["benchmark"],
                meta.get("icon"),
                source_url,
                membership_source,
            ),
        )
    return touched


def ensure_symbol(conn: sqlite3.Connection, ticker: str) -> int:
    conn.execute(
        """
        INSERT INTO symbols (ticker, asset_type, active, updated_at)
        VALUES (?, 'equity', 1, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            active = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (ticker,),
    )
    return int(conn.execute("SELECT id FROM symbols WHERE ticker = ?", (ticker,)).fetchone()["id"])


def ensure_watchlist(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO watchlists (key, name)
        VALUES ('default', 'My Watchlist')
        ON CONFLICT(key) DO UPDATE SET name = excluded.name
        """
    )
    return int(conn.execute("SELECT id FROM watchlists WHERE key = 'default'").fetchone()["id"])


def sync_watchlist(conn: sqlite3.Connection, entries: list[WatchlistEntry], dry_run: bool) -> tuple[int, int]:
    watchlist_id = ensure_watchlist(conn)
    desired = {entry.ticker: entry for entry in entries}
    existing = conn.execute(
        """
        SELECT s.ticker, s.id AS symbol_id, ws.active
        FROM watchlist_symbols ws
        JOIN symbols s ON s.id = ws.symbol_id
        WHERE ws.watchlist_id = ?
        """,
        (watchlist_id,),
    ).fetchall()
    existing_by_ticker = {row["ticker"]: row for row in existing}

    adds = 0
    removes = 0
    for ticker, entry in desired.items():
        symbol_id = ensure_symbol(conn, ticker)
        prior = existing_by_ticker.get(ticker)
        if not prior or not prior["active"]:
            adds += 1
        if dry_run:
            continue
        conn.execute(
            """
            INSERT INTO watchlist_symbols (
                watchlist_id, symbol_id, sector_key, sector_name, active, removed_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(watchlist_id, symbol_id) DO UPDATE SET
                sector_key = excluded.sector_key,
                sector_name = excluded.sector_name,
                active = 1,
                removed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (watchlist_id, symbol_id, entry.sector_key, entry.sector_name),
        )
        if not prior or not prior["active"]:
            conn.execute(
                """
                INSERT INTO watchlist_symbol_events
                    (watchlist_id, symbol_id, ticker, action, sector_key, sector_name, source)
                VALUES (?, ?, ?, 'sync', ?, ?, 'etl.ingest_index_data')
                """,
                (watchlist_id, symbol_id, ticker, entry.sector_key, entry.sector_name),
            )

    for ticker, row in existing_by_ticker.items():
        if ticker in desired or not row["active"]:
            continue
        removes += 1
        if dry_run:
            continue
        conn.execute(
            """
            UPDATE watchlist_symbols
            SET active = 0,
                removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE watchlist_id = ? AND symbol_id = ?
            """,
            (watchlist_id, row["symbol_id"]),
        )
        conn.execute(
            """
            INSERT INTO watchlist_symbol_events
                (watchlist_id, symbol_id, ticker, action, source)
            VALUES (?, ?, ?, 'remove', 'etl.ingest_index_data')
            """,
            (watchlist_id, row["symbol_id"], ticker),
        )
    if not dry_run:
        conn.execute(
            """
            INSERT INTO watchlist_symbol_events
                (watchlist_id, symbol_id, ticker, action, sector_key, sector_name, source)
            SELECT ws.watchlist_id,
                   ws.symbol_id,
                   s.ticker,
                   'sync',
                   ws.sector_key,
                   ws.sector_name,
                   'etl.ingest_index_data.bootstrap'
            FROM watchlist_symbols ws
            JOIN symbols s ON s.id = ws.symbol_id
            WHERE ws.watchlist_id = ?
              AND ws.active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM watchlist_symbol_events e
                  WHERE e.watchlist_id = ws.watchlist_id
                    AND e.symbol_id = ws.symbol_id
              )
            """,
            (watchlist_id,),
        )
    return adds, removes


def sync_index_memberships(conn: sqlite3.Connection, dry_run: bool) -> int:
    changed = 0
    for index_key, meta in INDEX_DEFS.items():
        tickers = current_tickers_from_source(index_key) or read_ticker_file(meta["file"])
        if not tickers:
            continue
        source_note = "source current constituent table" if meta.get("source_url") else "local index membership snapshot"
        if not dry_run:
            conn.execute(
                """
                UPDATE index_memberships
                SET active = 0,
                    effective_to = COALESCE(effective_to, date('now')),
                    updated_at = CURRENT_TIMESTAMP
                WHERE index_key = ? AND active = 1
                """,
                (index_key,),
            )
        for ticker in tickers:
            symbol_id = ensure_symbol(conn, ticker)
            changed += 1
            if dry_run:
                continue
            conn.execute(
                """
                INSERT INTO index_memberships (
                    index_key, symbol_id, effective_from, effective_to, source_note, source_url, active, updated_at
                )
                VALUES (?, ?, 'current_snapshot', NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(index_key, symbol_id, effective_from) DO UPDATE SET
                    effective_to = NULL,
                    source_note = excluded.source_note,
                    source_url = excluded.source_url,
                    active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (index_key, symbol_id, source_note, meta.get("source_url")),
            )
    return changed


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join(str(part) for part in col if str(part) != "nan") for col in df.columns]
    else:
        df.columns = [str(col) for col in df.columns]
    df.columns = [re.sub(r"\s+", " ", col).strip() for col in df.columns]
    return df


def parse_date(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def extract_tickers(value, watchlist: set[str]) -> set[str]:
    text = normalize_ticker(str(value or ""))
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9-]{0,5}\b", text))
    return tokens & watchlist


def extract_any_tickers(value) -> set[str]:
    text = normalize_ticker(str(value or ""))
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9-]{0,5}\b", text))
    noise = {"NAN", "NONE", "THE", "AND", "INC", "CORP", "CLASS", "PLC"}
    return {token for token in tokens if token not in noise}


def ticker_columns(df: pd.DataFrame) -> list[str]:
    columns = []
    for column in df.columns:
        low = column.lower()
        if "removed" in low or "leaving" in low:
            continue
        if any(key in low for key in ("symbol", "ticker", "added", "joining")):
            columns.append(column)
    return columns


def change_columns(df: pd.DataFrame, action: str) -> list[str]:
    columns = []
    ticker_columns_for_action = []
    for column in df.columns:
        low = column.lower()
        if "date" in low:
            continue
        if action == "add" and any(key in low for key in ("added", "joining", "addition")):
            columns.append(column)
            if "ticker" in low or "symbol" in low:
                ticker_columns_for_action.append(column)
        elif action == "remove" and any(key in low for key in ("removed", "leaving", "deletion", "deleted")):
            columns.append(column)
            if "ticker" in low or "symbol" in low:
                ticker_columns_for_action.append(column)
    return ticker_columns_for_action or columns


def date_columns(df: pd.DataFrame) -> list[str]:
    return [column for column in df.columns if "date" in column.lower()]


def quarter_for_date(value: str) -> str:
    parsed = datetime.fromisoformat(value).date()
    quarter = (parsed.month - 1) // 3 + 1
    return f"{parsed.year}-Q{quarter}"


def insert_constituent_change(
    conn: sqlite3.Connection,
    ticker: str,
    index_key: str,
    action: str,
    effective_date: str,
    source_url: str,
    dry_run: bool,
) -> None:
    meta = INDEX_DEFS[index_key]
    symbol_id = ensure_symbol(conn, ticker)
    if dry_run:
        return
    conn.execute(
        """
        INSERT INTO index_constituent_changes (
            index_key,
            index_name,
            ticker,
            symbol_id,
            action,
            effective_date,
            quarter,
            source_note,
            source_url,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(index_key, ticker, action, effective_date) DO UPDATE SET
            symbol_id = excluded.symbol_id,
            index_name = excluded.index_name,
            quarter = excluded.quarter,
            source_note = excluded.source_note,
            source_url = excluded.source_url,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            index_key,
            meta["name"],
            ticker,
            symbol_id,
            action,
            effective_date,
            quarter_for_date(effective_date),
            f"{meta['name']} source table",
            source_url,
        ),
    )


def ingest_source_events(
    conn: sqlite3.Connection,
    index_key: str,
    watchlist: set[str],
    dry_run: bool,
) -> int:
    meta = INDEX_DEFS[index_key]
    source_url = meta.get("source_url")
    if not source_url:
        return 0

    tables = fetch_source_tables(source_url)
    inserted = 0
    for df in tables:
        dcols = date_columns(df)
        tcols = ticker_columns(df)
        added_cols = change_columns(df, "add") or tcols
        removed_cols = change_columns(df, "remove")
        if not dcols or not tcols:
            continue
        for _, row in df.iterrows():
            added_date = None
            for column in dcols:
                added_date = parse_date(row.get(column))
                if added_date:
                    break
            if not added_date:
                continue

            added_tickers: set[str] = set()
            for column in added_cols:
                added_tickers.update(extract_any_tickers(row.get(column)))
            removed_tickers: set[str] = set()
            for column in removed_cols:
                removed_tickers.update(extract_any_tickers(row.get(column)))

            for ticker in added_tickers:
                inserted += 1
                insert_constituent_change(conn, ticker, index_key, "add", added_date, source_url, dry_run)

            for ticker in added_tickers:
                symbol_id = ensure_symbol(conn, ticker)
                if dry_run:
                    continue
                conn.execute(
                    """
                    INSERT INTO index_additions (
                        symbol_id,
                        ticker,
                        index_key,
                        index_name,
                        benchmark_ticker,
                        added_date,
                        source_note,
                        source_url,
                        active,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(ticker, index_key, added_date) DO UPDATE SET
                        symbol_id = excluded.symbol_id,
                        index_name = excluded.index_name,
                        benchmark_ticker = excluded.benchmark_ticker,
                        source_note = excluded.source_note,
                        source_url = excluded.source_url,
                        active = 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        symbol_id,
                        ticker,
                        index_key,
                        meta["name"],
                        meta["benchmark"],
                        added_date,
                        f"{meta['name']} source table",
                        source_url,
                    ),
                )
            for ticker in removed_tickers:
                inserted += 1
                insert_constituent_change(conn, ticker, index_key, "remove", added_date, source_url, dry_run)
    return inserted


def run_ingestion(dry_run: bool, remote: bool) -> None:
    init_db()
    entries = read_watchlist_entries()
    watchlist = {entry.ticker for entry in entries}
    print(f"Watchlist tickers: {len(watchlist)}")

    conn = get_conn()
    try:
        definitions = sync_index_definitions(conn, dry_run)
        adds, removes = sync_watchlist(conn, entries, dry_run)
        memberships = sync_index_memberships(conn, dry_run)
        print(f"Index definitions touched: {definitions}")
        print(f"Watchlist DB sync: {adds} adds/reactivations, {removes} removals")
        print(f"Current index membership rows touched: {memberships}")

        remote_events = 0
        if remote:
            for index_key in ("sp500", "nasdaq100"):
                count = ingest_source_events(conn, index_key, watchlist, dry_run)
                remote_events += count
                print(f"{INDEX_DEFS[index_key]['name']} source events matched: {count}")

        if dry_run:
            print("Dry run complete. No DB changes committed.")
            conn.rollback()
        else:
            conn.commit()
            print(f"Committed. Remote event rows touched: {remote_events}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync watchlist/index membership and index addition events.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-remote", action="store_true", help="Skip source-page event ingestion")
    args = parser.parse_args()
    started = datetime.now()
    run_ingestion(dry_run=args.dry_run, remote=not args.skip_remote)
    print(f"Elapsed: {(datetime.now() - started).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
