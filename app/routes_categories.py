# app/routes_categories.py
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

router = APIRouter()

# Static catalog shown to the UI
_CATEGORIES = [
    {"id": "top_volume", "label": "Top 10 Traded by Volume"},
    {"id": "aiSector", "label": "AI Sector"},
    {"id": "energy", "label": "Energy Sector"},
    {"id": "custom", "label": "My Custom List"},
]


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/categories", response_model=List[dict])
async def list_categories():
    """
    Return the category catalog the dashboard expects.
    """
    return _CATEGORIES


@router.get("/category/{category_id}/symbols", response_model=List[str])
async def category_symbols(
    category_id: str,
    session: AsyncSession = Depends(get_session),
    lookback_days: int = Query(30, ge=1, le=365 * 5, description="Window for top_volume"),
    limit: int = Query(10, ge=1, le=200, description="Max symbols for top_volume"),
):
    """
    Map category -> list of symbols.

    top_volume : DB-driven based on total volume over `lookback_days`
    aiSector   : curated list
    energy     : curated list
    custom     : read from config/custom_symbols.txt (one ticker per line)
    """
    cat = category_id.strip()

    if cat == "top_volume":
        since: date = date.today() - timedelta(days=lookback_days)
        sql = text(
            """
            SELECT t.symbol AS symbol,
                   COALESCE(SUM(COALESCE(pb.volume, 0)), 0)::bigint AS total_volume
            FROM price_bars pb
            JOIN tickers t ON t.id = pb.ticker_id
            WHERE pb.date >= :since
            GROUP BY t.symbol
            ORDER BY total_volume DESC
            LIMIT :limit
            """
        )
        res = await session.execute(sql, {"since": since, "limit": limit})
        rows = res.mappings().all()
        return [r["symbol"] for r in rows]

    if cat == "aiSector":
        return [
            "NVDA", "MSFT", "GOOGL", "AMZN", "META",
            "AVGO", "SMCI", "AMD", "TSLA", "PLTR",
        ]

    if cat == "energy":
        return [
            "XOM", "CVX", "SLB", "COP", "EOG",
            "PSX", "VLO", "MPC", "HAL", "BKR",
        ]

    if cat == "custom":
        cfg_path = Path("config/custom_symbols.txt")
        if not cfg_path.exists():
            return []
        tickers: List[str] = []
        for line in cfg_path.read_text().splitlines():
            s = line.strip().upper()
            if s and s.isalnum():
                tickers.append(s)
        # de-dupe, preserve order
        seen = set()
        unique = [t for t in tickers if not (t in seen or seen.add(t))]
        return unique

    raise HTTPException(status_code=404, detail=f"Unknown category: {category_id}")

