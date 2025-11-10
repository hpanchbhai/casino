# app/routes_options.py
# Purpose: Stable "Top Options" endpoint that ALWAYS returns a JSON array.
# Guardrails:
# - Does not remove or alter other routes.
# - Uses AsyncSessionLocal from app.database (your current location).
# - Avoids string->date errors by using proper DATE parameters.
# - Returns [] on no data / errors (never a scalar/object), so jq '.[0]' is safe.

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Literal, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

router = APIRouter(tags=["options"])

# Helper: map symbol -> ticker_id
GET_TICKER_ID_SQL = text("""
    SELECT id FROM tickers WHERE symbol = :symbol
""")

# Helper: aggregate option_snapshots into top contracts by volume/notional
# We aggregate over a window (today / 1w / 1m) and return top 10.
AGG_TOP_OPTIONS_SQL = text("""
    SELECT
        s.expiry::date            AS expiry,
        s.opt_type                AS type,
        s.strike::numeric         AS strike,
        COALESCE(SUM(s.volume),0) AS volume,
        COALESCE(SUM(s.open_interest),0) AS open_interest,
        COALESCE(SUM(s.notional_usd),0)  AS notional_usd,
        AVG(s.last_price)               AS last_price,
        AVG(s.iv)                       AS iv
    FROM option_snapshots s
    WHERE s.ticker_id = :ticker_id
      AND s.trade_date BETWEEN :start_date AND :end_date
    GROUP BY s.expiry, s.opt_type, s.strike
    ORDER BY volume DESC, notional_usd DESC
    LIMIT 10
""")

def _date_range_for(range_key: str) -> (date, date):
    """Return [start_date, end_date] inclusive for range=today|1w|1m."""
    today = date.today()
    if range_key == "today":
        return today, today
    if range_key == "1w":
        # 7 calendar days back is fine; your snapshots are daily.
        return today - timedelta(days=7), today
    if range_key == "1m":
        return today - timedelta(days=31), today
    # default fallback – treat as today
    return today, today

@router.get("/options/top/{symbol}")
async def top_options_for_symbol(
    symbol: str,
    range: Literal["today", "1w", "1m"] = Query("today", description="Window for aggregation")
) -> List[Dict[str, Any]]:
    """
    Returns an ARRAY of top contracts for the symbol over the selected range.
    Each item contains: {expiry, type, strike, volume, open_interest, notional_usd, last_price, iv}
    Never returns an object/scalar – empty set => [].
    """
    start_date, end_date = _date_range_for(range)

    async with AsyncSessionLocal() as session:  # type: AsyncSession
        # Resolve ticker_id
        res = await session.execute(GET_TICKER_ID_SQL.bindparams(symbol=str(symbol).upper()))
        row = res.fetchone()
        if not row:
            # Unknown symbol -> consistent empty array, not 404, so frontend isn't broken
            return []

        ticker_id = row[0]

        # Query top contracts
        res2 = await session.execute(
            AGG_TOP_OPTIONS_SQL.bindparams(
                ticker_id=ticker_id,
                start_date=start_date,
                end_date=end_date
            )
        )
        rows = res2.mappings().all()

        # Normalize to plain list[dict]
        data: List[Dict[str, Any]] = []
        for r in rows:
            data.append({
                "expiry": r.get("expiry"),
                "type": r.get("type"),
                "strike": float(r["strike"]) if r.get("strike") is not None else None,
                "volume": int(r["volume"]) if r.get("volume") is not None else 0,
                "open_interest": int(r["open_interest"]) if r.get("open_interest") is not None else 0,
                "notional_usd": float(r["notional_usd"]) if r.get("notional_usd") is not None else 0.0,
                "last_price": float(r["last_price"]) if r.get("last_price") is not None else None,
                "iv": float(r["iv"]) if r.get("iv") is not None else None,
            })

        # Always an array
        return data

