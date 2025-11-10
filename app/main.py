# app/main.py
# FastAPI core app — mounts options router and provides stable endpoints used by dashboard & curl tests.
# Guardrails honored:
# - Full-file delivery (no diffs/snippets).
# - Does not remove working features; only adds/fixes.
# - Stable JSON shapes (arrays/objects, never scalars where arrays expected).
# - Safe SQL with bind params (no string->date coercion issues).
# - Compatible with existing database.py (AsyncSessionLocal).

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal  # must exist per your repo
# Mount the options router implemented earlier
try:
    from app.routes_options import router as options_router
except Exception:
    options_router = None  # We'll conditionally include below

app = FastAPI(title="Core API", version="1.0.0")

# CORS for dashboard at localhost:5173 (Vite)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Helpers
# ----------------------------

def _today() -> date:
    return date.today()

def _days_ago(n: int) -> date:
    return _today() - timedelta(days=n)

CONFIG_DIR = Path("config")
CUSTOM_LISTS_PATH = CONFIG_DIR / "custom_lists.json"

DEFAULT_CATEGORIES = [
    {"id": "top_volume", "label": "Top 10 Traded by Volume"},
    {"id": "aiSector",   "label": "AI Sector"},
    {"id": "energy",     "label": "Energy Sector"},
    {"id": "custom",     "label": "My Custom List"},
]

DEFAULT_AI = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "SMCI", "AMD", "TSLA", "PLTR"]
DEFAULT_ENERGY = ["XOM", "CVX", "SLB", "COP", "EOG", "PSX", "VLO", "MPC", "HAL", "BKR"]

def _load_custom_list() -> List[str]:
    try:
        if CUSTOM_LISTS_PATH.exists():
            data = json.loads(CUSTOM_LISTS_PATH.read_text())
            syms = data.get("custom", [])
            if isinstance(syms, list):
                # Normalize & dedupe
                return sorted({str(s).upper().strip() for s in syms if str(s).strip()})
    except Exception:
        pass
    # Default starter list
    return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]

# ----------------------------
# Health
# ----------------------------

@app.get("/health")
async def health() -> Dict[str, Any]:
    # Simple ping; if we can open a session, assume DB reachable
    try:
        async with AsyncSessionLocal() as _:
            pass
        engine_name = "postgres"
    except Exception:
        engine_name = "unknown"
    return {"status": "ok", "service": "core", "engine": engine_name}

# ----------------------------
# Categories
# ----------------------------

@app.get("/categories")
async def list_categories() -> List[Dict[str, str]]:
    return DEFAULT_CATEGORIES

@app.get("/category/{category_id}/symbols")
async def category_symbols(
    category_id: str,
    lookback_days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=200),
) -> List[str]:
    cid = category_id.strip()

    if cid == "top_volume":
        start_date = _days_ago(lookback_days)
        sql = text("""
            SELECT t.symbol AS symbol,
                   COALESCE(SUM(COALESCE(pb.volume,0)),0)::bigint AS total_volume
            FROM price_bars pb
            JOIN tickers t ON t.id = pb.ticker_id
            WHERE pb.date >= :start_date
            GROUP BY t.symbol
            ORDER BY total_volume DESC
            LIMIT :limit
        """)
        async with AsyncSessionLocal() as session:  # type: AsyncSession
            res = await session.execute(sql.bindparams(start_date=start_date, limit=limit))
            rows = res.mappings().all()
            return [r["symbol"] for r in rows]

    if cid == "aiSector":
        return DEFAULT_AI[:limit]

    if cid == "energy":
        return DEFAULT_ENERGY[:limit]

    if cid == "custom":
        custom_syms = _load_custom_list()
        return custom_syms[:limit]

    # Unknown category -> empty (stable list)
    return []

# ----------------------------
# Price bars
# ----------------------------

# Resolve ticker_id by symbol
GET_TICKER_ID_SQL = text("SELECT id FROM tickers WHERE symbol = :symbol")

# Latest close
LATEST_CLOSE_SQL = text("""
    SELECT pb.date, pb.close
    FROM price_bars pb
    JOIN tickers t ON t.id = pb.ticker_id
    WHERE t.symbol = :symbol
    ORDER BY pb.date DESC
    LIMIT 1
""")

# Bars since date
BARS_SINCE_SQL = text("""
    SELECT pb.date, pb.close, pb.volume
    FROM price_bars pb
    JOIN tickers t ON t.id = pb.ticker_id
    WHERE t.symbol = :symbol
      AND pb.date >= :start_date
    ORDER BY pb.date ASC
""")

@app.get("/pricebars/{symbol}/latest")
async def latest_close(symbol: str) -> Dict[str, Any]:
    s = symbol.upper().strip()
    async with AsyncSessionLocal() as session:  # type: AsyncSession
        res = await session.execute(LATEST_CLOSE_SQL.bindparams(symbol=s))
        row = res.fetchone()
        if not row:
            # Stable object shape even if unknown
            return {"date": None, "close": None}
        return {"date": row[0].isoformat(), "close": float(row[1])}

@app.get("/pricebars/{symbol}")
async def pricebars(
    symbol: str,
    days: int = Query(90, ge=1, le=3650),  # 1 day - 10 years
) -> List[Dict[str, Any]]:
    s = symbol.upper().strip()
    since = _days_ago(days)
    async with AsyncSessionLocal() as session:  # type: AsyncSession
        res = await session.execute(BARS_SINCE_SQL.bindparams(symbol=s, start_date=since))
        rows = res.mappings().all()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "date": r["date"].isoformat(),
                "close": float(r["close"]),
                "volume": int(r["volume"]) if r["volume"] is not None else 0,
            })
        return out

@app.get("/pricebars/{symbol}/deltas")
async def price_deltas(
    symbol: str,
    days: int = Query(90, ge=2, le=3650),
) -> List[Dict[str, Any]]:
    # Compute daily pct change vs previous close
    bars = await pricebars(symbol, days)
    out: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for b in bars:
        c = b["close"]
        pct = None
        if prev_close is not None and prev_close != 0:
            pct = (c - prev_close) / prev_close * 100.0
        out.append({
            "date": b["date"],
            "close": c,
            "volume": b["volume"],
            "pct_change": pct,
        })
        prev_close = c
    return out

# ----------------------------
# Mount options router (array-safe)
# ----------------------------

if options_router is not None:
    app.include_router(options_router)

