# app/insights_api.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

# -----------------------------------------------------------------------------
# App setup (this was missing; caused "Attribute 'app' not found" on uvicorn)
# -----------------------------------------------------------------------------
app = FastAPI(title="Option Insights API", version="1.0.0")

# Allow your Vite dev server to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple health check for this service (port 8002 in your setup)
@app.get("/health")
async def health():
    return {"status": "ok", "service": "insights", "engine": "postgres"}

# -----------------------------------------------------------------------------
# Router with insights endpoints (your existing logic, preserved)
# -----------------------------------------------------------------------------
router = APIRouter(tags=["insights"], prefix="")

def _f(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    return x

async def _get_ticker_id(session: AsyncSession, symbol: str) -> Optional[int]:
    res = await session.execute(
        text("SELECT id FROM tickers WHERE symbol = :sym"),
        {"sym": symbol.upper()},
    )
    row = res.first()
    return row[0] if row else None

@router.get("/insights/{symbol}/latest")
async def latest_insight(symbol: str, request: Request, debug: int = 0):
    try:
        async with AsyncSessionLocal() as session:
            tid = await _get_ticker_id(session, symbol)
            if not tid:
                raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

            sql = text(
                """
                SELECT t.symbol,
                       d.trade_date,
                       COALESCE(d.pcr_volume, 0)  AS pcr_volume,
                       d.pcr_notional             AS pcr_notional,
                       d.avg_iv                   AS avg_iv,
                       d.iv_rank_1y               AS iv_rank_1y,
                       COALESCE(d.skew_25d, 0)    AS skew_25d,
                       COALESCE(d.term_slope, 0)  AS term_slope,
                       COALESCE(d.gex_lite, 0)    AS gex_lite,
                       d.top_contracts            AS top_contracts,
                       d.sentiment                AS sentiment
                FROM daily_option_insights d
                JOIN tickers t ON t.id = d.ticker_id
                WHERE d.ticker_id = :tid
                ORDER BY d.trade_date DESC
                LIMIT 1
                """
            )
            res = await session.execute(sql, {"tid": tid})
            row = res.mappings().first()

            if not row:
                return JSONResponse(
                    {
                        "symbol": symbol.upper(),
                        "trade_date": date.today().isoformat(),
                        "pcr_volume": 0,
                        "pcr_notional": None,
                        "avg_iv": 0,
                        "iv_rank_1y": 0,
                        "skew_25d": 0,
                        "term_slope": 0,
                        "gex_lite": 0,
                        "top_contracts": None,
                        "sentiment": None,
                    }
                )

            return JSONResponse(
                {
                    "symbol": row["symbol"],
                    "trade_date": str(row["trade_date"]),
                    "pcr_volume": _f(row["pcr_volume"]),
                    "pcr_notional": _f(row["pcr_notional"]),
                    "avg_iv": _f(row["avg_iv"]),
                    "iv_rank_1y": _f(row["iv_rank_1y"]),
                    "skew_25d": _f(row["skew_25d"]),
                    "term_slope": _f(row["term_slope"]),
                    "gex_lite": _f(row["gex_lite"]),
                    "top_contracts": row["top_contracts"],
                    "sentiment": row["sentiment"],
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        body = {
            "error": "internal_error",
            "where": "latest",
            "symbol": symbol.upper(),
        }
        if debug:
            body["detail"] = str(e)
        return JSONResponse(status_code=500, content=body)

@router.get("/insights/{symbol}/history")
async def history_insight(
    symbol: str,
    request: Request,
    days: int = Query(90, ge=1, le=365 * 5),
    debug: int = 0,
):
    cutoff_date = date.today() - timedelta(days=days)
    try:
        async with AsyncSessionLocal() as session:
            tid = await _get_ticker_id(session, symbol)
            if not tid:
                raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

            sql = text(
                """
                SELECT d.trade_date,
                       COALESCE(d.pcr_volume, 0)  AS pcr_volume,
                       d.pcr_notional             AS pcr_notional,
                       d.avg_iv                   AS avg_iv,
                       d.iv_rank_1y               AS iv_rank_1y,
                       COALESCE(d.skew_25d, 0)    AS skew_25d,
                       COALESCE(d.term_slope, 0)  AS term_slope,
                       COALESCE(d.gex_lite, 0)    AS gex_lite
                FROM daily_option_insights d
                WHERE d.ticker_id = :tid
                  AND d.trade_date >= :cutoff
                ORDER BY d.trade_date
                """
            )
            res = await session.execute(sql, {"tid": tid, "cutoff": cutoff_date})
            rows = res.mappings().all()

            points = []
            for r in rows:
                points.append(
                    {
                        "trade_date": str(r["trade_date"]),
                        "pcr_volume": _f(r["pcr_volume"]),
                        "pcr_notional": _f(r["pcr_notional"]),
                        "avg_iv": _f(r["avg_iv"]),
                        "iv_rank_1y": _f(r["iv_rank_1y"]),
                        "skew_25d": _f(r["skew_25d"]),
                        "term_slope": _f(r["term_slope"]),
                        "gex_lite": _f(r["gex_lite"]),
                    }
                )

            return JSONResponse({"symbol": symbol.upper(), "points": points})

    except HTTPException:
        raise
    except Exception as e:
        body = {
            "error": "internal_error",
            "where": "history",
            "symbol": symbol.upper(),
            "days": days,
        }
        if debug:
            body["detail"] = str(e)
        return JSONResponse(status_code=500, content=body)

# Mount router on the app (this line ensures the routes are served)
app.include_router(router)

