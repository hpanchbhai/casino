# app/category_routes.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, List, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="", tags=["categories"])

# ---------- Configurable locations ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_LIST_FILE = PROJECT_ROOT / "data" / "custom_list.json"   # optional

# ---------- Response models ----------
class Category(BaseModel):
    id: str
    name: str

# ---------- Providers (return List[str] of symbols) ----------
def _load_custom_list() -> List[str]:
    """
    Priority:
      1) data/custom_list.json -> ["AAPL","MSFT",...]
      2) ENV CUSTOM_LIST -> "AAPL,MSFT,NVDA"
      3) fallback curated list
    """
    try:
        if CUSTOM_LIST_FILE.exists():
            with CUSTOM_LIST_FILE.open() as f:
                data = json.load(f)
            if isinstance(data, dict) and "symbols" in data:
                return [s.upper() for s in data["symbols"]]
            if isinstance(data, list):
                return [str(s).upper() for s in data]
    except Exception:
        pass

    env = os.getenv("CUSTOM_LIST", "")
    if env.strip():
        return [s.strip().upper() for s in env.split(",") if s.strip()]

    # fallback
    return ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA"]

def _top_volume_symbols() -> List[str]:
    """
    TODO: Replace this stub with real DB-backed top-traded logic.
    """
    return ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "AVGO", "NFLX", "AMD", "GOOGL"]

def _ai_sector_symbols() -> List[str]:
    return ["NVDA", "AMD", "MSFT", "GOOGL", "META", "AVGO", "SMCI", "PLTR", "TSLA"]

def _energy_symbols() -> List[str]:
    return ["XOM", "CVX", "COP", "SLB", "HAL", "BP", "SHEL", "EOG", "MPC", "VLO"]

# ---------- Registry ----------
CATEGORY_DEFS: List[Category] = [
    Category(id="top_volume", name="Top 10 Traded by Volume"),
    Category(id="custom",     name="My Custom List"),
    Category(id="ai_sector",  name="AI Stocks"),
    Category(id="energy",     name="Energy Sector"),
]

PROVIDERS: Dict[str, Callable[[], List[str]]] = {
    "top_volume": _top_volume_symbols,
    "custom":     _load_custom_list,
    "ai_sector":  _ai_sector_symbols,
    "energy":     _energy_symbols,
}

# ---------- Routes ----------
@router.get("/categories", response_model=List[Category])
async def list_categories():
    return CATEGORY_DEFS

@router.get("/category/{category_id}/symbols", response_model=List[str])
async def symbols_for_category(category_id: str):
    cid = category_id.strip().lower()
    if cid not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category_id}")
    try:
        symbols = PROVIDERS[cid]()
        # dedupe + preserve order
        seen = set()
        out: List[str] = []
        for s in symbols:
            u = s.upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"category->symbols failed: {e}")

