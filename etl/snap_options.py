#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Snapshot options chains into option_snapshots for one or more symbols
and one or more trade dates.

Usage examples:
  # Single day (default = today if --date omitted)
  python -m etl.snap_options --symbols "AAPL,NVDA,MSFT" --date 2025-10-29

  # Last 30 days (including today)
  python -m etl.snap_options --symbols "AAPL,NVDA,MSFT" --days 30

  # Explicit date range (inclusive)
  python -m etl.snap_options --symbols "AAPL,NVDA" --start 2025-09-01 --end 2025-09-30
"""

import argparse
import asyncio
import datetime as dt
import logging
import math
import os
from typing import Iterable, List, Optional, Tuple

import pandas as pd

# External data source
# - yfinance is convenient; auto_adjust defaults changed in recent versions, but we only use options.
import yfinance as yf

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker

# --- Project config -----------------------------------------------------------

# Expect DATABASE_URL in app.config or environment; prefer env override to keep CLI flexible.
try:
    from app.config import DATABASE_URL as CFG_DATABASE_URL
except Exception:
    CFG_DATABASE_URL = None

DATABASE_URL = os.getenv("DATABASE_URL", CFG_DATABASE_URL or "postgresql+asyncpg://tracker:trackerpass@localhost:5433/stock_tracker")

# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Async engine & sessionmaker
engine: AsyncEngine = create_async_engine(DATABASE_URL, future=True, pool_pre_ping=True)
async_sessionmaker = _async_sessionmaker(engine, expire_on_commit=False)

# Helpers to coerce numeric safely
def _to_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and (math.isnan(v))):
        return None
    try:
        return float(v)
    except Exception:
        return None

def _to_int(v) -> Optional[int]:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return int(v)
    except Exception:
        return None

# ---- DB bootstrapping (idempotent) ------------------------------------------

DDL_CREATE_OPTION_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS option_snapshots (
  id BIGSERIAL PRIMARY KEY,
  ticker_id BIGINT NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  trade_date DATE NOT NULL,
  expiry DATE NOT NULL,
  opt_type CHAR(1) NOT NULL,
  strike NUMERIC(18,6) NOT NULL,
  iv NUMERIC(12,6),
  volume INTEGER,
  open_interest INTEGER,
  last_price NUMERIC(18,6),
  bid NUMERIC(18,6),
  ask NUMERIC(18,6),
  delta NUMERIC(12,6),
  gamma NUMERIC(12,6),
  vega NUMERIC(12,6),
  theta NUMERIC(12,6),
  notional_usd NUMERIC(18,6),
  UNIQUE (ticker_id, trade_date, expiry, opt_type, strike)
);
"""

# Split multi-statement index creation to avoid asyncpg “multiple commands” error.
DDL_CREATE_IDX_OPT_SNAP_SYMBOL_DATE = """
CREATE INDEX IF NOT EXISTS idx_opt_snap_symbol_date
  ON option_snapshots (ticker_id, trade_date);
"""

UPSERT_SNAPSHOT_SQL = text("""
INSERT INTO option_snapshots (
  ticker_id, trade_date, expiry, opt_type, strike,
  iv, volume, open_interest, last_price, bid, ask,
  delta, gamma, vega, theta, notional_usd
) VALUES (
  :ticker_id, :trade_date, :expiry, :opt_type, :strike,
  :iv, :volume, :open_interest, :last_price, :bid, :ask,
  :delta, :gamma, :vega, :theta, :notional_usd
)
ON CONFLICT (ticker_id, trade_date, expiry, opt_type, strike)
DO UPDATE SET
  iv = EXCLUDED.iv,
  volume = EXCLUDED.volume,
  open_interest = EXCLUDED.open_interest,
  last_price = EXCLUDED.last_price,
  bid = EXCLUDED.bid,
  ask = EXCLUDED.ask,
  delta = EXCLUDED.delta,
  gamma = EXCLUDED.gamma,
  vega = EXCLUDED.vega,
  theta = EXCLUDED.theta,
  notional_usd = EXCLUDED.notional_usd
""")

GET_TICKER_ID_SQL = text("SELECT id FROM tickers WHERE symbol = :sym")

async def ensure_table_exists() -> None:
    async with engine.begin() as conn:
        # One statement at a time for asyncpg
        await conn.exec_driver_sql(DDL_CREATE_OPTION_SNAPSHOTS)
        await conn.exec_driver_sql(DDL_CREATE_IDX_OPT_SNAP_SYMBOL_DATE)
    logging.info("✅ option_snapshots table ensured")

async def get_ticker_id(session: AsyncSession, symbol: str) -> Optional[int]:
    res = await session.execute(GET_TICKER_ID_SQL, {"sym": symbol.upper()})
    row = res.first()
    if row:
        return row[0]
    return None

# ---- External fetcher -------------------------------------------------------

async def _fetch_option_rows_for_symbol_date(symbol: str, trade_date: dt.date) -> List[dict]:
    """
    Fetch options chain rows for a symbol as-of 'trade_date'.

    NOTE:
      yfinance returns current chain; for historical ‘as-of’ we accept
      this approximation unless you wire a historical provider (Polygon/OPRA).
      The loader remains idempotent and schema-safe.
    """
    rows: List[dict] = []
    try:
        tk = yf.Ticker(symbol)
        expirations = tk.options or []

        # Safety: cap number of expiries so we don’t hammer the provider
        # You can tune this to, say, the next 6 expiries.
        expirations = expirations[:12]

        for exp_str in expirations:
            try:
                expiry = dt.date.fromisoformat(exp_str)
            except Exception:
                # yfinance sometimes returns weird formats; skip gracefully
                continue

            try:
                chain = tk.option_chain(exp_str)
            except Exception as e:
                logging.warning(f"[{symbol}] Failed fetching chain for {exp_str}: {e}")
                continue

            for side_name, side_flag in (("calls", "C"), ("puts", "P")):
                side_df: pd.DataFrame = getattr(chain, side_name, pd.DataFrame())
                if side_df is None or side_df.empty:
                    continue

                # Normalize/rename if needed
                # yfinance: columns commonly include: strike, lastPrice, bid, ask, volume, openInterest, impliedVolatility
                for _, r in side_df.iterrows():
                    strike = _to_float(r.get("strike"))
                    if strike is None:
                        continue
                    vol = _to_int(r.get("volume"))
                    oi = _to_int(r.get("openInterest"))
                    last = _to_float(r.get("lastPrice"))
                    bid = _to_float(r.get("bid"))
                    ask = _to_float(r.get("ask"))
                    iv = _to_float(r.get("impliedVolatility"))

                    # Greeks unavailable from yfinance chains; keep None
                    delta = None
                    gamma = None
                    vega = None
                    theta = None

                    # Notional proxy: lastPrice * 100 * volume
                    notional = None
                    if last is not None and vol is not None:
                        notional = round(last * 100.0 * vol, 6)

                    rows.append(
                        {
                            "trade_date": trade_date,
                            "expiry": expiry,
                            "opt_type": side_flag,
                            "strike": strike,
                            "iv": iv,
                            "volume": vol,
                            "open_interest": oi,
                            "last_price": last,
                            "bid": bid,
                            "ask": ask,
                            "delta": delta,
                            "gamma": gamma,
                            "vega": vega,
                            "theta": theta,
                            "notional_usd": notional,
                        }
                    )
    except Exception as e:
        logging.error(f"[{symbol}] options fetch error: {e}")

    return rows

# ---- Loader -----------------------------------------------------------------

async def upsert_option_rows(session: AsyncSession, ticker_id: int, rows: List[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    # Batch in chunks to avoid enormous executemany payloads
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        for rec in chunk:
            params = {
                "ticker_id": ticker_id,
                "trade_date": rec["trade_date"],
                "expiry": rec["expiry"],
                "opt_type": rec["opt_type"],
                "strike": rec["strike"],
                "iv": rec["iv"],
                "volume": rec["volume"],
                "open_interest": rec["open_interest"],
                "last_price": rec["last_price"],
                "bid": rec["bid"],
                "ask": rec["ask"],
                "delta": rec["delta"],
                "gamma": rec["gamma"],
                "vega": rec["vega"],
                "theta": rec["theta"],
                "notional_usd": rec["notional_usd"],
            }
            await session.execute(UPSERT_SNAPSHOT_SQL, params)
            inserted += 1
        await session.commit()
    return inserted

async def snapshot_for_symbol(session: AsyncSession, symbol: str, trade_date: dt.date) -> Tuple[str, int]:
    tid = await get_ticker_id(session, symbol)
    if not tid:
        logging.warning(f"[WARN] Unknown symbol in tickers table: {symbol}")
        return symbol, 0

    rows = await _fetch_option_rows_for_symbol_date(symbol, trade_date)
    if not rows:
        logging.warning(f"[WARN] No option rows for {symbol} on {trade_date}")
        return symbol, 0

    count = await upsert_option_rows(session, tid, rows)
    return symbol, count

# ---- CLI & Orchestration ----------------------------------------------------

def _date_range(start: dt.date, end: dt.date) -> List[dt.date]:
    if end < start:
        return []
    days = (end - start).days + 1
    return [start + dt.timedelta(days=i) for i in range(days)]

def _last_n_days(n: int, inclusive_today: bool = True) -> List[dt.date]:
    # macOS-compatible; uses datetime only.
    base = dt.date.today()
    if inclusive_today:
        return [base - dt.timedelta(days=i) for i in range(n)]
    else:
        return [base - dt.timedelta(days=i) for i in range(1, n + 1)]

async def run(symbols: List[str], dates: List[dt.date]) -> None:
    await ensure_table_exists()

    total = 0
    ok_pairs = 0
    errors = 0

    for trade_date in dates:
        logging.info(f"[info] Snapshotting options for {trade_date} ...")
        async with async_sessionmaker() as session:
            for sym in symbols:
                try:
                    s, n = await snapshot_for_symbol(session, sym.strip().upper(), trade_date)
                    if n > 0:
                        logging.info(f"[ok] {s} @ {trade_date}: upserted {n} rows")
                        ok_pairs += 1
                        total += n
                    else:
                        logging.info(f"[ok] {s} @ {trade_date}: nothing to upsert")
                except Exception as e:
                    errors += 1
                    logging.exception(f"[err] {sym} @ {trade_date}: {e}")

    logging.info(f"[summary] ok_pairs={ok_pairs} total_rows={total} errors={errors}")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Snapshot option chains into Postgres")
    p.add_argument("--symbols", required=True, help="Comma-separated list, e.g. AAPL,NVDA,MSFT")
    p.add_argument("--date", help="Single date YYYY-MM-DD (default: today)", default=None)
    p.add_argument("--days", type=int, help="Backfill N days including today", default=None)
    p.add_argument("--start", help="Range start YYYY-MM-DD", default=None)
    p.add_argument("--end", help="Range end YYYY-MM-DD", default=None)
    return p.parse_args()

def resolve_dates(args: argparse.Namespace) -> List[dt.date]:
    # Mode precedence: --days, --start/--end, --date, else today
    if args.days is not None:
        if args.days <= 0:
            raise SystemExit("--days must be > 0")
        return sorted(_last_n_days(args.days), reverse=False)
    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("Both --start and --end must be provided for range mode")
        try:
            start = dt.date.fromisoformat(args.start)
            end = dt.date.fromisoformat(args.end)
        except ValueError:
            raise SystemExit("Invalid --start/--end date format; expected YYYY-MM-DD")
        dates = _date_range(start, end)
        if not dates:
            raise SystemExit("Empty date range (is --end before --start?)")
        return dates
    if args.date:
        try:
            d = dt.date.fromisoformat(args.date)
        except ValueError:
            raise SystemExit("Invalid --date format; expected YYYY-MM-DD")
        return [d]
    return [dt.date.today()]

def main() -> None:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("No symbols provided")

    dates = resolve_dates(args)
    logging.info(f"[info] Running for {len(symbols)} symbols over {len(dates)} date(s)")
    asyncio.run(run(symbols, dates))

if __name__ == "__main__":
    main()

