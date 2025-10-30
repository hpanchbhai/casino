# app/main.py

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.database import get_db, engine
from app.models import Base

import asyncio
from datetime import date
import yfinance as yf
from sqlalchemy import select, text
from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import crud, schemas


from sqlalchemy import text
from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

import math


app = FastAPI(
    title="Stock Tracker API",
    description="Local API for price history, sectors, volume summaries, options flow, and profit simulation",
)

# CORS so frontend (5173) can call backend (8000)
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

@app.on_event("startup")
async def startup():
    # Ensure DB tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.get("/health")
async def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────
# PRICE HISTORY / SYMBOL DATA
# ─────────────────────────────────────────

@app.get("/pricebars")
async def get_top_volume_stocks(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """
    Top traded tickers by total historical volume.
    """
    sql = text("""
        SELECT t.symbol, SUM(p.volume) AS total_volume
        FROM price_bars p
        JOIN tickers t ON p.ticker_id = t.id
        GROUP BY t.symbol
        ORDER BY total_volume DESC
        LIMIT :limit;
    """)
    result = await db.execute(sql, {"limit": limit})
    rows = result.fetchall()

    return [
        {
            "symbol": r[0],
            "total_volume": int(r[1]),
        }
        for r in rows
    ]


@app.get("/pricebars/{symbol}")
async def get_pricebars(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Historical OHLCV for a ticker.
    """
    sql = text("""
        SELECT p.date,
               p.open,
               p.high,
               p.low,
               p.close,
               p.adjusted_close,
               p.volume
        FROM price_bars p
        JOIN tickers t ON p.ticker_id = t.id
        WHERE t.symbol = :symbol
        ORDER BY p.date ASC;
    """)

    try:
        result = await db.execute(sql, {"symbol": symbol.upper()})
        rows = result.fetchall()
    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=500,
            detail=f"DB error while fetching pricebars for {symbol}: {e}",
        )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No price data for {symbol}",
        )

    out = []
    for r in rows:
        bar_date = r[0]  # date
        iso_date = f"{bar_date.isoformat()}T00:00:00"
        out.append({
            "date": iso_date,
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "adjusted_close": float(r[5]),
            "volume": int(r[6]),
        })

    return out


@app.get("/sectors")
async def get_sectors():
    """
    Sector lists for dropdown.
    TODO: make this dynamic via ETF holdings / API fetch.
    """
    data = {
        "AI Stocks": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "PLTR"],
        "Energy Sector": ["XOM", "CVX", "SLB", "HAL", "COP", "BP"],
        "EV / Auto": ["TSLA", "NIO", "RIVN", "GM", "F"],
        "Big Tech": ["AAPL", "MSFT", "AMZN", "GOOGL", "META"],
    }

    return {
        "fetched_at": datetime.utcnow().isoformat(),
        "data": data,
    }


# ─────────────────────────────────────────
# OPTIONS FLOW: TOP 5 TODAY
# ─────────────────────────────────────────

@app.get("/options/activity/top5")
async def get_top5_options_activity(
    symbol: str = Query(..., description="Ticker, e.g. TSLA"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return top 5 most-traded option contracts TODAY for this ticker,
    sorted by volume_today DESC.
    """

    # We consider "today" as current UTC date for now.
    today = datetime.utcnow().date()

    sql = text("""
        SELECT
            t.symbol,
            f.contract_type,
            f.strike,
            f.expiry,
            f.trade_date,
            f.volume_today,
            f.open_interest,
            f.notional_usd,
            f.iv,
            f.avg_volume_20d
        FROM option_flow f
        JOIN tickers t ON f.ticker_id = t.id
        WHERE t.symbol = :symbol
          AND f.trade_date = :today
        ORDER BY f.volume_today DESC
        LIMIT 5;
    """)

    result = await db.execute(sql, {"symbol": symbol.upper(), "today": today})
    rows = result.fetchall()

    # rows may be empty if we haven't ingested that ticker yet today
    out = []
    for r in rows:
        (
            _sym,
            contract_type,
            strike,
            expiry,
            trade_date,
            volume_today,
            open_interest,
            notional_usd,
            iv,
            avg_volume_20d,
        ) = r

        out.append({
            "contract": f"{symbol.upper()} {int(strike)}{'C' if contract_type=='CALL' else 'P'}",
            "expiry": expiry.isoformat(),
            "type": contract_type,
            "strike": f"${int(strike)}",
            "volume": int(volume_today),
            "open_interest": int(open_interest) if open_interest is not None else None,
            "iv": f"{iv:.0f}%" if iv is not None else None,
            "notional": f"${notional_usd/1_000_000:.1f}M" if notional_usd else None,
        })

    return {
        "symbol": symbol.upper(),
        "period": "today",
        "rows": out,
    }


# ─────────────────────────────────────────
# OPTIONS FLOW: UNUSUAL VOLUME
# ─────────────────────────────────────────

@app.get("/options/activity/unusual")
async def get_unusual_options_activity(
    symbol: str = Query(..., description="Ticker, e.g. TSLA"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return contracts for this ticker where today's volume is way above
    20d average. We'll say ratio >= 5x is 'unusual'.
    """

    today = datetime.utcnow().date()

    sql = text("""
        SELECT
            t.symbol,
            f.contract_type,
            f.strike,
            f.expiry,
            f.trade_date,
            f.volume_today,
            f.avg_volume_20d,
            f.volume_ratio,
            f.notional_usd,
            f.iv,
            f.open_interest,
            f.note
        FROM option_flow f
        JOIN tickers t ON f.ticker_id = t.id
        WHERE t.symbol = :symbol
          AND f.trade_date = :today
          AND f.volume_ratio IS NOT NULL
          AND f.volume_ratio >= 5.0
        ORDER BY f.volume_ratio DESC
        LIMIT 10;
    """)

    result = await db.execute(sql, {"symbol": symbol.upper(), "today": today})
    rows = result.fetchall()

    out = []
    for r in rows:
        (
            _sym,
            contract_type,
            strike,
            expiry,
            trade_date,
            volume_today,
            avg_volume_20d,
            volume_ratio,
            notional_usd,
            iv,
            open_interest,
            note,
        ) = r

        out.append({
            "contract": f"{symbol.upper()} {int(strike)}{'C' if contract_type=='CALL' else 'P'}",
            "expiry": expiry.isoformat(),
            "type": contract_type,
            "strike": f"${int(strike)}",
            "volume_today": int(volume_today),
            "avg_volume_20d": float(avg_volume_20d) if avg_volume_20d is not None else None,
            "vol_ratio": float(volume_ratio) if volume_ratio is not None else None,
            "notional": f"${notional_usd/1_000_000:.1f}M" if notional_usd else None,
            "iv": f"{iv:.0f}%" if iv is not None else None,
            "note": note,
        })

    return {
        "symbol": symbol.upper(),
        "rows": out,
    }

# ─────────────────────────────────────────
# PROFIT TARGET SIMULATION
# ─────────────────────────────────────────

@app.get("/options/profit_target")
async def profit_target_calc(
    symbol: str = Query(..., description="Ticker, e.g. TSLA"),
    strike: float = Query(..., description="Option strike price, e.g. 200"),
    option_type: str = Query(..., regex="^(CALL|PUT)$", description="CALL or PUT"),
    premium_paid: float = Query(..., description="Price per contract you pay ($/share)"),
    contracts: int = Query(1, description="# of option contracts you buy"),
    target_profit: float = Query(1000.0, description="Profit target in dollars"),
    days_to_expiry: int = Query(7, description="Rough holding period (days)"),
    underlying_price_now: float = Query(..., description="Current stock price"),
):
    """
    VERY rough math: "How far does the stock need to move for me to hit $X profit?"
    We assume linear delta and ignore time decay changes, IV crush, gamma ramp, etc.
    This is not trading advice, just a directional estimator.
    """

    # 1 option contract controls 100 shares
    notional_shares = contracts * 100

    # naive directional delta guess:
    # calls positive (0.5-ish), puts negative (-0.5-ish)
    # (later: real delta from greeks table!)
    base_delta = 0.5 if option_type == "CALL" else -0.5

    # value_change_per_$1_move_in_stock ~= delta * 100 * contracts
    per_dollar_move_value = base_delta * notional_shares

    # how many $ of stock move do we need to get target_profit?
    # solve target_profit ≈ per_dollar_move_value * move
    if per_dollar_move_value == 0:
        needed_move = math.inf
    else:
        needed_move = target_profit / per_dollar_move_value  # could be neg for PUT

    # estimate target stock price
    est_target_stock_price = underlying_price_now + needed_move

    # rough % move
    pct_move = (
        (est_target_stock_price - underlying_price_now)
        / underlying_price_now
        * 100.0
    )

    # naive breakeven at expiry for calls/puts (classical payoff, ignores theta):
    if option_type == "CALL":
        breakeven = strike + premium_paid
    else:
        # for a put, breakeven is strike - premium
        breakeven = strike - premium_paid

    return {
        "symbol": symbol.upper(),
        "contracts": contracts,
        "option_type": option_type,
        "strike": strike,
        "premium_paid": premium_paid,
        "target_profit": target_profit,
        "underlying_price_now": underlying_price_now,
        "days_to_expiry": days_to_expiry,

        "assumed_delta": base_delta,
        "needed_move_in_stock_dollars": needed_move,
        "est_target_stock_price": est_target_stock_price,
        "est_pct_move_in_underlying": pct_move,
        "breakeven_at_expiry": breakeven,
        "disclaimer": (
            "Ignores real greeks, IV changes, and theta. "
            "Use only for rough intuition, not trade decisions."
        ),
    }

# --- DAILY % CHANGE (/pricebars/{symbol}/deltas) ---

@app.get("/pricebars/{symbol}/deltas")
async def price_deltas(symbol: str, db: AsyncSession = Depends(get_db)):
    """
    Return daily % change for a symbol, newest last.
    """
    # ensure we have bars first
    bars = await db.execute(
        text("""
            SELECT date, close
            FROM price_bars pb
            JOIN tickers t ON t.id = pb.ticker_id
            WHERE t.symbol = :sym
            ORDER BY date ASC
        """),
        {"sym": symbol.upper()},
    )
    rows = bars.mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No price data for symbol")

    out = []
    prev_close = None
    for r in rows:
        c = float(r["close"])
        pct = None if prev_close in (None, 0) else (c - prev_close) / prev_close * 100.0
        out.append({"date": str(r["date"]), "close": c, "pct_change": pct, "volume": 0})
        prev_close = c
    return out

async def _has_pricebars(db: AsyncSession, symbol: str) -> bool:
    q = await db.execute(text("""
        SELECT 1
        FROM price_bars pb
        JOIN tickers t ON t.id = pb.ticker_id
        WHERE t.symbol = :sym
        LIMIT 1
    """), {"sym": symbol.upper()})
    return q.first() is not None


async def _ingest_symbol(db: AsyncSession, symbol: str, period: str = "5y") -> int:
    """
    Pulls daily OHLCV from yfinance and upserts into price_bars.
    Returns number of rows upserted.
    """
    symbol = symbol.upper()
    # ensure ticker row
    t = await crud.get_ticker_by_symbol(db, symbol)
    if not t:
        t = await crud.create_ticker(db, schemas.TickerCreate(symbol=symbol))

    df = yf.download(symbol, period=period, interval="1d", progress=False)
    if df is None or df.empty:
        return 0

    rows = []
    for idx, row in df.iterrows():
        try:
            rows.append({
                "date": idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "adjusted_close": float(row.get("Adj Close", row["Close"])),
                "volume": int(row["Volume"]),
            })
        except Exception:
            # skip malformed rows
            continue

    if not rows:
        return 0

    await crud.upsert_pricebars(db, t.id, rows)
    return len(rows)

@app.post("/ensure/pricebars/{symbol}")
async def ensure_pricebars(symbol: str, period: str = "5y", db: AsyncSession = Depends(get_db)):
    """
    If price bars for `symbol` don't exist, fetch & store them.
    Always returns a JSON status.
    """
    symbol = symbol.upper()
    if await _has_pricebars(db, symbol):
        return {"status": "ok", "symbol": symbol, "ingested": 0, "message": "Already present"}

    n = await _ingest_symbol(db, symbol, period=period)
    if n == 0:
        raise HTTPException(status_code=502, detail=f"Failed to ingest {symbol}")
    return {"status": "ok", "symbol": symbol, "ingested": n}
