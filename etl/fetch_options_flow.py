# etl/fetch_options_flow.py

import sys, os
import asyncio
from datetime import datetime, date

from sqlalchemy import select, update, insert
from sqlalchemy.exc import IntegrityError
import pandas as pd

# so we can `import app...`
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal
from app.models import Ticker, OptionFlow


async def get_or_create_ticker(session, symbol: str):
    symbol_up = symbol.upper()

    res = await session.execute(
        select(Ticker).where(Ticker.symbol == symbol_up)
    )
    row = res.scalar_one_or_none()
    if row:
        return row

    # create if missing
    new_ticker = Ticker(symbol=symbol_up)
    session.add(new_ticker)
    await session.commit()
    await session.refresh(new_ticker)
    return new_ticker


async def upsert_option_flow_rows(session, ticker_id: int, rows: list[dict]):
    """
    rows format:
    {
        "contract_type": "CALL" | "PUT",
        "strike": float,
        "expiry": date,
        "trade_date": date,

        "volume_today": int,
        "avg_volume_20d": float,
        "open_interest": int,
        "volume_ratio": float,
        "notional_usd": float,
        "iv": float,
        "note": str,
    }
    """

    for r in rows:
        payload = {
            "ticker_id": ticker_id,
            "contract_type": r["contract_type"],
            "strike": float(r["strike"]),
            "expiry": r["expiry"],
            "trade_date": r["trade_date"],
            "volume_today": int(r["volume_today"]),
            "avg_volume_20d": float(r["avg_volume_20d"]) if r.get("avg_volume_20d") is not None else None,
            "open_interest": int(r["open_interest"]) if r.get("open_interest") is not None else None,
            "volume_ratio": float(r["volume_ratio"]) if r.get("volume_ratio") is not None else None,
            "notional_usd": float(r["notional_usd"]) if r.get("notional_usd") is not None else None,
            "iv": float(r["iv"]) if r.get("iv") is not None else None,
            "note": r.get("note"),
        }

        ins_stmt = insert(OptionFlow).values(**payload)

        try:
            await session.execute(ins_stmt)
        except IntegrityError:
            # row already exists for this contract+day → do an update
            await session.rollback()
            upd_stmt = (
                update(OptionFlow)
                .where(
                    OptionFlow.ticker_id == ticker_id,
                    OptionFlow.contract_type == payload["contract_type"],
                    OptionFlow.strike == payload["strike"],
                    OptionFlow.expiry == payload["expiry"],
                    OptionFlow.trade_date == payload["trade_date"],
                )
                .values(**payload)
            )
            await session.execute(upd_stmt)

    await session.commit()


async def mock_fetch_flow_for_symbol(symbol: str):
    """
    For now we mock flow. Later you'll replace this with a real data source.
    We'll pretend "today" = current UTC date.
    """
    today = date.today()

    # Fake sample data similar to what we were showing in the UI.
    sample = [
        {
            "contract_type": "CALL",
            "strike": 200.0,
            "expiry": date(2025, 1, 17),
            "trade_date": today,
            "volume_today": 124500,
            "avg_volume_20d": 9500,
            "open_interest": 88210,
            "volume_ratio": 124500 / 9500,  # ~13.1x
            "notional_usd": 35_200_000.0,
            "iv": 41.0,
            "note": "aggressive call sweep",
        },
        {
            "contract_type": "PUT",
            "strike": 180.0,
            "expiry": date(2024, 11, 15),
            "trade_date": today,
            "volume_today": 98000,
            "avg_volume_20d": 8000,
            "open_interest": 45210,
            "volume_ratio": 98000 / 8000,  # ~12.25x
            "notional_usd": 22_100_000.0,
            "iv": 53.0,
            "note": "downside hedge / bearish protection",
        },
        {
            "contract_type": "CALL",
            "strike": 210.0,
            "expiry": date(2025, 3, 21),
            "trade_date": today,
            "volume_today": 76500,
            "avg_volume_20d": 12000,
            "open_interest": 39120,
            "volume_ratio": 76500 / 12000,  # 6.4x
            "notional_usd": 19_400_000.0,
            "iv": 38.0,
            "note": "OTM calls building size",
        },
        {
            "contract_type": "PUT",
            "strike": 150.0,
            "expiry": date(2025, 1, 17),
            "trade_date": today,
            "volume_today": 54200,
            "avg_volume_20d": 4500,
            "open_interest": 31000,
            "volume_ratio": 54200 / 4500,  # 12x
            "notional_usd": 12_800_000.0,
            "iv": 47.0,
            "note": "protective puts / insurance",
        },
        {
            "contract_type": "CALL",
            "strike": 250.0,
            "expiry": date(2025, 6, 20),
            "trade_date": today,
            "volume_today": 40100,
            "avg_volume_20d": 5000,
            "open_interest": 28500,
            "volume_ratio": 40100 / 5000,  # 8x
            "notional_usd": 10_200_000.0,
            "iv": 36.0,
            "note": "lottery OTM calls",
        },
    ]

    return sample


async def run_for_symbol(symbol: str):
    async with AsyncSessionLocal() as session:
        ticker_row = await get_or_create_ticker(session, symbol)

        mock_rows = await mock_fetch_flow_for_symbol(symbol)
        await upsert_option_flow_rows(session, ticker_row.id, mock_rows)

        print(f"[OK] Stored {len(mock_rows)} option flow rows for {symbol}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Mock ingest options flow into Postgres"
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Ticker symbol, e.g. TSLA",
    )
    args = parser.parse_args()

    asyncio.run(run_for_symbol(args.symbol))


if __name__ == "__main__":
    main()

