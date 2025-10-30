import sys, os
import asyncio
from datetime import datetime
import argparse

import yfinance as yf
import pandas as pd
import numpy as np

# make sure we can "import app"
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal
from app import crud
from sqlalchemy.ext.asyncio import AsyncSession


def _scalar(v):
    """
    Make sure a pandas value becomes a plain Python scalar.
    - If it's already int/float/str/datetime, return it.
    - If it's a pandas Series/array with one element, return that element.
    """
    if isinstance(v, (int, float, str, datetime, np.number, pd.Timestamp)):
        return v

    # single-element Series or array
    if hasattr(v, "iloc") and len(v) == 1:
        return v.iloc[0]
    if hasattr(v, "__len__") and len(v) == 1:
        return list(v)[0]

    return v  # fallback, best effort


async def ensure_ticker(session: AsyncSession, symbol: str):
    """
    Ensure a ticker row exists; return ORM row.
    """
    existing = await crud.get_ticker_by_symbol(session, symbol)
    if existing:
        return existing
    created = await crud.create_ticker(session, symbol)
    return created


async def fetch_and_store(symbol: str, period: str = "2y", start=None, end=None):
    """
    Download OHLCV daily candles for `symbol` using yfinance and store in DB.
    """
    print(f"[INFO] Fetching {symbol} from Yahoo Finance...")

    if start or end:
        df = yf.download(
            symbol,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    else:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False,
        )

    if df is None or df.empty:
        print(f"[WARN] No data returned for {symbol}")
        return

    # reset index so we definitely have a 'Date' column
    df = df.reset_index()

    rows_to_upsert = []

    for _, row in df.iterrows():
        # pull raw values
        raw_date = _scalar(row.get("Date"))
        raw_open = _scalar(row.get("Open"))
        raw_high = _scalar(row.get("High"))
        raw_low = _scalar(row.get("Low"))
        raw_close = _scalar(row.get("Close"))
        raw_adj_close = _scalar(row.get("Adj Close", raw_close))
        raw_volume = _scalar(row.get("Volume"))

        # normalize date to datetime
        if isinstance(raw_date, pd.Timestamp):
            date_val = raw_date.to_pydatetime()
        elif isinstance(raw_date, datetime):
            date_val = raw_date
        else:
            # best guess parse
            date_val = pd.to_datetime(raw_date).to_pydatetime()

        # numeric cleanup: float/int cast
        open_val = float(raw_open)
        high_val = float(raw_high)
        low_val = float(raw_low)
        close_val = float(raw_close)
        adj_close_val = float(raw_adj_close)
        volume_val = int(raw_volume)

        rows_to_upsert.append(
            {
                "date": date_val,            # full datetime here; crud will .date() it
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "adjusted_close": adj_close_val,
                "volume": volume_val,
            }
        )

    async with AsyncSessionLocal() as session:
        ticker_obj = await ensure_ticker(session, symbol)
        await crud.upsert_pricebars(session, ticker_obj.id, rows_to_upsert)

    print(f"[DONE] Stored {len(rows_to_upsert)} rows for {symbol}.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical price data into Postgres"
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Ticker symbol(s), comma-separated. Example: AAPL,TSLA,NVDA"
    )
    parser.add_argument(
        "--period",
        default="2y",
        help="yfinance period (e.g. 1y, 2y, 5y, max). Ignored if --start is provided."
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Explicit start date YYYY-MM-DD (overrides period)."
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Explicit end date YYYY-MM-DD (optional)."
    )

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    async def runner():
        for sym in symbols:
            await fetch_and_store(
                sym,
                period=args.period,
                start=args.start,
                end=args.end,
            )

    asyncio.run(runner())


if __name__ == "__main__":
    main()

