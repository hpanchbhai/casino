# etl/refresh_metadata.py
# =========================================================
# Fetch basic metadata (sector) for all tickers in DB and update tickers.sector.
# Uses yfinance (no API key). Safe for repeated runs.
# =========================================================

import os
import asyncio
import logging
from typing import Optional

import yfinance as yf
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("refresh_metadata")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://tracker:trackerpass@localhost:5433/stock_tracker",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def fetch_sector(symbol: str) -> Optional[str]:
    """
    Try to obtain sector via yfinance. Some symbols may return None.
    """
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        sector = info.get("sector")
        if isinstance(sector, str) and sector.strip():
            return sector.strip()
    except Exception:
        pass
    return None


async def update_sector(session: AsyncSession, symbol: str, sector: Optional[str]):
    if not sector:
        return
    q = text("UPDATE tickers SET sector = :sector WHERE symbol = :sym")
    await session.execute(q.bindparams(sector=sector, sym=symbol))


async def main():
    async with SessionLocal() as session:
        # get all symbols
        res = await session.execute(text("SELECT symbol FROM tickers ORDER BY symbol"))
        symbols = [r[0] for r in res.fetchall()]
        if not symbols:
            log.warning("No tickers present to refresh.")
            return

        log.info(f"Refreshing sector for {len(symbols)} tickers...")
        updated = 0
        for sym in symbols:
            sector = await asyncio.get_event_loop().run_in_executor(None, fetch_sector, sym)
            if sector:
                await update_sector(session, sym, sector)
                updated += 1

        await session.commit()
        log.info(f"Done. Updated sector for {updated}/{len(symbols)} tickers.")


if __name__ == "__main__":
    asyncio.run(main())

