# app/routes_pricebars.py
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

router = APIRouter(tags=["pricebars"])

# ---------- SQL helpers

SQL_TICKER_ID = text("""
    SELECT id FROM tickers WHERE symbol = :symbol
""")

SQL_PRICEBARS_RANGE = text("""
    SELECT
        pb.date::date              AS date,
        pb.open::numeric           AS open,
        pb.high::numeric           AS high,
        pb.low::numeric            AS low,
        pb.close::numeric          AS close,
        pb.adjusted_close::numeric AS adjusted_close,
        COALESCE(pb.volume,0)      AS volume
    FROM price_bars pb
    WHERE pb.ticker_id = :tid AND pb.date >= :start_date
    ORDER BY pb.date ASC
""")

SQL_LATEST_CLOSE = text("""
    SELECT
        pb.date::date  AS date,
        pb.close::numeric AS close
    FROM price_bars pb
    WHERE pb.ticker_id = :tid
    ORDER BY pb.date DESC
    LIMIT 1
""")


async def _get_ticker_id(session: AsyncSession, symbol: str) -> Optional[int]:
    res = await session.execute(SQL_TICKER_ID, {"symbol": symbol.upper()})
    row = res.first()
    return int(row[0]) if row else None


def _iso(d: dt.date | dt.datetime | None) -> Optional[str]:
    if not d:
        return None
    return d.date().isoformat() if isinstance(d, dt.datetime) else d.isoformat()


# ---------- Routes

@router.get("/pricebars/{symbol}")
async def get_pricebars(
    symbol: str,
    days: int = Query(default=120, ge=1, le=3650, description="Number of most recent days to return"),
) -> List[Dict]:
    """
    Return OHLCV rows for `symbol` over the last `days`.
    Dates are ISO strings, numerics are JSON numbers.
    """
    start_date = (dt.date.today() - dt.timedelta(days=days))

    async with AsyncSessionLocal() as session:  # type: AsyncSession
        tid = await _get_ticker_id(session, symbol)
        if tid is None:
            # Not in DB yet
            return []

        try:
            res = await session.execute(
                SQL_PRICEBARS_RANGE,
                {"tid": tid, "start_date": start_date}
            )
            rows = [dict(r._mapping) for r in res.fetchall()]
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"pricebars_query_failed: {type(e).__name__}: {e}"
            )

    # Normalize output (coerce Decimal -> float)
    out: List[Dict] = []
    for r in rows:
        out.append({
            "date": _iso(r.get("date")),
            "open": float(r["open"]) if r.get("open") is not None else None,
            "high": float(r["high"]) if r.get("high") is not None else None,
            "low": float(r["low"]) if r.get("low") is not None else None,
            "close": float(r["close"]) if r.get("close") is not None else None,
            "adjusted_close": float(r["adjusted_close"]) if r.get("adjusted_close") is not None else None,
            "volume": int(r["volume"]) if r.get("volume") is not None else 0,
        })
    return out


@router.get("/pricebars/{symbol}/latest")
async def latest_close(symbol: str) -> Dict:
    """
    Return {"date": "...", "close": number} for the most recent close we have.
    """
    async with AsyncSessionLocal() as session:  # type: AsyncSession
        tid = await _get_ticker_id(session, symbol)
        if tid is None:
            # Not in DB yet
            return {"date": None, "close": None}

        try:
            res = await session.execute(SQL_LATEST_CLOSE, {"tid": tid})
            row = res.first()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"latest_close_query_failed: {type(e).__name__}: {e}"
            )

    if not row:
        return {"date": None, "close": None}

    d = dict(row._mapping)
    return {"date": _iso(d.get("date")), "close": float(d["close"]) if d.get("close") is not None else None}


@router.get("/pricebars/{symbol}/deltas")
async def daily_deltas(
    symbol: str,
    days: int = Query(default=120, ge=2, le=3650),
) -> List[Dict]:
    """
    Convenience endpoint: returns [{date, close, volume, pct_change}] for last N days.
    UI can compute this client-side, but this helps keep the browser light.
    """
    bars = await get_pricebars(symbol, days=days)
    if not bars:
        return []

    out: List[Dict] = []
    prev_close: Optional[float] = None
    for row in bars:
        c = row.get("close")
        vol = row.get("volume", 0)
        if c is None:
            out.append({"date": row["date"], "close": None, "volume": vol, "pct_change": None})
            continue
        pct = None
        if prev_close not in (None, 0):
            pct = ((c - prev_close) / prev_close) * 100.0
        out.append({"date": row["date"], "close": c, "volume": vol, "pct_change": pct})
        prev_close = c
    return out

