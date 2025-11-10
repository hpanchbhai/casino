# etl/compute_option_insights.py
"""
Compute daily option insights (PCR by volume & notional, IV Rank, etc.)
and persist them into daily_option_insights.

Run:
  PYTHONPATH=. python -m etl.compute_option_insights
"""

from __future__ import annotations

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
from typing import Optional, List
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Use your existing engine (port, creds) from app.database
from app.database import engine

# Bound async session factory
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("compute_option_insights")

# ---------- DDL (split to avoid "multiple commands" error) ----------
DDL_CREATE = """
CREATE TABLE IF NOT EXISTS daily_option_insights (
  id BIGSERIAL PRIMARY KEY,
  ticker_id BIGINT NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  trade_date DATE NOT NULL,
  pcr_volume NUMERIC(18,6),
  pcr_notional NUMERIC(18,6),
  avg_iv NUMERIC(18,6),
  iv_rank_1y NUMERIC(18,6),
  skew_25d NUMERIC(18,6),
  term_slope NUMERIC(18,6),
  gex_lite NUMERIC(18,6),
  top_contracts JSONB,
  UNIQUE (ticker_id, trade_date)
);
"""

DDL_HEAL = [
    "ALTER TABLE daily_option_insights ADD COLUMN IF NOT EXISTS pcr_notional NUMERIC(18,6);",
    "ALTER TABLE daily_option_insights ADD COLUMN IF NOT EXISTS gex_lite NUMERIC(18,6);",
    "ALTER TABLE daily_option_insights ADD COLUMN IF NOT EXISTS top_contracts JSONB;"
]

UPSERT_SQL = text("""
INSERT INTO daily_option_insights (
  ticker_id, trade_date,
  pcr_volume, pcr_notional,
  avg_iv, iv_rank_1y,
  skew_25d, term_slope, gex_lite, top_contracts
)
VALUES (
  :ticker_id, :trade_date,
  :pcr_volume, :pcr_notional,
  :avg_iv, :iv_rank_1y,
  :skew_25d, :term_slope, :gex_lite, :top_contracts
)
ON CONFLICT (ticker_id, trade_date) DO UPDATE SET
  pcr_volume   = EXCLUDED.pcr_volume,
  pcr_notional = EXCLUDED.pcr_notional,
  avg_iv       = EXCLUDED.avg_iv,
  iv_rank_1y   = EXCLUDED.iv_rank_1y,
  skew_25d     = EXCLUDED.skew_25d,
  term_slope   = EXCLUDED.term_slope,
  gex_lite     = EXCLUDED.gex_lite,
  top_contracts= EXCLUDED.top_contracts;
""")

async def ensure_table_exists() -> None:
    async with engine.begin() as conn:
        # run each statement separately (important for asyncpg/prepare)
        for stmt in [DDL_CREATE, *DDL_HEAL]:
            s = stmt.strip().rstrip(";")
            if s:
                await conn.exec_driver_sql(s)
    log.info("✅ daily_option_insights table ensured (and healed if needed)")

def _iv_rank(values: pd.Series) -> float:
    """Return IV Rank (0–100) for the last value vs its min/max range."""
    if values.empty:
        return 0.0
    vals = values.astype(float)
    mn, mx = float(vals.min()), float(vals.max())
    last = float(vals.iloc[-1])
    eps = 1e-9
    return ((last - mn) / (mx - mn + eps)) * 100.0

async def compute_for_symbol(session: AsyncSession, ticker_id: int, symbol: str) -> int:
    """
    Compute insights for the **latest trade_date** available for this ticker
    using option_snapshots (already populated by your snap_options ETL).
    """
    log.info(f"[INFO] Computing insights for {symbol}…")

    # Find latest trade_date for this ticker
    q_last = text("""
        SELECT MAX(trade_date) AS last_date
        FROM option_snapshots
        WHERE ticker_id = :tid
    """)
    row = (await session.execute(q_last, {"tid": ticker_id})).first()
    if not row or row[0] is None:
        log.warning(f"[WARN] No snapshot data found for {symbol}")
        return 0
    last_date = row[0]

    # Pull the entire series (for IV rank) + the latest-day slice (for PCR)
    q_all = text("""
        SELECT trade_date, opt_type, iv, volume, notional_usd
        FROM option_snapshots
        WHERE ticker_id = :tid
        ORDER BY trade_date
    """)
    all_rows = (await session.execute(q_all, {"tid": ticker_id})).mappings().all()
    df_all = pd.DataFrame(all_rows)
    if df_all.empty:
        log.warning(f"[WARN] No snapshot data found for {symbol}")
        return 0

    # Coerce numeric fields to float safely
    for col in ("iv", "volume", "notional_usd"):
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    # Latest-day slice
    df_day = df_all[df_all["trade_date"] == last_date].copy()
    if df_day.empty:
        log.warning(f"[WARN] No rows on latest date for {symbol}")
        return 0

    # Split calls/puts for latest day
    df_calls = df_day[df_day["opt_type"] == "C"]
    df_puts  = df_day[df_day["opt_type"] == "P"]

    # PCR by volume (latest day)
    call_vol = float(df_calls["volume"].sum()) if "volume" in df_calls else 0.0
    put_vol  = float(df_puts["volume"].sum())  if "volume" in df_puts  else 0.0
    pcr_volume = (put_vol / call_vol) if call_vol > 0 else 0.0

    # PCR by notional (latest day)
    call_notional = float(df_calls["notional_usd"].sum()) if "notional_usd" in df_calls else 0.0
    put_notional  = float(df_puts["notional_usd"].sum())  if "notional_usd" in df_puts  else 0.0
    pcr_notional = (put_notional / call_notional) if call_notional > 0 else None

    # Average IV (latest day). If missing, fall back to overall mean.
    if "iv" in df_day and not df_day["iv"].dropna().empty:
        avg_iv = float(df_day["iv"].dropna().mean())
    else:
        avg_iv = float(df_all["iv"].dropna().mean()) if "iv" in df_all else 0.0

    # IV Rank over the full historical series (0–100)
    iv_rank_1y = _iv_rank(df_all["iv"].dropna()) if "iv" in df_all else 0.0

    # Placeholders (you can wire real computations later)
    skew_25d  = 0.0
    term_slope= 0.0
    gex_lite  = 0.0
    top_contracts = None

    # Upsert a single row for the latest date
    await session.execute(
        UPSERT_SQL,
        {
            "ticker_id":   ticker_id,
            "trade_date":  last_date,
            "pcr_volume":  pcr_volume,
            "pcr_notional":pcr_notional,
            "avg_iv":      avg_iv,
            "iv_rank_1y":  iv_rank_1y,
            "skew_25d":    skew_25d,
            "term_slope":  term_slope,
            "gex_lite":    gex_lite,
            "top_contracts": top_contracts,
        },
    )
    log.info(f"[OK] {symbol}: inserted 1 insight row for {last_date}.")
    return 1

async def run_all() -> None:
    await ensure_table_exists()
    async with AsyncSessionLocal() as session:
        # Fetch tickers
        rs = await session.execute(text("SELECT id, symbol FROM tickers ORDER BY symbol"))
        tickers: List[tuple] = rs.all()

        total = 0
        for tid, sym in tickers:
            try:
                total += await compute_for_symbol(session, tid, sym)
            except Exception as e:
                log.exception(f"[ERR] {sym} failed: {e}")
        await session.commit()
        log.info(f"[SUMMARY] Completed {len(tickers)} symbols, rows inserted: {total}")

def main():
    asyncio.run(run_all())

if __name__ == "__main__":
    main()

