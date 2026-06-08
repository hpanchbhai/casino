"""
Milestone 2 — Backfill 10 years of daily OHLCV data for a ticker.

Key concept introduced here: INSERT ... ON CONFLICT DO NOTHING
This makes the script IDEMPOTENT — you can run it 10 times and the DB
stays correct. No duplicate rows, no errors.
"""

import psycopg2
import yfinance as yf
from datetime import date

DB_PARAMS = {
    "host": "localhost",
    "dbname": "stocks",
    "user": "",
    "password": "",
}

# ── The SQL we'll execute for every row ───────────────────────────────────────
#
# %s are placeholders — psycopg2 fills them in safely (prevents SQL injection).
# Never use f-strings or .format() to build SQL — that's how you get hacked.
#
# ON CONFLICT (ticker, price_date) DO NOTHING:
#   If a row with this (ticker, date) already exists (our PK), skip it silently.
#   This is what makes re-running the script safe — idempotent.
#
INSERT_SQL = """
    INSERT INTO prices_raw (ticker, price_date, open, high, low, close, volume, adj_close)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, price_date) DO NOTHING;
"""


def backfill(ticker: str, start: str = "2015-01-01"):
    """Download OHLCV from yfinance and insert into prices_raw."""

    print(f"Downloading {ticker} from {start} → today...")
    df = yf.download(ticker, start=start, end=str(date.today()), auto_adjust=True, progress=False)
    # auto_adjust=True → yfinance returns split+dividend adjusted OHLCV.
    # The "Close" column IS the adjusted close. We store it in both close and adj_close
    # so the schema stays explicit.

    if df.empty:
        print("  No data returned — check ticker symbol.")
        return

    print(f"  Got {len(df)} rows. Inserting into prices_raw...")

    # yfinance returns a MultiIndex column DataFrame when downloading a single ticker
    # e.g. ("Close", "AAPL") instead of just "Close". Flatten to simple column names.
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    rows_inserted = 0
    for price_date, row in df.iterrows():
        # price_date is a pandas Timestamp — psycopg2 converts it to DATE automatically.
        # row["Close"] etc. are numpy float64 — also handled automatically.
        cur.execute(INSERT_SQL, (
            ticker,
            price_date,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(row["Volume"]),
            float(row["Close"]),   # adj_close = same as Close when auto_adjust=True
        ))
        rows_inserted += cur.rowcount  # rowcount = 1 if inserted, 0 if skipped (conflict)

    # conn.commit() is critical — psycopg2 runs in a transaction by default.
    # Nothing is saved to disk until you call commit(). If you forget, all
    # inserts vanish when the connection closes.
    conn.commit()

    cur.close()
    conn.close()

    total_rows = len(df)
    skipped = total_rows - rows_inserted
    print(f"  Done. {rows_inserted} inserted, {skipped} skipped (already existed).")


if __name__ == "__main__":
    backfill("AAPL")

    # Verify what landed in the DB
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            MIN(price_date)  AS earliest,
            MAX(price_date)  AS latest,
            COUNT(*)         AS total_rows
        FROM prices_raw
        WHERE ticker = 'AAPL';
    """)
    row = cur.fetchone()
    print(f"\nDB check → earliest: {row[0]}  latest: {row[1]}  rows: {row[2]}")
    cur.close()
    conn.close()
