# app/crud.py
#
# Async DB helpers used by FastAPI and ETL.
# - create_ticker
# - get_ticker_by_symbol
# - upsert_pricebars

import pandas as pd

from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date

from .models import Ticker, PriceBar


async def get_ticker_by_symbol(db: AsyncSession, symbol: str):
    """Return existing Ticker row by symbol, or None."""
    symbol_up = symbol.upper()
    stmt = select(Ticker).where(Ticker.symbol == symbol_up).limit(1)
    res = await db.execute(stmt)
    row = res.scalar_one_or_none()
    return row


async def create_ticker(db: AsyncSession, symbol: str):
    """Create a new ticker row with this symbol (uppercase)."""
    symbol_up = symbol.upper()

    new_ticker = Ticker(symbol=symbol_up)
    db.add(new_ticker)
    try:
        await db.commit()
    except IntegrityError:
        # ticker already existed in race condition, that's fine
        await db.rollback()

    # re-fetch to be sure we return the object with an id
    stmt = select(Ticker).where(Ticker.symbol == symbol_up).limit(1)
    res = await db.execute(stmt)
    return res.scalar_one()


async def upsert_pricebars(db: AsyncSession, ticker_id: int, rows: list[dict]):
    """
    Insert or update daily price bars for a ticker.
    rows = [
      {
        "date": datetime,
        "open": float,
        "high": float,
        "low": float,
        "close": float,
        "adjusted_close": float,
        "volume": int,
      },
      ...
    ]
    """
    for r in rows:
        raw_date = r["date"]

        # normalize to python date (YYYY-MM-DD)
        if isinstance(raw_date, datetime):
            bar_date = raw_date.date()
        elif hasattr(raw_date, "to_pydatetime"):
            # e.g. pandas.Timestamp
            bar_date = raw_date.to_pydatetime().date()
        else:
            # last resort: parse string
            bar_date = pd.to_datetime(raw_date).date()

        open_val = float(r["open"])
        high_val = float(r["high"])
        low_val = float(r["low"])
        close_val = float(r["close"])
        adj_close_val = float(r["adjusted_close"])
        vol_val = int(r["volume"])

        ins_stmt = insert(PriceBar).values(
            ticker_id=ticker_id,
            date=bar_date,
            open=open_val,
            high=high_val,
            low=low_val,
            close=close_val,
            adjusted_close=adj_close_val,
            volume=vol_val,
        )

        try:
            await db.execute(ins_stmt)
        except IntegrityError:
            await db.rollback()
            upd_stmt = (
                update(PriceBar)
                .where(
                    PriceBar.ticker_id == ticker_id,
                    PriceBar.date == bar_date,
                )
                .values(
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    adjusted_close=adj_close_val,
                    volume=vol_val,
                )
            )
            await db.execute(upd_stmt)

    await db.commit()

