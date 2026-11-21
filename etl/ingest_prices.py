"""
Incremental daily OHLCV ingestion.

Initial run backfills from 2000-01-01 by default. Later runs fetch only the
missing range after each ticker's latest stored date in backfill_log.

Examples:
    python -m etl.ingest_prices --dry-run
    python -m etl.ingest_prices --tickers AAPL MSFT NVDA
    python -m etl.ingest_prices --universe all --max-tickers 25 --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
DATA_DIR = ROOT / "data"
STATIC_WATCHLIST = ROOT / "static" / "stocks_to_track"

DEFAULT_START_DATE = "2000-01-01"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.init_db import DB_PATH, get_conn, init as init_db  # noqa: E402


@dataclass(frozen=True)
class FetchPlan:
    ticker: str
    start_date: date
    end_date: date
    reason: str


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _normalize_ticker(value: str) -> str:
    return value.strip().upper()


def _read_ticker_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    tickers: list[str] = []
    for line in path.read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.lower() == "stock list:":
            continue
        tickers.append(_normalize_ticker(value))
    return tickers


def _load_watchlist_tickers() -> list[str]:
    tickers = _read_ticker_file(STATIC_WATCHLIST)

    watchlist_json = DATA_DIR / "watchlist.json"
    if watchlist_json.exists():
        import json

        data = json.loads(watchlist_json.read_text())
        for sector in data.get("sectors", []):
            for item in sector.get("tickers", []):
                ticker = item if isinstance(item, str) else item.get("ticker", "")
                if ticker:
                    tickers.append(_normalize_ticker(ticker))

    return sorted(set(tickers))


def _load_all_file_tickers() -> list[str]:
    tickers = _load_watchlist_tickers()
    for path in sorted(DATA_DIR.glob("*.txt")):
        tickers.extend(_read_ticker_file(path))
    return sorted(set(tickers))


def resolve_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        raw: list[str] = []
        for item in args.tickers:
            raw.extend(part for part in item.split(",") if part.strip())
        return sorted(set(_normalize_ticker(t) for t in raw))

    if args.universe == "all":
        tickers = _load_all_file_tickers()
    else:
        tickers = _load_watchlist_tickers()

    if args.max_tickers:
        tickers = tickers[: args.max_tickers]
    return tickers


def _safe_float(value) -> float | None:
    try:
        result = float(value)
        return round(result, 6) if result == result else None
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    try:
        result = float(value)
        if result != result:
            return None
        return int(result)
    except (TypeError, ValueError):
        return None


def ensure_symbol(conn: sqlite3.Connection, ticker: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO symbols (ticker, asset_type, active)
        VALUES (?, 'equity', 1)
        """,
        (ticker,),
    )
    row = conn.execute("SELECT id FROM symbols WHERE ticker = ?", (ticker,)).fetchone()
    return int(row["id"])


def get_source_id(conn: sqlite3.Connection, source_name: str = "yfinance") -> int | None:
    row = conn.execute("SELECT id FROM data_sources WHERE name = ?", (source_name,)).fetchone()
    return int(row["id"]) if row else None


def latest_stored_date(conn: sqlite3.Connection, ticker: str) -> date | None:
    row = conn.execute(
        """
        SELECT COALESCE(
            (SELECT last_date FROM backfill_log WHERE ticker = ?),
            (SELECT MAX(date) FROM daily_prices WHERE ticker = ?)
        ) AS last_date
        """,
        (ticker, ticker),
    ).fetchone()
    if not row or not row["last_date"]:
        return None
    return _parse_date(row["last_date"])


def build_fetch_plan(
    conn: sqlite3.Connection,
    tickers: Iterable[str],
    start_date: date,
    end_date: date,
    force_full: bool,
) -> list[FetchPlan]:
    plans: list[FetchPlan] = []

    for ticker in tickers:
        last_date = None if force_full else latest_stored_date(conn, ticker)
        if last_date:
            fetch_start = last_date + timedelta(days=1)
            reason = f"catch-up after {last_date.isoformat()}"
        else:
            fetch_start = start_date
            reason = "initial backfill"

        if fetch_start > end_date:
            continue
        plans.append(FetchPlan(ticker=ticker, start_date=fetch_start, end_date=end_date, reason=reason))

    return plans


def fetch_price_rows(plan: FetchPlan) -> list[tuple]:
    import pandas as pd
    import yfinance as yf

    end_exclusive = plan.end_date + timedelta(days=1)
    df = yf.download(
        plan.ticker,
        start=plan.start_date.isoformat(),
        end=end_exclusive.isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if df.empty:
        return []

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    rows: list[tuple] = []
    for ts, row in df.iterrows():
        price_date = ts.date()
        if price_date < plan.start_date or price_date > plan.end_date:
            continue
        rows.append(
            (
                price_date.isoformat(),
                _safe_float(row.get("Open")),
                _safe_float(row.get("High")),
                _safe_float(row.get("Low")),
                _safe_float(row.get("Close")),
                _safe_float(row.get("Adj Close", row.get("Close"))),
                _safe_int(row.get("Volume")),
            )
        )
    return rows


def insert_price_rows(
    conn: sqlite3.Connection,
    ticker: str,
    rows: list[tuple],
    source_id: int | None,
) -> int:
    if not rows:
        return 0

    symbol_id = ensure_symbol(conn, ticker)
    payload = [
        (
            symbol_id,
            ticker,
            price_date,
            open_price,
            high,
            low,
            close,
            adj_close,
            volume,
            source_id,
        )
        for price_date, open_price, high, low, close, adj_close, volume in rows
    ]

    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO daily_prices
            (symbol_id, ticker, date, open, high, low, close, adj_close, volume, source_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker, date) DO UPDATE SET
            symbol_id = excluded.symbol_id,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            adj_close = excluded.adj_close,
            volume = excluded.volume,
            source_id = excluded.source_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        payload,
    )

    dates = [row[0] for row in rows]
    conn.execute(
        """
        INSERT INTO backfill_log (ticker, first_date, last_date, row_count, last_updated)
        VALUES (
            ?,
            (SELECT MIN(date) FROM daily_prices WHERE ticker = ?),
            (SELECT MAX(date) FROM daily_prices WHERE ticker = ?),
            (SELECT COUNT(*) FROM daily_prices WHERE ticker = ?),
            datetime('now')
        )
        ON CONFLICT(ticker) DO UPDATE SET
            first_date = MIN(backfill_log.first_date, excluded.first_date),
            last_date = MAX(backfill_log.last_date, excluded.last_date),
            row_count = excluded.row_count,
            last_updated = excluded.last_updated
        """,
        (ticker, ticker, ticker, ticker),
    )
    return conn.total_changes - before


def mark_expired_plan_summary(conn: sqlite3.Connection, plans: list[FetchPlan]) -> None:
    total_tickers = conn.execute("SELECT COUNT(*) FROM backfill_log").fetchone()[0]
    print(f"Tracked tickers with backfill metadata: {total_tickers}")
    print(f"Tickers needing fetch: {len(plans)}")
    for plan in plans[:20]:
        print(
            f"  {plan.ticker}: {plan.start_date.isoformat()} -> "
            f"{plan.end_date.isoformat()} ({plan.reason})"
        )
    if len(plans) > 20:
        print(f"  ... and {len(plans) - 20} more")


def main() -> None:
    parser = argparse.ArgumentParser(description="Incrementally ingest daily OHLCV price data.")
    parser.add_argument("--universe", choices=["watchlist", "all"], default="watchlist")
    parser.add_argument("--tickers", nargs="*", help="Specific tickers, space or comma separated")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--force-full", action="store_true", help="Ignore backfill_log and fetch from start-date")
    parser.add_argument("--dry-run", action="store_true", help="Show fetch plan without downloading or writing")
    parser.add_argument("--max-tickers", type=int, help="Limit ticker count for test runs")
    parser.add_argument("--sleep", type=float, default=0.1, help="Seconds to sleep between ticker fetches")
    args = parser.parse_args()

    init_db()

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)
    if start_date > end_date:
        raise SystemExit("--start-date must be on or before --end-date")

    tickers = resolve_tickers(args)
    if not tickers:
        raise SystemExit("No tickers resolved for ingestion")

    conn = get_conn()
    try:
        plans = build_fetch_plan(conn, tickers, start_date, end_date, args.force_full)
        print(f"Resolved tickers: {len(tickers)}")
        mark_expired_plan_summary(conn, plans)

        if args.dry_run:
            print("Dry run complete. No data fetched or written.")
            return

        source_id = get_source_id(conn, "yfinance")
        started = time.time()
        total_rows = 0
        total_changes = 0

        for idx, plan in enumerate(plans, start=1):
            print(
                f"[{idx}/{len(plans)}] {plan.ticker}: fetching "
                f"{plan.start_date.isoformat()} -> {plan.end_date.isoformat()}"
            )
            rows = fetch_price_rows(plan)
            changes = insert_price_rows(conn, plan.ticker, rows, source_id)
            conn.commit()
            total_rows += len(rows)
            total_changes += changes
            print(f"  rows fetched: {len(rows)} | db changes: {changes}")
            if args.sleep and idx < len(plans):
                time.sleep(args.sleep)

        elapsed = time.time() - started
        print(f"Done. Rows fetched: {total_rows}. DB changes: {total_changes}. Elapsed: {elapsed:.1f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
