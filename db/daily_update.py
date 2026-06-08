"""
db/daily_update.py
──────────────────
Daily updater — runs every market day (ideally after 4:30 PM ET):
  1. Deletes rows older than today − 10 years  (rolling window)
  2. Fetches today's OHLCV for all tracked tickers
  3. Inserts new rows

Automate with launchd (see db/com.stocksdashboard.daily_update.plist) or cron.

Manual run:
    .venv/bin/python3 db/daily_update.py

Options:
    --dry-run    show what would happen, don't write to DB
    --date YYYY-MM-DD   pretend today is this date (for testing)
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

# ── Setup ──────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DB_PATH   = Path(__file__).parent / "stocks.db"
WATCHLIST = ROOT / "data" / "watchlist.json"
LOG_FILE  = Path(__file__).parent / "update.log"

WINDOW_YEARS = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daily_update")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def load_tickers() -> list[str]:
    with open(WATCHLIST) as f:
        data = json.load(f)
    tickers = []
    for sector in data.get("sectors", []):
        for item in sector.get("tickers", []):
            ticker = item if isinstance(item, str) else item.get("ticker", "")
            if ticker:
                tickers.append(ticker.upper())
    return sorted(set(tickers))


def cutoff_date(today: date) -> date:
    """Oldest date to keep = today minus 10 years."""
    return today.replace(year=today.year - WINDOW_YEARS)


def prune_old_rows(conn: sqlite3.Connection, today: date, dry_run: bool) -> int:
    """Delete rows older than the rolling window. Returns number of rows deleted."""
    cutoff = cutoff_date(today).isoformat()
    count = conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE date < ?", (cutoff,)
    ).fetchone()[0]

    if count > 0:
        log.info(f"🗑️   Pruning {count:,} rows older than {cutoff}")
        if not dry_run:
            conn.execute("DELETE FROM daily_prices WHERE date < ?", (cutoff,))
    else:
        log.info(f"✅  Nothing to prune (cutoff: {cutoff})")

    return count


def fetch_todays_data(tickers: list[str], today: date) -> dict[str, list]:
    """Batch-download today's OHLCV for all tickers. Returns dict ticker → rows."""
    log.info(f"📡  Fetching data for {len(tickers)} tickers (date: {today})")

    # yfinance batch download — fetch last 5 days to handle weekends/holidays,
    # then we'll only keep rows matching `today`
    start = (today - timedelta(days=5)).isoformat()
    end   = (today + timedelta(days=1)).isoformat()

    try:
        raw = yf.download(
            tickers=tickers,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception as e:
        log.error(f"❌  yfinance batch download failed: {e}")
        return {}

    result: dict[str, list] = {}

    if len(tickers) == 1:
        ticker = tickers[0]
        df = raw
        if df.empty:
            return result
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        rows = []
        for ts, row in df.iterrows():
            d_str = ts.strftime("%Y-%m-%d")
            rows.append((
                ticker, d_str,
                _f(row.get("Open")),
                _f(row.get("High")),
                _f(row.get("Low")),
                _f(row.get("Close")),
                _f(row.get("Adj Close", row.get("Close"))),
                _i(row.get("Volume")),
            ))
        result[ticker] = rows
        return result

    for ticker in tickers:
        try:
            df = raw[ticker] if ticker in raw.columns.get_level_values(0) else None
            if df is None or df.empty:
                continue
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            rows = []
            for ts, row in df.iterrows():
                d_str = ts.strftime("%Y-%m-%d")
                rows.append((
                    ticker, d_str,
                    _f(row.get("Open")),
                    _f(row.get("High")),
                    _f(row.get("Low")),
                    _f(row.get("Close")),
                    _f(row.get("Adj Close", row.get("Close"))),
                    _i(row.get("Volume")),
                ))
            if rows:
                result[ticker] = rows
        except Exception as e:
            log.warning(f"  ⚠️  {ticker}: {e}")

    return result


def _f(val) -> float | None:
    """Safe float conversion."""
    try:
        v = float(val)
        return round(v, 4) if v == v else None   # NaN check
    except (TypeError, ValueError):
        return None


def _i(val) -> int | None:
    """Safe int conversion."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def insert_rows(conn: sqlite3.Connection, ticker_rows: dict[str, list], dry_run: bool) -> int:
    """Insert new OHLCV rows. Returns total rows inserted."""
    all_rows = []
    for rows in ticker_rows.values():
        all_rows.extend(rows)

    if not all_rows:
        log.warning("⚠️  No rows to insert")
        return 0

    if not dry_run:
        conn.executemany("""
            INSERT OR REPLACE INTO daily_prices
                (ticker, date, open, high, low, close, adj_close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, all_rows)

        # Update backfill_log for each ticker
        for ticker, rows in ticker_rows.items():
            if rows:
                dates = [r[1] for r in rows]
                conn.execute("""
                    UPDATE backfill_log
                    SET last_date    = MAX(last_date, ?),
                        row_count    = (SELECT COUNT(*) FROM daily_prices WHERE ticker = ?),
                        last_updated = datetime('now')
                    WHERE ticker = ?
                """, (max(dates), ticker, ticker))

    log.info(f"✅  Inserted {len(all_rows):,} rows for {len(ticker_rows)} tickers")
    return len(all_rows)


def main():
    parser = argparse.ArgumentParser(description="Daily rolling-window OHLCV update")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--date", help="Override today's date (YYYY-MM-DD, for testing)")
    args = parser.parse_args()

    if args.date:
        today = date.fromisoformat(args.date)
    else:
        today = date.today()

    log.info(f"{'─'*60}")
    log.info(f"🚀  Daily update starting — today={today}  dry_run={args.dry_run}")

    if not DB_PATH.exists():
        log.error(f"❌  Database not found at {DB_PATH}. Run db/init_db.py first.")
        sys.exit(1)

    conn = get_conn()
    t0 = time.time()

    try:
        tickers = load_tickers()
        log.info(f"📋  Tracking {len(tickers)} tickers from watchlist")

        # Step 1 — Prune old rows
        pruned = prune_old_rows(conn, today, args.dry_run)

        # Step 2 — Fetch today's data
        ticker_rows = fetch_todays_data(tickers, today)

        # Step 3 — Insert
        inserted = insert_rows(conn, ticker_rows, args.dry_run)

        if not args.dry_run:
            conn.commit()

        elapsed = time.time() - t0
        db_size = DB_PATH.stat().st_size / 1_048_576
        total_rows = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]

        log.info(f"{'─'*60}")
        log.info(f"✅  Update complete in {elapsed:.1f}s")
        log.info(f"    Pruned:   {pruned:,} rows")
        log.info(f"    Inserted: {inserted:,} rows")
        log.info(f"    Total DB rows: {total_rows:,}  |  DB size: {db_size:.1f} MB")

    except Exception as e:
        conn.rollback()
        log.error(f"❌  Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    main()
