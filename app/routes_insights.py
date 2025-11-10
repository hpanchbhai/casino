# app/routes_insights.py
from typing import List, Optional
from datetime import date, timedelta
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# We use the same session factory the rest of core uses.
from app.database import AsyncSessionLocal

router = APIRouter(prefix="/insights", tags=["insights"])

# --------- Pydantic models ---------
class InsightPoint(BaseModel):
    trade_date: str
    pcr_volume: float
    pcr_notional: Optional[float] = None
    avg_iv: Optional[float] = None
    iv_rank_1y: Optional[float] = None
    skew_25d: Optional[float] = None
    term_slope: Optional[float] = None
    gex_lite: Optional[float] = None

class InsightLatest(BaseModel):
    symbol: str
    trade_date: str
    pcr_volume: float
    pcr_notional: Optional[float] = None
    avg_iv: Optional[float] = None
    iv_rank_1y: Optional[float] = None
    skew_25d: Optional[float] = None
    term_slope: Optional[float] = None
    gex_lite: Optional[float] = None
    top_contracts: Optional[str] = None
    sentiment: Optional[str] = None

class InsightHistory(BaseModel):
    symbol: str
    points: List[InsightPoint]

# --------- DB session dependency ---------
async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

def _f(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)

# --------- Endpoints ---------
@router.get("/{symbol}/latest", response_model=InsightLatest)
async def latest_insight(symbol: str, session: AsyncSession = Depends(get_session)):
    # Resolve ticker_id first
    tid_row = await session.execute(
        text("SELECT id FROM tickers WHERE symbol = :s"),
        {"s": symbol.upper()},
    )
    tid = tid_row.scalar()
    if not tid:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    sql = text("""
        SELECT t.symbol,
               d.trade_date,
               d.pcr_volume,
               d.pcr_notional,
               d.avg_iv,
               d.iv_rank_1y,
               d.skew_25d,
               d.term_slope,
               d.gex_lite,
               d.top_contracts,
               COALESCE(d.sentiment, NULL) AS sentiment
        FROM daily_option_insights d
        JOIN tickers t ON t.id = d.ticker_id
        WHERE d.ticker_id = :tid
        ORDER BY d.trade_date DESC
        LIMIT 1
    """)
    res = await session.execute(sql, {"tid": tid})
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="No insights available")

    return InsightLatest(
        symbol=row["symbol"],
        trade_date=str(row["trade_date"]),
        pcr_volume=_f(row["pcr_volume"]),
        pcr_notional=_f(row["pcr_notional"]) if row["pcr_notional"] is not None else None,
        avg_iv=_f(row["avg_iv"]) if row["avg_iv"] is not None else None,
        iv_rank_1y=_f(row["iv_rank_1y"]) if row["iv_rank_1y"] is not None else None,
        skew_25d=_f(row["skew_25d"]) if row["skew_25d"] is not None else None,
        term_slope=_f(row["term_slope"]) if row["term_slope"] is not None else None,
        gex_lite=_f(row["gex_lite"]) if row["gex_lite"] is not None else None,
        top_contracts=row["top_contracts"],
        sentiment=row["sentiment"],
    )

@router.get("/{symbol}/history", response_model=InsightHistory)
async def history_insight(
    symbol: str,
    days: int = Query(90, ge=1, le=365 * 5, description="How many days back to fetch"),
    session: AsyncSession = Depends(get_session),
):
    # Resolve ticker_id
    tid_row = await session.execute(
        text("SELECT id FROM tickers WHERE symbol = :s"),
        {"s": symbol.upper()},
    )
    tid = tid_row.scalar()
    if not tid:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    sql = text("""
        SELECT d.trade_date,
               d.pcr_volume,
               d.pcr_notional,
               d.avg_iv,
               d.iv_rank_1y,
               d.skew_25d,
               d.term_slope,
               d.gex_lite
        FROM daily_option_insights d
        WHERE d.ticker_id = :tid
          AND d.trade_date >= (CURRENT_DATE - :days::INT)
        ORDER BY d.trade_date
    """)
    res = await session.execute(sql, {"tid": tid, "days": days})
    rows = res.mappings().all()

    points = [
        InsightPoint(
            trade_date=str(r["trade_date"]),
            pcr_volume=_f(r["pcr_volume"]),
            pcr_notional=_f(r["pcr_notional"]) if r["pcr_notional"] is not None else None,
            avg_iv=_f(r["avg_iv"]) if r["avg_iv"] is not None else None,
            iv_rank_1y=_f(r["iv_rank_1y"]) if r["iv_rank_1y"] is not None else None,
            skew_25d=_f(r["skew_25d"]) if r["skew_25d"] is not None else None,
            term_slope=_f(r["term_slope"]) if r["term_slope"] is not None else None,
            gex_lite=_f(r["gex_lite"]) if r["gex_lite"] is not None else None,
        )
        for r in rows
    ]

    return InsightHistory(symbol=symbol.upper(), points=points)

