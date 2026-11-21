import math
import os
import re
import sqlite3
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from scipy.stats import norm
import requests as _http

import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ─── Index definitions ──────────────────────────────────────
INDICES = {
    "watchlist": {
        "name": "My Watchlist",
        "file": os.path.join("static", "stocks_to_track"),
        "icon": "⭐",
    },
    "sp500": {
        "name": "S&P 500",
        "file": os.path.join("data", "sp500.txt"),
        "icon": "🏛️",
    },
    "nasdaq100": {
        "name": "NASDAQ 100",
        "file": os.path.join("data", "nasdaq100.txt"),
        "icon": "💻",
    },
    "dowjones": {
        "name": "Dow Jones 30",
        "file": os.path.join("data", "dowjones.txt"),
        "icon": "🏭",
    },
    "russell2000": {
        "name": "Russell 2000",
        "file": os.path.join("data", "russell2000.txt"),
        "icon": "📊",
    },
    "spdr_etfs": {
        "name": "SPDR & Major ETFs",
        "file": os.path.join("data", "spdr_etfs.txt"),
        "icon": "📦",
    },
}

# ─── Simple in-memory cache ─────────────────────────────────
_cache = {}
CACHE_TTL = 300  # 5 minutes

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "db", "stocks.db")

HISTORY_PERIOD_DAYS = {
    "5d": 7,
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 366,
    "5y": 366 * 5,
}

INDEX_BENCHMARKS = {
    "sp500": ("S&P 500", "SPY"),
    "nasdaq100": ("NASDAQ 100", "QQQ"),
    "russell2000": ("Russell 2000", "IWM"),
    "dowjones": ("Dow Jones", "DIA"),
    "spdr_etfs": ("SPDR & Major ETFs", None),
    "custom": ("Custom Index / ETF", None),
}

INDEX_ADDITION_LIST_KEYS = ("sp500", "nasdaq100", "russell2000", "dowjones", "spdr_etfs")


def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _index_definition_fallback(index_key: str) -> dict:
    idx = INDICES.get(index_key, {})
    name, benchmark = INDEX_BENCHMARKS.get(index_key, (idx.get("name", index_key.upper()), None))
    return {
        "key": index_key,
        "name": name or idx.get("name", index_key.upper()),
        "benchmark_ticker": benchmark,
        "icon": idx.get("icon", ""),
        "source_url": None,
        "membership_source": "fallback",
        "active": 1,
    }


def _index_definitions(conn: sqlite3.Connection | None = None, include_inactive: bool = False) -> list[dict]:
    rows: list[sqlite3.Row] = []
    close_conn = False
    if os.path.exists(DB_PATH):
        try:
            if conn is None:
                conn = get_db_conn()
                close_conn = True
            where = "1 = 1" if include_inactive else "active = 1"
            rows = conn.execute(
                f"""
                SELECT key, name, benchmark_ticker, icon, source_url, membership_source, active
                FROM index_definitions
                WHERE {where}
                ORDER BY name ASC
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            if close_conn and conn is not None:
                conn.close()

    if rows:
        return [dict(row) for row in rows]
    keys = [key for key in INDEX_ADDITION_LIST_KEYS if key in INDICES or key in INDEX_BENCHMARKS]
    return [_index_definition_fallback(key) for key in keys]


def _index_definition(index_key: str, conn: sqlite3.Connection | None = None) -> dict | None:
    key = (index_key or "").strip().lower()
    if not key:
        return None
    close_conn = False
    if os.path.exists(DB_PATH):
        try:
            if conn is None:
                conn = get_db_conn()
                close_conn = True
            row = conn.execute(
                """
                SELECT key, name, benchmark_ticker, icon, source_url, membership_source, active
                FROM index_definitions
                WHERE key = ? AND active = 1
                """,
                (key,),
            ).fetchone()
            if row:
                return dict(row)
        except sqlite3.Error:
            pass
        finally:
            if close_conn and conn is not None:
                conn.close()
    if key in INDEX_BENCHMARKS or key in INDICES:
        return _index_definition_fallback(key)
    return None


def _history_start_date(period: str) -> str | None:
    if period in ("max", "all"):
        return None
    if period == "ytd":
        return date(date.today().year, 1, 1).isoformat()
    days = HISTORY_PERIOD_DAYS.get(period)
    if not days:
        return None
    return (date.today() - timedelta(days=days)).isoformat()


def _can_use_daily_history_db(period: str, interval: str) -> bool:
    if interval not in ("1d", "1wk", "1w", "1week"):
        return False
    return period in HISTORY_PERIOD_DAYS or period in ("ytd", "max", "all")


def _weekly_close_rows(rows: list[sqlite3.Row]) -> list[dict]:
    weekly: dict[tuple[int, int], dict] = {}
    for row in rows:
        d = date.fromisoformat(row["date"])
        year_week = d.isocalendar()[:2]
        weekly[year_week] = {"date": row["date"], "close": round(float(row["close"]), 2)}
    return list(weekly.values())


def get_history_from_db(ticker: str, period: str, interval: str) -> list[dict]:
    if not _can_use_daily_history_db(period, interval) or not os.path.exists(DB_PATH):
        return []

    start_date = _history_start_date(period)
    where = "ticker = ? AND close IS NOT NULL"
    params: list[str] = [ticker]
    if start_date:
        where += " AND date >= ?"
        params.append(start_date)

    with get_db_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT date, close
            FROM daily_prices
            WHERE {where}
            ORDER BY date ASC
            """,
            params,
        ).fetchall()

    if not rows:
        return []
    if interval in ("1wk", "1w", "1week"):
        return _weekly_close_rows(rows)
    return [{"date": row["date"], "close": round(float(row["close"]), 2)} for row in rows]


def _price_on_or_after(conn: sqlite3.Connection, ticker: str, target_date: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT date, close
        FROM daily_prices
        WHERE ticker = ? AND date >= ? AND close IS NOT NULL
        ORDER BY date ASC
        LIMIT 1
        """,
        (ticker, target_date),
    ).fetchone()


def _latest_price(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT date, close
        FROM daily_prices
        WHERE ticker = ? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()


def _return_between(start_price: float | None, end_price: float | None) -> float | None:
    if start_price is None or end_price is None or start_price == 0:
        return None
    return round((end_price / start_price - 1) * 100, 2)


def _annualized_return(start_price: float, end_price: float, days_held: int) -> float | None:
    if start_price <= 0 or end_price <= 0 or days_held <= 0:
        return None
    return round(((end_price / start_price) ** (365 / days_held) - 1) * 100, 2)


def _forward_return(conn: sqlite3.Connection, ticker: str, start_row: sqlite3.Row, days: int) -> float | None:
    target = (date.fromisoformat(start_row["date"]) + timedelta(days=days)).isoformat()
    end_row = _price_on_or_after(conn, ticker, target)
    if not end_row:
        return None
    return _return_between(float(start_row["close"]), float(end_row["close"]))


def _period_key(price_date: date, cadence: str) -> str:
    if cadence == "monthly":
        return price_date.strftime("%Y-%m")
    if cadence == "quarterly":
        quarter = (price_date.month - 1) // 3 + 1
        return f"{price_date.year}-Q{quarter}"
    return str(price_date.year)


def _periodic_returns_since(conn: sqlite3.Connection, ticker: str, start_date: str) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT date, close
        FROM daily_prices
        WHERE ticker = ? AND date >= ? AND close IS NOT NULL
        ORDER BY date ASC
        """,
        (ticker, start_date),
    ).fetchall()
    if len(rows) < 2:
        return {"monthly": [], "quarterly": [], "yearly": []}

    output: dict[str, list[dict]] = {}
    for cadence in ("monthly", "quarterly", "yearly"):
        buckets: dict[str, dict] = {}
        for row in rows:
            d = date.fromisoformat(row["date"])
            key = _period_key(d, cadence)
            bucket = buckets.setdefault(
                key,
                {
                    "period": key,
                    "start_date": row["date"],
                    "start_price": float(row["close"]),
                    "end_date": row["date"],
                    "end_price": float(row["close"]),
                },
            )
            bucket["end_date"] = row["date"]
            bucket["end_price"] = float(row["close"])

        output[cadence] = [
            {
                **bucket,
                "start_price": round(bucket["start_price"], 2),
                "end_price": round(bucket["end_price"], 2),
                "return_pct": _return_between(bucket["start_price"], bucket["end_price"]),
            }
            for bucket in buckets.values()
        ]
    return output


def _price_series_since(conn: sqlite3.Connection, ticker: str, start_date: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT date, close
        FROM daily_prices
        WHERE ticker = ? AND date >= ? AND close IS NOT NULL
        ORDER BY date ASC
        """,
        (ticker.upper(), start_date),
    ).fetchall()
    if not rows:
        return []

    start_price = float(rows[0]["close"])
    return [
        {
            "date": row["date"],
            "close": round(float(row["close"]), 2),
            "return_pct": _return_between(start_price, float(row["close"])),
        }
        for row in rows
    ]


def _index_addition_record_from_request(args) -> dict | tuple[dict, int]:
    ticker = args.get("ticker", "").strip().upper()
    added_date = args.get("added_date", "").strip()
    index_key = args.get("index_key", args.get("index", "custom")).strip().lower() or "custom"
    index_def = _index_definition(index_key) or _index_definition_fallback(index_key)
    index_name = index_def["name"]
    default_benchmark = index_def.get("benchmark_ticker")
    benchmark_ticker = args.get("benchmark_ticker", default_benchmark or "").strip().upper() or None

    if not ticker:
        return {"error": "ticker is required"}, 400
    if not added_date:
        return {"error": "added_date is required"}, 400
    try:
        date.fromisoformat(added_date)
    except ValueError:
        return {"error": "added_date must be YYYY-MM-DD"}, 400

    return {
        "ticker": ticker,
        "index_key": index_key,
        "index_name": args.get("index_name", index_name).strip() or index_name,
        "benchmark_ticker": benchmark_ticker,
        "added_date": added_date,
        "source_note": "ad hoc",
    }


def _index_candidates_for_ticker(ticker: str) -> list[dict]:
    ticker = ticker.upper().strip()
    if os.path.exists(DB_PATH):
        with get_db_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT
                    im.index_key,
                    COALESCE(id.name, im.index_key) AS index_name,
                    id.benchmark_ticker,
                    id.icon,
                    CASE WHEN ia.ticker IS NULL THEN 0 ELSE 1 END AS has_addition_record
                FROM index_memberships im
                JOIN symbols s ON s.id = im.symbol_id
                LEFT JOIN index_definitions id ON id.key = im.index_key
                LEFT JOIN (
                    SELECT DISTINCT ticker, index_key
                    FROM index_additions
                    WHERE active = 1
                      AND added_date <= date('now')
                ) ia ON ia.ticker = s.ticker AND ia.index_key = im.index_key
                WHERE s.ticker = ?
                  AND im.active = 1
                  AND im.effective_to IS NULL
                  AND COALESCE(id.active, 1) = 1
                ORDER BY has_addition_record DESC, COALESCE(id.name, im.index_key)
                """,
                (ticker,),
            ).fetchall()
        matches = [
            {
                "key": row["index_key"],
                "name": row["index_name"],
                "icon": row["icon"] or INDICES.get(row["index_key"], {}).get("icon", ""),
                "benchmark_ticker": row["benchmark_ticker"],
                "has_addition_record": bool(row["has_addition_record"]),
            }
            for row in rows
        ]
        if matches:
            return matches

    matches: list[dict] = []
    for key in INDEX_ADDITION_LIST_KEYS:
        idx = INDICES.get(key)
        if not idx:
            continue
        tickers = {t.upper() for t in load_tickers(idx["file"])}
        if ticker in tickers:
            index_name, benchmark = INDEX_BENCHMARKS.get(key, (idx["name"], None))
            matches.append({
                "key": key,
                "name": index_name or idx["name"],
                "icon": idx.get("icon", ""),
                "benchmark_ticker": benchmark,
            })
    return matches


def _saved_index_addition_records(
    conn: sqlite3.Connection,
    index_key: str,
    limit: int,
    ticker: str | None = None,
    include_upcoming: bool = False,
) -> list[dict]:
    where = "active = 1"
    params: list = []
    if not include_upcoming:
        where += " AND added_date <= ?"
        params.append(date.today().isoformat())
    if ticker:
        where += " AND ticker = ?"
        params.append(ticker.upper())
    if index_key != "all":
        where += " AND index_key = ?"
        params.append(index_key)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT ticker, index_key, index_name, benchmark_ticker, added_date, source_note
        FROM index_additions
        WHERE {where}
        ORDER BY added_date DESC, ticker ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _build_index_addition_performance(conn: sqlite3.Connection, record: dict) -> dict:
    ticker = record["ticker"].upper()
    start_row = _price_on_or_after(conn, ticker, record["added_date"])
    latest_row = _latest_price(conn, ticker)

    item = dict(record)
    item["ticker"] = ticker
    item["data_source"] = "daily_prices"

    if not start_row or not latest_row:
        item["error"] = "No stored price data available for ticker/date."
        return item

    start_price = float(start_row["close"])
    latest_price = float(latest_row["close"])
    days_held = (date.fromisoformat(latest_row["date"]) - date.fromisoformat(start_row["date"])).days
    total_return = _return_between(start_price, latest_price)

    item.update({
        "start_date": start_row["date"],
        "latest_date": latest_row["date"],
        "start_price": round(start_price, 2),
        "latest_price": round(latest_price, 2),
        "days_held": days_held,
        "years_held": round(days_held / 365, 2) if days_held >= 0 else None,
        "total_return_pct": total_return,
        "annualized_return_pct": _annualized_return(start_price, latest_price, days_held),
        "return_1m_pct": _forward_return(conn, ticker, start_row, 30),
        "return_3m_pct": _forward_return(conn, ticker, start_row, 90),
        "return_6m_pct": _forward_return(conn, ticker, start_row, 182),
        "return_1y_pct": _forward_return(conn, ticker, start_row, 365),
        "return_2y_pct": _forward_return(conn, ticker, start_row, 730),
        "periodic_returns": _periodic_returns_since(conn, ticker, start_row["date"]),
        "price_series": _price_series_since(conn, ticker, start_row["date"]),
    })

    benchmark = record.get("benchmark_ticker")
    if benchmark:
        bench_start = _price_on_or_after(conn, benchmark, record["added_date"])
        bench_latest = _latest_price(conn, benchmark)
        if bench_start and bench_latest:
            bench_return = _return_between(float(bench_start["close"]), float(bench_latest["close"]))
            item.update({
                "benchmark_ticker": benchmark,
                "benchmark_return_pct": bench_return,
                "excess_return_pct": round(total_return - bench_return, 2) if total_return is not None and bench_return is not None else None,
                "benchmark_series": _price_series_since(conn, benchmark, bench_start["date"]),
            })
        else:
            item["benchmark_note"] = f"{benchmark} not available in daily_prices."

    return item


def load_tickers(file_path: str) -> list[str]:
    """Load stock tickers from a text file (one per line, skips blanks/headers)."""
    tickers = []
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file_path)
    try:
        with open(full_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("stock list"):
                    continue
                tickers.append(line)
    except FileNotFoundError:
        print(f"❌ File not found: {full_path}")
    return tickers


def compute_rsi(data: pd.Series, period: int = 14) -> float | None:
    """Compute RSI using Wilder's smoothing (exponential moving average).

    This matches the standard RSI(14) used by Robinhood, TradingView,
    and most professional charting platforms.
    """
    if data is None or len(data) < period + 1:
        return None

    delta = data.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    # Wilder's smoothing: EMA with alpha = 1/period (same as com = period - 1)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    last_rsi = rsi.dropna()
    if last_rsi.empty:
        return None
    return round(float(last_rsi.iloc[-1]), 2)


# ─── Black-Scholes Greeks ───────────────────────────────────
RISK_FREE_RATE = 0.043  # ~4.3% (approximate 10-yr Treasury yield)


def compute_greeks(spot: float, strike: float, T: float, sigma: float, option_type: str) -> dict:
    """Compute delta and theta using the Black-Scholes model.

    Args:
        spot:  current underlying price
        strike: option strike price
        T:     time to expiration in years (must be > 0)
        sigma: implied volatility (as a decimal, e.g. 0.30 for 30%)
        option_type: 'Call' or 'Put'

    Returns:
        dict with 'delta' and 'theta' (theta is per-day, in dollars per share)
    """
    r = RISK_FREE_RATE

    if T <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return {"delta": 0.0, "theta": 0.0}

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "Call":
        delta = norm.cdf(d1)
        theta = (
            -spot * norm.pdf(d1) * sigma / (2 * math.sqrt(T))
            - r * strike * math.exp(-r * T) * norm.cdf(d2)
        ) / 365  # per-day
    else:  # Put
        delta = norm.cdf(d1) - 1
        theta = (
            -spot * norm.pdf(d1) * sigma / (2 * math.sqrt(T))
            + r * strike * math.exp(-r * T) * norm.cdf(-d2)
        ) / 365  # per-day

    return {"delta": round(delta, 4), "theta": round(theta, 4)}


def fetch_single_stock(ticker: str) -> dict | None:
    """Fetch price history and compute RSI for a single ticker."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y")

        if hist is None or hist.empty or "Close" not in hist.columns:
            return None

        close = hist["Close"].dropna()
        if len(close) < 15:
            return None

        rsi = compute_rsi(close)
        if rsi is None:
            return None

        last_price = round(float(close.iloc[-1]), 2)
        prev_price = round(float(close.iloc[-2]), 2) if len(close) >= 2 else last_price
        change_pct = round(((last_price - prev_price) / prev_price) * 100, 2) if prev_price != 0 else 0

        return {
            "ticker": ticker,
            "price": last_price,
            "rsi": rsi,
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"⚠️  {ticker}: {e}")
        return None


def fetch_rsi_for_tickers(tickers: list[str]) -> list[dict]:
    """Fetch RSI data for a list of tickers using parallel requests."""
    results = []
    print(f"⏳ Fetching data for {len(tickers)} stocks...")

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_single_stock, t): t for t in tickers}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                    print(f"  ✅ {result['ticker']}: RSI={result['rsi']}")
            except Exception as e:
                print(f"  ⚠️ {ticker}: {e}")

    print(f"✅ Fetched {len(results)}/{len(tickers)} stocks")
    return results


def get_index_data(index_key: str) -> dict | None:
    """Get RSI data for an index, using cache if available."""
    if index_key in _cache:
        cached_time, cached_data = _cache[index_key]
        if time.time() - cached_time < CACHE_TTL:
            print(f"📦 Cache hit for {index_key}")
            return cached_data

    index_info = INDICES.get(index_key)
    if not index_info:
        return None

    tickers = load_tickers(index_info["file"])
    if not tickers:
        return None

    stocks = fetch_rsi_for_tickers(tickers)
    if not stocks:
        return None

    sorted_by_rsi = sorted(stocks, key=lambda x: x["rsi"])

    data = {
        "name": index_info["name"],
        "icon": index_info["icon"],
        "lowest_rsi": sorted_by_rsi[:5],
        "highest_rsi": sorted_by_rsi[-5:][::-1],
        "all_stocks": sorted_by_rsi,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(stocks),
    }

    _cache[index_key] = (time.time(), data)
    return data


# ─── Routes ─────────────────────────────────────────────────


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/indices")
def get_indices_list():
    """Return list of available indices."""
    return jsonify([
        {"key": k, "name": v["name"], "icon": v["icon"]}
        for k, v in INDICES.items()
    ])


@app.route("/api/rsi/<index_key>")
def get_rsi_data(index_key: str):
    """API endpoint returning RSI data for a specific index."""
    if index_key not in INDICES:
        return jsonify({"error": f"Unknown index: {index_key}"}), 404

    data = get_index_data(index_key)
    if not data:
        return jsonify({"error": f"Could not fetch data for {index_key}"}), 500

    return jsonify(data)


@app.route("/api/history")
def get_history():
    """Return historical close prices for a single ticker.

    Query params:
      - ticker: symbol (required)
      - period: e.g. 1d,5d,1mo,3mo,6mo,1y,5y (optional, default 1mo)
      - interval: e.g. 1m,2m,5m,15m,1d,1wk (optional, auto-picked if not provided)
    """
    from flask import request

    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    period = request.args.get("period", "1mo")
    interval = request.args.get("interval")

    # Pick a sensible default interval if not provided
    if not interval:
        if period == "1d":
            interval = "5m"
        elif period == "5d":
            interval = "15m"
        elif period in ("1mo", "3mo", "6mo"):
            interval = "1d"
        elif period == "1y":
            interval = "1d"
        elif period == "5y":
            interval = "1wk"
        else:
            interval = "1d"

    cache_key = f"history:{ticker}:{period}:{interval}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return jsonify(cached_data)

    db_data = get_history_from_db(ticker, period, interval)
    if db_data:
        payload = {
            "ticker": ticker,
            "period": period,
            "interval": interval,
            "data": db_data,
            "source": "db",
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    def _fetch_history() -> pd.DataFrame:
        t = yf.Ticker(ticker)
        data = t.history(period=period, interval=interval)
        if data is not None and not data.empty:
            return data
        # fallback: try without interval (let yfinance decide)
        data = t.history(period=period)
        if data is not None and not data.empty:
            return data
        # fallback: use download with progress off
        data = yf.download(ticker, period=period, interval=interval, progress=False)
        if data is not None and not data.empty:
            return data
        return pd.DataFrame()

    try:
        hist = _fetch_history()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if hist is None or hist.empty or "Close" not in hist.columns:
        return jsonify({"error": "no data found for ticker"}), 404

    # Build arrays of ISO dates and close prices, skipping NaN values
    # (yfinance can return NaN for close on some rows, and NaN is not valid JSON)
    hist = hist.dropna(subset=["Close"])
    data = []
    for idx, row in hist.iterrows():
        try:
            ts = idx.isoformat()
        except Exception:
            ts = str(idx)
        data.append({"date": ts, "close": round(float(row["Close"]), 2)})

    payload = {
        "ticker": ticker,
        "period": period,
        "interval": interval,
        "data": data,
        "source": "yfinance",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


@app.route("/api/index-additions/performance")
def get_index_addition_performance():
    """Return performance since stocks were added to an index.

    Modes:
      - Saved records: /api/index-additions/performance?index_key=sp500
      - Ad hoc: /api/index-additions/performance?ticker=NVDA&index_key=sp500&added_date=2020-01-01
    """
    if not os.path.exists(DB_PATH):
        return jsonify({"error": "database not found; run db/init_db.py first"}), 500

    ticker = request.args.get("ticker", "").strip().upper()
    added_date = request.args.get("added_date", "").strip()
    index_key = request.args.get("index_key", request.args.get("index", "all")).strip().lower() or "all"
    try:
        limit = min(max(int(request.args.get("limit", "100")), 1), 500)
    except ValueError:
        limit = 100
    include_upcoming = request.args.get("include_upcoming", "").strip().lower() in {"1", "true", "yes"}

    candidates = _index_candidates_for_ticker(ticker) if ticker else []

    with get_db_conn() as conn:
        if ticker and added_date:
            record = _index_addition_record_from_request(request.args)
            if isinstance(record, tuple):
                body, status = record
                return jsonify(body), status
            records = [record]
        else:
            records = _saved_index_addition_records(
                conn,
                index_key,
                limit,
                ticker=ticker or None,
                include_upcoming=include_upcoming,
            )

        items = [_build_index_addition_performance(conn, rec) for rec in records]

    payload = {
        "count": len(items),
        "index_key": index_key,
        "ticker": ticker or None,
        "candidate_indices": candidates,
        "items": items,
        "source": "daily_prices",
        "lookup_mode": "ad_hoc" if ticker and added_date else "saved_records",
        "message": (
            "No saved index-addition records found for this ticker yet."
            if ticker and not added_date and not items
            else None
        ),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return jsonify(payload)


@app.route("/api/index-definitions")
def get_index_definitions():
    return jsonify({
        "indices": _index_definitions(),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/index-additions/candidates")
def get_index_addition_candidates():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    return jsonify({
        "ticker": ticker,
        "indices": _index_candidates_for_ticker(ticker),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/index-changes")
def get_index_changes():
    if not os.path.exists(DB_PATH):
        return jsonify({"error": "database not found; run db/init_db.py first"}), 500

    index_key = request.args.get("index_key", "sp500").strip().lower() or "sp500"
    index_def = _index_definition(index_key)
    if not index_def:
        return jsonify({"error": f"unknown index_key: {index_key}"}), 400
    include_upcoming = request.args.get("include_upcoming", "").strip().lower() in {"1", "true", "yes"}
    requested_years = [
        y.strip()
        for y in request.args.get("years", "").split(",")
        if y.strip().isdigit()
    ]

    with get_db_conn() as conn:
        year_rows = conn.execute(
            """
            SELECT DISTINCT substr(effective_date, 1, 4) AS year
            FROM index_constituent_changes
            WHERE index_key = ?
              AND (? = 1 OR effective_date <= ?)
            ORDER BY year DESC
            """,
            (index_key, 1 if include_upcoming else 0, date.today().isoformat()),
        ).fetchall()
        available_years = [row["year"] for row in year_rows if row["year"]]
        selected_years = requested_years or (available_years[:1] if available_years else [])

        where = "index_key = ?"
        params: list = [index_key]
        if not include_upcoming:
            where += " AND effective_date <= ?"
            params.append(date.today().isoformat())
        if selected_years:
            placeholders = ",".join("?" for _ in selected_years)
            where += f" AND substr(effective_date, 1, 4) IN ({placeholders})"
            params.extend(selected_years)

        rows = conn.execute(
            f"""
            SELECT ticker, action, effective_date, quarter, source_note, source_url
            FROM index_constituent_changes
            WHERE {where}
            ORDER BY effective_date DESC, action ASC, ticker ASC
            """,
            params,
        ).fetchall()

    groups_by_quarter: dict[str, dict] = {}
    for row in rows:
        group = groups_by_quarter.setdefault(
            row["quarter"],
            {"quarter": row["quarter"], "additions": [], "removals": []},
        )
        item = {
            "ticker": row["ticker"],
            "effective_date": row["effective_date"],
            "source_note": row["source_note"],
            "source_url": row["source_url"],
        }
        if row["action"] == "add":
            group["additions"].append(item)
        else:
            group["removals"].append(item)

    return jsonify({
        "index_key": index_key,
        "index_name": index_def["name"],
        "benchmark_ticker": index_def.get("benchmark_ticker"),
        "available_years": available_years,
        "selected_years": selected_years,
        "groups": [groups_by_quarter[key] for key in sorted(groups_by_quarter.keys(), reverse=True)],
        "count": len(rows),
        "source": "index_constituent_changes",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# ─── Watchlist — JSON-backed with sectors ─────────────────────────────────

import json as _json

_WL_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "watchlist.json")
_WL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "stocks_to_track")  # kept in sync as flat backup


def _read_wl_json() -> dict:
    """Load watchlist.json; returns the full dict with 'sectors' list."""
    try:
        with open(_WL_JSON, "r") as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return {"sectors": []}


def _write_wl_json(data: dict):
    """Save watchlist.json and regenerate the flat stocks_to_track file."""
    with open(_WL_JSON, "w") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
    # Regenerate flat file so INDICES["watchlist"]["file"] still works
    all_tickers = _flatten_wl(data)
    with open(_WL_FILE, "w") as f:
        f.write("Stock List:\n\n")
        for t in all_tickers:
            f.write(t + "\n")


def _flatten_wl(data: dict) -> list[str]:
    """Return a deduplicated ordered flat list of all tickers across all sectors."""
    seen, out = set(), []
    for sector in data.get("sectors", []):
        for t in sector.get("tickers", []):
            t = t.upper()
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _flatten_wl_with_sectors(data: dict) -> list[dict]:
    seen, out = set(), []
    for sector in data.get("sectors", []):
        for ticker in sector.get("tickers", []):
            ticker = str(ticker).strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            out.append({
                "ticker": ticker,
                "sector_key": sector.get("key"),
                "sector_name": sector.get("name"),
            })
    return out


def _ensure_symbol(conn: sqlite3.Connection, ticker: str) -> int:
    conn.execute(
        """
        INSERT INTO symbols (ticker, asset_type, active, updated_at)
        VALUES (?, 'equity', 1, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            active = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (ticker,),
    )
    return int(conn.execute("SELECT id FROM symbols WHERE ticker = ?", (ticker,)).fetchone()["id"])


def _ensure_watchlist(conn: sqlite3.Connection, key: str = "default", name: str = "My Watchlist") -> int:
    conn.execute(
        """
        INSERT INTO watchlists (key, name)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET name = excluded.name
        """,
        (key, name),
    )
    return int(conn.execute("SELECT id FROM watchlists WHERE key = ?", (key,)).fetchone()["id"])


def _record_watchlist_event(
    conn: sqlite3.Connection,
    watchlist_id: int,
    symbol_id: int,
    ticker: str,
    action: str,
    sector_key: str | None,
    sector_name: str | None,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO watchlist_symbol_events
            (watchlist_id, symbol_id, ticker, action, sector_key, sector_name, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (watchlist_id, symbol_id, ticker, action, sector_key, sector_name, source),
    )


def _sync_watchlist_db_from_json(data: dict, source: str = "watchlist_json") -> None:
    if not os.path.exists(DB_PATH):
        return

    desired = _flatten_wl_with_sectors(data)
    desired_by_ticker = {row["ticker"]: row for row in desired}

    with get_db_conn() as conn:
        watchlist_id = _ensure_watchlist(conn)
        existing = conn.execute(
            """
            SELECT s.ticker, s.id AS symbol_id, ws.active
            FROM watchlist_symbols ws
            JOIN symbols s ON s.id = ws.symbol_id
            WHERE ws.watchlist_id = ?
            """,
            (watchlist_id,),
        ).fetchall()
        existing_by_ticker = {row["ticker"]: row for row in existing}

        for ticker, row in desired_by_ticker.items():
            symbol_id = _ensure_symbol(conn, ticker)
            prior = existing_by_ticker.get(ticker)
            action = "add" if not prior or not prior["active"] else None
            conn.execute(
                """
                INSERT INTO watchlist_symbols (
                    watchlist_id,
                    symbol_id,
                    sector_key,
                    sector_name,
                    active,
                    removed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(watchlist_id, symbol_id) DO UPDATE SET
                    sector_key = excluded.sector_key,
                    sector_name = excluded.sector_name,
                    active = 1,
                    removed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (watchlist_id, symbol_id, row["sector_key"], row["sector_name"]),
            )
            if action:
                _record_watchlist_event(
                    conn,
                    watchlist_id,
                    symbol_id,
                    ticker,
                    action,
                    row["sector_key"],
                    row["sector_name"],
                    source,
                )

        for ticker, row in existing_by_ticker.items():
            if ticker in desired_by_ticker or not row["active"]:
                continue
            conn.execute(
                """
                UPDATE watchlist_symbols
                SET active = 0,
                    removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE watchlist_id = ? AND symbol_id = ?
                """,
                (watchlist_id, row["symbol_id"]),
            )
            _record_watchlist_event(
                conn,
                watchlist_id,
                row["symbol_id"],
                ticker,
                "remove",
                None,
                None,
                source,
            )
        conn.commit()


def _read_watchlist() -> list[str]:
    """Flat ordered list of all watchlist tickers (from JSON source of truth)."""
    data = _read_wl_json()
    _sync_watchlist_db_from_json(data, source="api_read")
    return _flatten_wl(data)


def _write_watchlist(tickers: list[str]):
    """Add any unknown tickers to the 'Other' sector and save."""
    data = _read_wl_json()
    existing = set(_flatten_wl(data))
    new_tickers = [t for t in tickers if t not in existing]
    if new_tickers:
        # Find or create 'Other' sector
        other = next((s for s in data["sectors"] if s["key"] == "other"), None)
        if other is None:
            other = {"key": "other", "name": "Other", "icon": "📌", "tickers": []}
            data["sectors"].append(other)
        for t in new_tickers:
            other["tickers"].append(t)
    # Remove deleted tickers
    ticker_set = set(tickers)
    for sector in data["sectors"]:
        sector["tickers"] = [t for t in sector["tickers"] if t in ticker_set]
    _write_wl_json(data)


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    return jsonify({"tickers": _read_watchlist()})


@app.route("/api/watchlist/sectors", methods=["GET"])
def get_watchlist_sectors():
    """Return full sector-categorized watchlist."""
    data = _read_wl_json()
    _sync_watchlist_db_from_json(data, source="api_read")
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# SECTOR STATS  GET /api/sectors/stats?sector=<key>
#   Returns price, RSI, 52W high/low, YTD high/low, % from 52W high
#   for every ticker in the requested sector (or ALL sectors if sector=all).
#   Cached 15 min per sector key.
# ─────────────────────────────────────────────────────────────────────────────
_SECTOR_STATS_TTL = 900  # 15 minutes


def _fetch_sector_stock(ticker: str) -> dict | None:
    """Fetch extended stats for a single ticker: price, RSI, 52W & YTD ranges."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y")
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        close = hist["Close"].dropna()
        if len(close) < 15:
            return None

        rsi = compute_rsi(close)
        price = round(float(close.iloc[-1]), 2)
        prev  = round(float(close.iloc[-2]), 2) if len(close) >= 2 else price
        chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0

        w52_high = round(float(close.max()), 2)
        w52_low  = round(float(close.min()), 2)
        pct_from_high = round((price - w52_high) / w52_high * 100, 2) if w52_high else None

        # YTD: filter to current year
        import datetime as _dt
        ytd_start = _dt.date.today().replace(month=1, day=1)
        ytd_close = close[close.index.date >= ytd_start] if hasattr(close.index, 'date') else close
        ytd_high  = round(float(ytd_close.max()), 2) if not ytd_close.empty else w52_high
        ytd_low   = round(float(ytd_close.min()), 2) if not ytd_close.empty else w52_low

        # Company short name — use fast_info (no extra HTTP call)
        name = ticker
        try:
            fi2 = t.fast_info
            # fast_info may expose 'company_name' in some yfinance versions
            name = getattr(fi2, "company_name", None) or ticker
        except Exception:
            pass

        return {
            "ticker":          ticker,
            "name":            name,
            "price":           price,
            "change_pct":      chg_pct,
            "rsi":             rsi,
            "w52_high":        w52_high,
            "w52_low":         w52_low,
            "pct_from_high":   pct_from_high,
            "ytd_high":        ytd_high,
            "ytd_low":         ytd_low,
        }
    except Exception as e:
        print(f"⚠️  sector-stats {ticker}: {e}")
        return None


def _fetch_sector_stats(sector_key: str) -> list[dict]:
    """Return stats for every ticker in a sector (or all sectors). Cached."""
    cache_key = f"sector_stats_{sector_key}"
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < _SECTOR_STATS_TTL:
            return data

    wl = _read_wl_json()
    if sector_key == "all":
        tickers = _flatten_wl(wl)
        # Attach sector label to each ticker
        ticker_to_sector: dict[str, dict] = {}
        for s in wl.get("sectors", []):
            for tk in s.get("tickers", []):
                ticker_to_sector[tk] = {"sector_key": s["key"], "sector_name": s["name"], "sector_icon": s["icon"]}
    else:
        sector = next((s for s in wl.get("sectors", []) if s["key"] == sector_key), None)
        if not sector:
            return []
        tickers = sector["tickers"]
        ticker_to_sector = {tk: {"sector_key": sector["key"], "sector_name": sector["name"], "sector_icon": sector["icon"]} for tk in tickers}

    results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_fetch_sector_stock, tk): tk for tk in tickers}
        for fut in as_completed(futures):
            row = fut.result()
            if row:
                row.update(ticker_to_sector.get(row["ticker"], {}))
                results.append(row)

    results.sort(key=lambda r: r.get("rsi") or 50)
    _cache[cache_key] = (time.time(), results)
    return results


@app.route("/api/sectors/stats")
def get_sector_stats():
    sector_key = request.args.get("sector", "all").strip().lower()
    rows = _fetch_sector_stats(sector_key)
    return jsonify({"sector": sector_key, "stocks": rows, "count": len(rows)})


@app.route("/api/watchlist", methods=["POST"])
def add_to_watchlist():
    ticker = (request.json or {}).get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    data = _read_wl_json()
    existing = _flatten_wl(data)
    if ticker not in existing:
        # Place in 'Other' sector (user can re-categorize later)
        other = next((s for s in data["sectors"] if s["key"] == "other"), None)
        if other is None:
            other = {"key": "other", "name": "Other", "icon": "📌", "tickers": []}
            data["sectors"].append(other)
        other["tickers"].append(ticker)
        _write_wl_json(data)
        _sync_watchlist_db_from_json(data, source="dashboard_add")
        existing = _flatten_wl(data)
    return jsonify({"tickers": existing})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def remove_from_watchlist(ticker: str):
    ticker = ticker.strip().upper()
    data = _read_wl_json()
    for sector in data["sectors"]:
        sector["tickers"] = [t for t in sector["tickers"] if t != ticker]
    _write_wl_json(data)
    _sync_watchlist_db_from_json(data, source="dashboard_remove")
    return jsonify({"tickers": _flatten_wl(data)})


@app.route("/api/quote")
def get_quote():
    """Return a quick quote summary for a ticker (for the watchlist cards)."""
    from flask import request as flask_request
    ticker = flask_request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    cache_key = f"quote:{ticker}"
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < 300:   # 5-min cache
            return jsonify(data)
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        fi   = t.fast_info
        payload = {
            "ticker":      ticker,
            "name":        info.get("longName") or info.get("shortName") or ticker,
            "market_cap":  info.get("marketCap"),
            "volume":      info.get("volume") or info.get("regularMarketVolume"),
            "week52_high": round(float(fi.year_high), 2) if getattr(fi, "year_high", None) else info.get("fiftyTwoWeekHigh"),
            "week52_low":  round(float(fi.year_low),  2) if getattr(fi, "year_low",  None) else info.get("fiftyTwoWeekLow"),
            "sector":      info.get("sector"),
            "industry":    info.get("industry"),
        }
        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/options")
def get_options():
    """Return top 5 options (calls + puts) sorted by a chosen metric.

    Query params:
        - ticker: symbol (required)
        - term: short, medium, long (optional, default long)
            short  → all expirations (no min/max filter)
            medium → 90–180 days out
            long   → 180–730 days out
        - sort_by: volume, open_interest, expiry, iv, spread (optional, default volume)
    """
    from flask import request

    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    term = request.args.get("term", "long").lower()
    if term not in ("short", "medium", "long"):
        term = "long"

    sort_by = request.args.get("sort_by", "volume").lower()
    if sort_by not in ("volume", "open_interest", "expiry", "iv", "spread"):
        sort_by = "volume"

    # Expiry window by term
    term_windows = {
        "short":  (0, None),      # all expirations, no filter
        "medium": (90, 180),      # 3–6 months out
        "long":   (180, 730),     # 6 months – 2 years out
    }
    min_days, max_days = term_windows[term]

    cache_key = f"options:{ticker}:{term}:{sort_by}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return jsonify(cached_data)

    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return jsonify({"error": "no options data available"}), 404

        today = datetime.utcnow().date()
        min_expiry = today + timedelta(days=min_days)
        max_expiry = today + timedelta(days=max_days) if max_days is not None else None

        valid_expirations = []
        for exp in expirations:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            except ValueError:
                continue
            if exp_date < min_expiry:
                continue
            if max_expiry is not None and exp_date > max_expiry:
                continue
            valid_expirations.append(exp)

        term_labels = {"short": "Short Term (all)", "medium": "Medium Term (3–6 mo)", "long": "Long Term (6 mo – 2 yr)"}
        if not valid_expirations:
            window_desc = term_labels[term]
            return jsonify({"error": f"no expirations found for {window_desc}"}), 404

        all_options = []
        for exp in valid_expirations:
            try:
                chain = t.option_chain(exp)
                calls = chain.calls
                puts = chain.puts
                if calls is not None and not calls.empty:
                    calls = calls.copy()
                    calls["expiry"] = exp
                    calls["option_type"] = "Call"
                    all_options.append(calls)
                if puts is not None and not puts.empty:
                    puts = puts.copy()
                    puts["expiry"] = exp
                    puts["option_type"] = "Put"
                    all_options.append(puts)
            except Exception:
                continue

        if not all_options:
            return jsonify({"error": "no options found"}), 404

        # Get current underlying price for Greeks calculation
        spot_price = 0.0
        try:
            hist = t.history(period="5d")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                spot_price = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass

        options_df = pd.concat(all_options, ignore_index=True)
        options_df = options_df.fillna(0)

        # Compute spread column for sorting
        options_df["spread"] = (options_df.get("ask", 0) - options_df.get("bid", 0)).clip(lower=0)

        # Sort based on selected metric
        sort_labels = {
            "volume": "Volume (Highest)",
            "open_interest": "Open Interest (Highest)",
            "expiry": "Expiry (Nearest First)",
            "iv": "Implied Volatility (Highest)",
            "spread": "Bid-Ask Spread (Tightest)",
        }

        if sort_by == "volume":
            top_options = options_df.sort_values(by="volume", ascending=False).head(5)
        elif sort_by == "open_interest":
            top_options = options_df.sort_values(by="openInterest", ascending=False).head(5)
        elif sort_by == "expiry":
            top_options = options_df.sort_values(by=["expiry", "volume"], ascending=[True, False]).head(5)
        elif sort_by == "iv":
            iv_col = "impliedVolatility" if "impliedVolatility" in options_df.columns else None
            if iv_col:
                top_options = options_df.sort_values(by=iv_col, ascending=False).head(5)
            else:
                top_options = options_df.sort_values(by="volume", ascending=False).head(5)
        elif sort_by == "spread":
            # Tightest spread = smallest spread, but filter out zero-bid rows (illiquid)
            liquid = options_df[options_df["bid"] > 0]
            if liquid.empty:
                liquid = options_df
            top_options = liquid.sort_values(by="spread", ascending=True).head(5)
        else:
            top_options = options_df.sort_values(by="volume", ascending=False).head(5)

        data = []
        for _, row in top_options.iterrows():
            iv_val = float(row.get("impliedVolatility", 0)) if "impliedVolatility" in row.index else 0.0
            opt_type = row.get("option_type") or ""
            expiry_str = row.get("expiry") or ""
            strike = float(row.get("strike", 0))

            # Compute Greeks (delta, theta) via Black-Scholes
            greeks = {"delta": 0.0, "theta": 0.0}
            if spot_price > 0 and iv_val > 0 and expiry_str:
                try:
                    exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    T = (exp_date - today).days / 365.0
                    if T > 0:
                        greeks = compute_greeks(spot_price, strike, T, iv_val, opt_type)
                except Exception:
                    pass

            data.append({
                "contract": row.get("contractSymbol"),
                "option_type": opt_type,
                "expiry": expiry_str,
                "strike": strike,
                "last": float(row.get("lastPrice", 0)),
                "bid": float(row.get("bid", 0)),
                "ask": float(row.get("ask", 0)),
                "volume": int(row.get("volume", 0)),
                "open_interest": int(row.get("openInterest", 0)),
                "iv": round(iv_val * 100, 2),
                "spread": round(float(row.get("ask", 0)) - float(row.get("bid", 0)), 2),
                "delta": greeks["delta"],
                "theta": greeks["theta"],
            })

        term_labels = {"short": "Short Term (all)", "medium": "Medium Term (3–6 mo)", "long": "Long Term (6 mo – 2 yr)"}
        window_desc = term_labels[term]
        max_desc = f"{max_days}d" if max_days else "∞"

        payload = {
            "ticker": ticker,
            "expiry": ", ".join(valid_expirations[:3]),
            "term": term,
            "term_label": window_desc,
            "sort_by": sort_by,
            "sort_label": sort_labels.get(sort_by, "Volume"),
            "top_options": data,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": f"Top 5 by {sort_labels.get(sort_by, sort_by)} · {window_desc} · Expirations {min_days}–{max_desc} out.",
        }

        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ─── Earnings API ─────────────────────────────────────────
# (flask.request already imported in /api/history and /api/options above;
#  keeping the import here too is harmless in Flask but we alias to avoid confusion)

def get_relevant_earnings_week():
    """Return (start_date, end_date) for the relevant earnings week.

    Mon–Thu  → current calendar week (Mon–Sun)
    Fri–Sun  → next calendar week    (Mon–Sun)
    """
    today = datetime.utcnow().date()
    weekday = today.weekday()  # Monday=0 … Sunday=6
    if weekday >= 4:  # Fri / Sat / Sun → next week
        start = today + timedelta(days=(7 - weekday))
    else:             # Mon–Thu → this week
        start = today - timedelta(days=weekday)
    end = start + timedelta(days=6)
    return start, end


def _safe_float(val):
    """Return a rounded float or None – never NaN/Inf."""
    try:
        f = float(val)
        return round(f, 4) if (f == f and abs(f) != float("inf")) else None
    except (TypeError, ValueError):
        return None


def fetch_earnings_for_ticker(ticker: str, week_start, week_end) -> dict | None:
    """Return earnings dict for *ticker* if its next earnings fall in [week_start, week_end]."""
    try:
        t = yf.Ticker(ticker)

        # ── 1. Determine upcoming earnings date ──────────────────────────────
        edate = None
        try:
            cal = t.calendar
            if cal is not None:
                # yfinance ≥ 0.2 returns a plain dict; older returns a DataFrame
                if isinstance(cal, dict):
                    raw = cal.get("Earnings Date") or cal.get("earningsDate")
                    if raw:
                        if isinstance(raw, (list, tuple)) and len(raw):
                            raw = raw[0]
                        edate = pd.to_datetime(raw, errors="coerce")
                        if pd.isnull(edate):
                            edate = None
                        else:
                            edate = edate.date()
                elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                    raw = cal.loc["Earnings Date"].values[0]
                    if not pd.isnull(raw):
                        edate = pd.to_datetime(raw, errors="coerce")
                        edate = None if pd.isnull(edate) else edate.date()
        except Exception:
            pass

        if edate is None or not (week_start <= edate <= week_end):
            return None

        # ── 2. Price history + RSI ─────────────────────────────────────────
        hist = t.history(period="1y")
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        close = hist["Close"].dropna()
        if len(close) < 15:
            return None
        rsi = compute_rsi(close)
        last_price = _safe_float(close.iloc[-1])

        # ── 3. Past earnings results (most-recent 3) ──────────────────────
        earnings_list: list[dict] = []
        try:
            # yfinance ≥ 0.2 exposes quarterly earnings via .quarterly_earnings
            for src in (getattr(t, "quarterly_earnings", None), getattr(t, "earnings", None)):
                if src is not None and not src.empty:
                    for idx_val, row in src.tail(3).iterrows():
                        earnings_list.append({
                            "date": str(idx_val),
                            "eps_actual":   _safe_float(row.get("Earnings") or row.get("epsActual")),
                            "eps_estimate": _safe_float(row.get("epsEstimate")),
                            "revenue":      _safe_float(row.get("Revenue")  or row.get("revenueActual")),
                            "surprise_pct": _safe_float(row.get("surprisePercent")),
                        })
                    break  # prefer quarterly_earnings; fall back to earnings
        except Exception:
            pass

        # ── 4. Guidance ───────────────────────────────────────────────────
        guidance_available = False
        guidance_detail: str | None = None
        for attr in ("guidance", "analyst_price_target", "recommendations_summary"):
            try:
                g = getattr(t, attr, None)
                if g is not None and not (hasattr(g, "empty") and g.empty):
                    guidance_available = True
                    if isinstance(g, pd.DataFrame) and not g.empty:
                        guidance_detail = g.iloc[0].to_dict().__str__()[:120]
                    break
            except Exception:
                pass

        # ── 5. Post-earnings price move (after most-recent quarterly earnings) ─
        post_move: dict | None = None
        try:
            # Try to get the last earnings *date* from quarterly financials
            earn_date_src = None
            for src in (
                getattr(t, "quarterly_earnings", None),
                getattr(t, "quarterly_financials", None),
                getattr(t, "earnings", None),
            ):
                if src is not None and not src.empty:
                    # index may be a DatetimeIndex or year ints
                    idx0 = src.index[-1]  # most recent (last row for earnings, last col for financials)
                    # quarterly_financials: columns are dates, index are metrics
                    if hasattr(src, "columns") and isinstance(src.columns, pd.DatetimeIndex):
                        idx0 = src.columns[0]  # most recent quarter
                    earn_date_src = pd.to_datetime(idx0, errors="coerce")
                    if not pd.isnull(earn_date_src):
                        break

            if earn_date_src is not None and not pd.isnull(earn_date_src):
                # Normalize timezone so comparison works
                hist_idx = hist.index
                if hasattr(hist_idx, "tz") and hist_idx.tz is not None:
                    earn_date_src = earn_date_src.tz_localize(hist_idx.tz) if earn_date_src.tzinfo is None else earn_date_src.tz_convert(hist_idx.tz)
                before_hist = hist[hist.index <= earn_date_src]
                after_hist  = hist[hist.index  > earn_date_src]
                if not before_hist.empty and not after_hist.empty:
                    price_before = float(before_hist["Close"].iloc[-1])
                    price_after  = float(after_hist["Close"].iloc[0])
                    move = price_after - price_before
                    pct  = (move / price_before * 100) if price_before else 0.0
                    post_move = {
                        "earn_date":  str(earn_date_src.date()),
                        "next_date":  str(after_hist.index[0].date()),
                        "move":       round(move, 2),
                        "pct":        round(pct, 2),
                        "direction":  "up" if move > 0 else ("down" if move < 0 else "flat"),
                    }
        except Exception:
            pass

        return {
            "ticker":            ticker,
            "earnings_date":     str(edate),
            "price":             last_price,
            "rsi":               rsi,
            "past_earnings":     earnings_list,
            "guidance":          guidance_available,
            "guidance_detail":   guidance_detail,
            "post_earnings_move": post_move,
        }
    except Exception as e:
        print(f"⚠️  {ticker} earnings: {e}")
        return None


EARNINGS_CACHE_TTL = 300  # 5 minutes

@app.route("/api/earnings")
def get_earnings_widget():
    """Return stocks with earnings in the relevant week.

    Query params:
        universe  – one of: watchlist, sp500, nasdaq100, dowjones, russell2000, spdr_etfs
                    (default: sp500)
    """
    from flask import request as flask_request

    universe = flask_request.args.get("universe", "sp500").lower()
    if universe not in INDICES:
        universe = "sp500"

    week_start, week_end = get_relevant_earnings_week()
    cache_key = f"earnings:{universe}:{week_start}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < EARNINGS_CACHE_TTL:
            print(f"📦 Earnings cache hit for {universe} / {week_start}")
            return jsonify(cached_data)

    tickers = load_tickers(INDICES[universe]["file"])
    print(f"⏳ Scanning {len(tickers)} tickers for earnings {week_start} – {week_end} (universe={universe})")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futs = {executor.submit(fetch_earnings_for_ticker, t, week_start, week_end): t for t in tickers}
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                results.append(res)
                print(f"  ✅ {res['ticker']} earns {res['earnings_date']}")

    results.sort(key=lambda x: x["earnings_date"])
    print(f"✅ Found {len(results)} stocks with earnings for universe={universe}")

    payload = {
        "universe":   universe,
        "week_start": str(week_start),
        "week_end":   str(week_end),
        "count":      len(results),
        "results":    results,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ─── Earnings Calendar (weekly, Mon-Fri, pre/post market) ─────────────────

def _fetch_cal_ticker(ticker: str, week_start, week_end):
    """Fetch upcoming earnings date + timing for a single ticker for the calendar view."""
    try:
        import pytz
        t = yf.Ticker(ticker)
        edate = None
        timing = "unknown"

        # Try get_earnings_dates first – gives us the hour encoding for pre/post timing
        try:
            et_tz = pytz.timezone("America/New_York")
            today_utc = pd.Timestamp.now(tz="UTC")
            ed = t.get_earnings_dates(limit=6)
            if ed is not None and not ed.empty:
                future = ed[ed.index >= today_utc]
                if not future.empty:
                    ts = future.index[-1]          # nearest upcoming (ascending after reverse)
                    ts_et = ts.tz_convert(et_tz)
                    edate = ts_et.date()
                    hour = ts_et.hour
                    timing = "pre" if hour < 12 else "post"
        except Exception:
            pass

        # Fallback: t.calendar
        if edate is None:
            try:
                cal = t.calendar
                if cal is not None:
                    if isinstance(cal, dict):
                        raw = cal.get("Earnings Date") or cal.get("earningsDate")
                        if raw:
                            if isinstance(raw, (list, tuple)) and len(raw):
                                raw = raw[0]
                            edate_ts = pd.to_datetime(raw, errors="coerce")
                            if not pd.isnull(edate_ts):
                                edate = edate_ts.date()
            except Exception:
                pass

        if edate is None or not (week_start <= edate <= week_end):
            return None

        # Company name
        name = ticker
        try:
            info = t.info
            n = info.get("shortName") or info.get("longName")
            if n:
                name = n
        except Exception:
            pass

        return {
            "ticker": ticker,
            "name":   name,
            "earnings_date": str(edate),
            "timing": timing,        # "pre" | "post" | "unknown"
        }
    except Exception:
        return None


@app.route("/api/earnings/calendar")
def get_earnings_calendar():
    """Return next (or current) week's earnings grouped by day & timing.

    Query params:
        week      – "next" (default) or "this"
        universe  – "sp500" (default), "nasdaq100", "dowjones", etc.
    """
    from flask import request as flask_request

    week_param = flask_request.args.get("week", "next").lower()
    universe   = flask_request.args.get("universe", "sp500").lower()
    if universe not in INDICES:
        universe = "sp500"

    today   = datetime.utcnow().date()
    weekday = today.weekday()   # Mon=0 … Sun=6

    if week_param == "next":
        # Always move to next Monday
        days_ahead = (7 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        mon = today + timedelta(days=days_ahead)
    else:  # "this"
        mon = today - timedelta(days=weekday)

    week_start = mon
    week_end   = mon + timedelta(days=4)   # Friday

    cache_key = f"earnings_cal:{universe}:{week_start}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < EARNINGS_CACHE_TTL:
            print(f"📦 Cal cache hit for {universe} / {week_start}")
            return jsonify(cached_data)

    tickers = load_tickers(INDICES[universe]["file"])
    print(f"⏳ Calendar scan: {len(tickers)} tickers for {week_start}–{week_end} ({universe})")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futs = {executor.submit(_fetch_cal_ticker, tk, week_start, week_end): tk for tk in tickers}
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                results.append(res)

    results.sort(key=lambda x: (x["earnings_date"], x["ticker"]))
    print(f"✅ Calendar: {len(results)} stocks found for {week_start}–{week_end}")

    # Group by calendar date → timing bucket
    import collections
    day_buckets: dict[str, dict] = {}
    for r in results:
        d = r["earnings_date"]
        if d not in day_buckets:
            day_buckets[d] = {"pre": [], "post": [], "unknown": []}
        day_buckets[d][r["timing"]].append({"ticker": r["ticker"], "name": r["name"]})

    payload = {
        "week_start":   str(week_start),
        "week_end":     str(week_end),
        "universe":     universe,
        "days":         day_buckets,
        "count":        len(results),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ─── Earnings Calendar Image ──────────────────────────────────────────────

# Logo cache: ticker → PIL Image (RGBA, already resized to LOGO_SIZE)
_LOGO_CACHE: dict[str, object] = {}
_LOGO_SIZE  = 72   # pixels

def _fetch_logo(ticker: str):
    """Return a PIL RGBA Image of the company logo, or None on failure.
    Tries FMP first (PNG), then falls back to None so the caller draws initials.
    Results are cached in _LOGO_CACHE for the lifetime of the process.
    """
    from PIL import Image
    import io as _io

    if ticker in _LOGO_CACHE:
        return _LOGO_CACHE[ticker]

    url = f"https://financialmodelingprep.com/image-stock/{ticker}.png"
    try:
        r = _http.get(url, timeout=6, headers={"User-Agent": "StocksDashboard/1.0"})
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            logo = Image.open(_io.BytesIO(r.content)).convert("RGBA")
            logo = logo.resize((_LOGO_SIZE, _LOGO_SIZE), Image.LANCZOS)
            _LOGO_CACHE[ticker] = logo
            return logo
    except Exception:
        pass

    _LOGO_CACHE[ticker] = None
    return None

def _make_calendar_image(data: dict) -> bytes:
    """Render the earnings calendar as a PNG image (Earnings Whispers style)."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    import io, hashlib

    # ── Palette (matches Earnings Whispers exactly) ───────────────────────
    BG          = (10,  11,  18)
    HEADER_BG   = (14,  16,  24)
    DAY_HDR_BG  = (18,  21,  34)
    CARD_BG     = (20,  24,  40)
    CARD_BDR    = (36,  42,  72)
    GRID_LINE   = (28,  34,  58)

    PRE_BAR_BG  = (24,  14,   0)
    PRE_ACCENT  = (249, 115,  22)
    PRE_TEXT    = (253, 186,  16)
    PRE_CARD_L  = (249, 115,  22)

    POST_BAR_BG = (4,   16,  42)
    POST_ACCENT = (59,  130, 246)
    POST_TEXT   = (147, 197, 253)
    POST_CARD_L = (59,  130, 246)

    WHITE       = (240, 242, 255)
    MUTED       = (100, 112, 148)
    DIM         = (46,   54,  90)

    # Fallback avatar colors when no logo available
    AVATAR_COLORS = [
        (239, 68,  68),  (249,115, 22),  (234,179,  8),  (34, 197, 94),
        (20, 184,166),   (59, 130,246),  (99, 102,241),  (168, 85,247),
        (236, 72,153),   (220, 38,127),  (6,  182,212),  (16, 185,129),
    ]
    def avatar_color(ticker):
        h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
        return AVATAR_COLORS[h % len(AVATAR_COLORS)]

    # ── Fonts ─────────────────────────────────────────────────────────────
    BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    REG  = "/System/Library/Fonts/Supplemental/Arial.ttf"
    def F(path, size):
        try:    return ImageFont.truetype(path, size)
        except: return ImageFont.load_default()

    f_brand    = F(BOLD, 22)
    f_weekof   = F(REG,  13)
    f_day_name = F(BOLD, 17)
    f_day_date = F(REG,  13)
    f_bar      = F(BOLD, 15)
    f_init     = F(BOLD, 20)
    f_ticker   = F(BOLD, 17)
    f_company  = F(REG,  11)
    f_empty    = F(REG,  13)
    f_footer   = F(REG,  10)

    # ── Data prep ─────────────────────────────────────────────────────────
    mon = datetime.strptime(data["week_start"], "%Y-%m-%d")
    days      = [mon + timedelta(days=i) for i in range(5)]
    day_isos  = [d.strftime("%Y-%m-%d") for d in days]
    day_names = [d.strftime("%A").upper() for d in days]
    day_dates = [d.strftime("%b %-d")    for d in days]

    pre_cols, post_cols = [], []
    for iso in day_isos:
        b = data["days"].get(iso, {})
        pre_cols.append( [(x["ticker"], x["name"]) for x in (b.get("pre")  or []) + (b.get("unknown") or [])])
        post_cols.append([(x["ticker"], x["name"]) for x in  b.get("post") or []])

    max_pre  = max((len(c) for c in pre_cols),  default=0)
    max_post = max((len(c) for c in post_cols), default=0)

    # ── Pre-fetch all logos in parallel ───────────────────────────────────
    all_tickers = list({t for col in pre_cols + post_cols for t, _ in col})
    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_fetch_logo, all_tickers))   # fills _LOGO_CACHE

    # ── Dimensions ────────────────────────────────────────────────────────
    NCOLS       = 5
    COL_W       = 240
    TOTAL_W     = NCOLS * COL_W
    HEADER_H    = 96
    DAY_HDR_H   = 60
    BAR_H       = 40
    AVATAR_D    = 72           # logo circle diameter (matches _LOGO_SIZE)
    LOGO_INSET  = 8            # white circle padding around logo
    CARD_H      = AVATAR_D + 50
    CARD_PAD_V  = 14
    CARD_GAP    = 10
    EMPTY_ROW_H = 80

    def section_h(n):
        return EMPTY_ROW_H if n == 0 else CARD_PAD_V + n*(CARD_H+CARD_GAP) - CARD_GAP + CARD_PAD_V

    PRE_ROW_H  = section_h(max_pre)
    POST_ROW_H = section_h(max_post)
    FOOTER_H   = 28
    TOTAL_H    = HEADER_H + DAY_HDR_H + BAR_H + PRE_ROW_H + BAR_H + POST_ROW_H + FOOTER_H

    # Work on RGBA canvas so we can paste transparent logos cleanly
    canvas = Image.new("RGBA", (TOTAL_W, TOTAL_H), (*BG, 255))
    draw   = ImageDraw.Draw(canvas)

    # ── Helper: centered text ─────────────────────────────────────────────
    def ctext(xc, y, text, font, fill):
        bb = draw.textbbox((0, 0), text, font=font)
        draw.text((xc - (bb[2]-bb[0])//2, y), text, fill=fill, font=font)

    # ── Helper: circular-clipped logo or colored initial ──────────────────
    def paste_logo(cx, cy, ticker):
        """Paste a real logo clipped to a circle; fall back to colored initial."""
        r   = AVATAR_D // 2
        x0  = cx - r
        y0  = cy - r

        logo_img = _LOGO_CACHE.get(ticker)

        if logo_img is not None:
            # White background disc (so transparent logos look clean on dark bg)
            disc_bg = Image.new("RGBA", (AVATAR_D, AVATAR_D), (0,0,0,0))
            d_bg    = ImageDraw.Draw(disc_bg)
            d_bg.ellipse([0, 0, AVATAR_D-1, AVATAR_D-1], fill=(255,255,255,255))

            # Scale logo to fit inside disc with inset margin
            inner = AVATAR_D - LOGO_INSET * 2
            logo_fit = logo_img.resize((inner, inner), Image.LANCZOS)

            # Circular mask for logo itself
            logo_mask = Image.new("L", (inner, inner), 0)
            ImageDraw.Draw(logo_mask).ellipse([0, 0, inner-1, inner-1], fill=255)

            disc_bg.paste(logo_fit, (LOGO_INSET, LOGO_INSET), mask=logo_mask)

            # Final circular mask for the whole disc
            full_mask = Image.new("L", (AVATAR_D, AVATAR_D), 0)
            ImageDraw.Draw(full_mask).ellipse([0, 0, AVATAR_D-1, AVATAR_D-1], fill=255)
            disc_bg.putalpha(full_mask)

            # Subtle shadow ring
            shadow = Image.new("RGBA", (AVATAR_D+4, AVATAR_D+4), (0,0,0,0))
            ImageDraw.Draw(shadow).ellipse([0,0,AVATAR_D+3,AVATAR_D+3], fill=(0,0,0,160))
            canvas.alpha_composite(shadow, dest=(x0-2, y0-2))
            canvas.alpha_composite(disc_bg, dest=(x0, y0))
        else:
            # Colored circle with ticker initials
            col = avatar_color(ticker)
            tmp = Image.new("RGBA", (AVATAR_D, AVATAR_D), (0,0,0,0))
            d   = ImageDraw.Draw(tmp)
            d.ellipse([0, 0, AVATAR_D-1, AVATAR_D-1], fill=(*col, 255))
            letters = ticker[:2]
            bb  = d.textbbox((0,0), letters, font=f_init)
            lw, lh = bb[2]-bb[0], bb[3]-bb[1]
            d.text((AVATAR_D//2 - lw//2, AVATAR_D//2 - lh//2 - bb[1]//2),
                   letters, fill=(255,255,255,255), font=f_init)
            mask = Image.new("L", (AVATAR_D, AVATAR_D), 0)
            ImageDraw.Draw(mask).ellipse([0,0,AVATAR_D-1,AVATAR_D-1], fill=255)
            tmp.putalpha(mask)
            canvas.alpha_composite(tmp, dest=(x0, y0))

    # ── Helper: full company card ─────────────────────────────────────────
    def draw_card(col_idx, card_top, ticker, company, is_pre):
        cx       = col_idx * COL_W + COL_W // 2
        border   = PRE_CARD_L  if is_pre else POST_CARD_L
        tick_col = PRE_TEXT    if is_pre else POST_TEXT
        pad      = 12
        cx0, cx1 = col_idx*COL_W + pad, (col_idx+1)*COL_W - pad
        cy0, cy1 = card_top, card_top + CARD_H

        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=10, fill=CARD_BG)
        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=10, outline=CARD_BDR, width=1)
        draw.rounded_rectangle([cx0, cy0, cx0+4, cy1], radius=2, fill=border)

        avatar_cy = cy0 + CARD_PAD_V//2 + AVATAR_D//2 + 4
        paste_logo(cx, avatar_cy, ticker)

        tick_y = avatar_cy + AVATAR_D//2 + 6
        ctext(cx, tick_y, ticker, f_ticker, tick_col)
        cname = company[:22] + "…" if len(company) > 22 else company
        ctext(cx, tick_y + 22, cname, f_company, MUTED)

    # ══════════════ DRAW ══════════════════════════════════════════════════

    # ── Header ────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, TOTAL_W, HEADER_H], fill=HEADER_BG)
    draw.rectangle([0, 0, TOTAL_W, 5], fill=PRE_ACCENT)
    draw.text((24, 20), "📅  EARNINGS CALENDAR", fill=WHITE, font=f_brand)
    fri = days[-1]
    sub = (f"Week of {mon.strftime('%b %-d')} – {fri.strftime('%b %-d, %Y')}"
           f"   ·   {data['count']} companies   ·   {data['universe'].upper()}")
    draw.text((26, 56), sub, fill=MUTED, font=f_weekof)
    draw.line([(0, HEADER_H), (TOTAL_W, HEADER_H)], fill=GRID_LINE, width=1)

    # ── Day column headers ────────────────────────────────────────────────
    y_dh = HEADER_H
    draw.rectangle([0, y_dh, TOTAL_W, y_dh+DAY_HDR_H], fill=DAY_HDR_BG)
    for ci, (dname, ddate) in enumerate(zip(day_names, day_dates)):
        cx = ci*COL_W + COL_W//2
        if ci > 0:
            draw.line([(ci*COL_W, y_dh), (ci*COL_W, TOTAL_H-FOOTER_H)], fill=GRID_LINE, width=1)
        ctext(cx, y_dh+10, dname, f_day_name, WHITE)
        ctext(cx, y_dh+34, ddate, f_day_date, MUTED)
    draw.line([(0, y_dh+DAY_HDR_H), (TOTAL_W, y_dh+DAY_HDR_H)], fill=GRID_LINE, width=1)

    # ── BEFORE THE OPEN bar ───────────────────────────────────────────────
    y_pre_bar = y_dh + DAY_HDR_H
    draw.rectangle([0, y_pre_bar, TOTAL_W, y_pre_bar+BAR_H], fill=PRE_BAR_BG)
    draw.rectangle([0, y_pre_bar, 4, y_pre_bar+BAR_H], fill=PRE_ACCENT)
    draw.text((TOTAL_W//2 - 90, y_pre_bar+12), "▲   BEFORE THE OPEN", fill=PRE_ACCENT, font=f_bar)
    draw.line([(0, y_pre_bar+BAR_H), (TOTAL_W, y_pre_bar+BAR_H)], fill=GRID_LINE, width=1)

    # ── Pre-market cards ──────────────────────────────────────────────────
    y_pre = y_pre_bar + BAR_H
    draw.rectangle([0, y_pre, TOTAL_W, y_pre+PRE_ROW_H], fill=BG)
    for ci, cards in enumerate(pre_cols):
        if not cards:
            ctext(ci*COL_W + COL_W//2, y_pre + PRE_ROW_H//2 - 8, "—", f_empty, DIM)
        else:
            for pi, (ticker, company) in enumerate(cards):
                draw_card(ci, y_pre + CARD_PAD_V + pi*(CARD_H+CARD_GAP), ticker, company, True)
    draw.line([(0, y_pre+PRE_ROW_H), (TOTAL_W, y_pre+PRE_ROW_H)], fill=GRID_LINE, width=1)

    # ── AFTER THE CLOSE bar ───────────────────────────────────────────────
    y_post_bar = y_pre + PRE_ROW_H
    draw.rectangle([0, y_post_bar, TOTAL_W, y_post_bar+BAR_H], fill=POST_BAR_BG)
    draw.rectangle([0, y_post_bar, 4, y_post_bar+BAR_H], fill=POST_ACCENT)
    draw.text((TOTAL_W//2 - 90, y_post_bar+12), "▼   AFTER THE CLOSE", fill=POST_ACCENT, font=f_bar)
    draw.line([(0, y_post_bar+BAR_H), (TOTAL_W, y_post_bar+BAR_H)], fill=GRID_LINE, width=1)

    # ── Post-market cards ─────────────────────────────────────────────────
    y_post = y_post_bar + BAR_H
    draw.rectangle([0, y_post, TOTAL_W, y_post+POST_ROW_H], fill=BG)
    for ci, cards in enumerate(post_cols):
        if not cards:
            ctext(ci*COL_W + COL_W//2, y_post + POST_ROW_H//2 - 8, "—", f_empty, DIM)
        else:
            for pi, (ticker, company) in enumerate(cards):
                draw_card(ci, y_post + CARD_PAD_V + pi*(CARD_H+CARD_GAP), ticker, company, False)
    draw.line([(0, y_post+POST_ROW_H), (TOTAL_W, y_post+POST_ROW_H)], fill=GRID_LINE, width=1)

    # ── Footer ────────────────────────────────────────────────────────────
    y_foot = y_post + POST_ROW_H
    draw.rectangle([0, y_foot, TOTAL_W, TOTAL_H], fill=HEADER_BG)
    draw.text((TOTAL_W//2 - 220, y_foot+9),
              f"Stocks RSI Dashboard   ·   Data via Yahoo Finance   ·   {data['last_updated']}",
              fill=MUTED, font=f_footer)
    draw.rectangle([0, TOTAL_H-4, TOTAL_W, TOTAL_H], fill=POST_ACCENT)

    # Convert to RGB PNG
    out = canvas.convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()



    HEADER_BG   = (14,  16,  24)
    DAY_HDR_BG  = (18,  21,  34)
    CARD_BG     = (20,  24,  40)
    CARD_BDR    = (36,  42,  72)
    GRID_LINE   = (28,  34,  58)

    PRE_BAR_BG  = (24,  14,   0)    # dark orange tint
    PRE_ACCENT  = (249, 115,  22)   # orange-500
    PRE_TEXT    = (253, 186,  16)   # amber
    PRE_CARD_L  = (249, 115,  22)   # card left border

    POST_BAR_BG = (4,   16,  42)    # dark blue tint
    POST_ACCENT = (59,  130, 246)   # blue-500
    POST_TEXT   = (147, 197, 253)   # blue-200
    POST_CARD_L = (59,  130, 246)

    WHITE       = (240, 242, 255)
    MUTED       = (100, 112, 148)
    DIM         = (46,   54,  90)
    EMPTY_COL   = (40,   48,  80)

    # Avatar palette – one per ticker (deterministic)
    AVATAR_COLORS = [
        (239, 68,  68),  (249,115, 22),  (234,179,  8),  (34, 197, 94),
        (20, 184,166),   (59, 130,246),  (99, 102,241),  (168, 85,247),
        (236, 72,153),   (220, 38,127),  (6,  182,212),  (16, 185,129),
    ]

    def avatar_color(ticker: str):
        h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
        return AVATAR_COLORS[h % len(AVATAR_COLORS)]

    # ── Fonts ─────────────────────────────────────────────────────────────
    BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    REG  = "/System/Library/Fonts/Supplemental/Arial.ttf"
    def F(path, size):
        try:    return ImageFont.truetype(path, size)
        except: return ImageFont.load_default()

    f_brand    = F(BOLD, 22)
    f_weekof   = F(REG,  13)
    f_day_name = F(BOLD, 17)
    f_day_date = F(REG,  13)
    f_bar      = F(BOLD, 15)
    f_avatar   = F(BOLD, 20)   # letter in circle
    f_ticker   = F(BOLD, 17)
    f_company  = F(REG,  11)
    f_empty    = F(REG,  13)
    f_footer   = F(REG,  10)

    # ── Data prep ─────────────────────────────────────────────────────────
    mon = datetime.strptime(data["week_start"], "%Y-%m-%d")
    days = [mon + timedelta(days=i) for i in range(5)]
    day_isos   = [d.strftime("%Y-%m-%d") for d in days]
    day_names  = [d.strftime("%A").upper() for d in days]  # MONDAY …
    day_dates  = [d.strftime("%b %-d")    for d in days]  # Jun 2

    pre_cols, post_cols = [], []
    for iso in day_isos:
        b = data["days"].get(iso, {})
        pre_cols.append( [(x["ticker"], x["name"]) for x in (b.get("pre")  or []) + (b.get("unknown") or [])] )
        post_cols.append([(x["ticker"], x["name"]) for x in  b.get("post") or []])

    max_pre  = max((len(c) for c in pre_cols),  default=0)
    max_post = max((len(c) for c in post_cols), default=0)

    # ── Dimensions ────────────────────────────────────────────────────────
    NCOLS       = 5
    COL_W       = 240          # each day column
    TOTAL_W     = NCOLS * COL_W
    HEADER_H    = 96
    DAY_HDR_H   = 60
    BAR_H       = 40           # "BEFORE THE OPEN" / "AFTER THE CLOSE" bar
    AVATAR_D    = 62           # logo circle diameter
    CARD_H      = AVATAR_D + 46   # circle + ticker + name
    CARD_PAD_V  = 14           # vertical padding inside cell
    CARD_GAP    = 10           # gap between cards in same cell
    EMPTY_ROW_H = 80

    def section_h(n_max):
        if n_max == 0: return EMPTY_ROW_H
        return CARD_PAD_V + n_max * CARD_H + (n_max - 1) * CARD_GAP + CARD_PAD_V

    PRE_ROW_H  = section_h(max_pre)
    POST_ROW_H = section_h(max_post)
    FOOTER_H   = 28
    TOTAL_H    = HEADER_H + DAY_HDR_H + BAR_H + PRE_ROW_H + BAR_H + POST_ROW_H + FOOTER_H

    img  = Image.new("RGB", (TOTAL_W, TOTAL_H), BG)
    draw = ImageDraw.Draw(img)

    # ── Helper: draw centered text ────────────────────────────────────────
    def ctext(x_center, y, text, font, fill):
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        draw.text((x_center - tw // 2, y), text, fill=fill, font=font)

    # ── Helper: draw avatar circle ────────────────────────────────────────
    def draw_avatar(cx, cy, ticker):
        col = avatar_color(ticker)
        r   = AVATAR_D // 2
        x0, y0 = cx - r, cy - r
        x1, y1 = cx + r, cy + r
        # Shadow ring
        draw.ellipse([x0-2, y0-2, x1+2, y1+2], fill=(0, 0, 0))
        # Colored fill
        draw.ellipse([x0, y0, x1, y1], fill=col)
        # Letter(s): up to 2 chars of ticker
        letters = ticker[:2]
        f_sz = f_avatar
        bb = draw.textbbox((0, 0), letters, font=f_sz)
        lw = bb[2] - bb[0]; lh = bb[3] - bb[1]
        draw.text((cx - lw//2, cy - lh//2 - bb[1]//2), letters, fill=WHITE, font=f_sz)

    # ── Helper: draw company card ─────────────────────────────────────────
    def draw_card(col_idx, card_top, ticker, company, is_pre):
        cx     = col_idx * COL_W + COL_W // 2
        border = PRE_CARD_L if is_pre else POST_CARD_L
        pad    = 12
        card_x0 = col_idx * COL_W + pad
        card_x1 = (col_idx + 1) * COL_W - pad
        card_y0 = card_top
        card_y1 = card_top + CARD_H
        # Card background
        draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1],
                                radius=10, fill=CARD_BG)
        # Card outline
        draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1],
                                radius=10, outline=CARD_BDR, width=1)
        # Left accent bar
        draw.rounded_rectangle([card_x0, card_y0, card_x0+4, card_y1],
                                radius=2, fill=border)
        # Avatar circle
        avatar_cy = card_top + CARD_PAD_V//2 + AVATAR_D//2 + 4
        draw_avatar(cx, avatar_cy, ticker)
        # Ticker
        tick_y = avatar_cy + AVATAR_D//2 + 6
        tick_col = PRE_TEXT if is_pre else POST_TEXT
        ctext(cx, tick_y, ticker, f_ticker, tick_col)
        # Company name (truncated)
        max_c = 22
        cname = company[:max_c] + "…" if len(company) > max_c else company
        ctext(cx, tick_y + 22, cname, f_company, MUTED)

    # ══════════════ DRAW ══════════════════════════════════════════════════

    # ── Header ────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, TOTAL_W, HEADER_H], fill=HEADER_BG)
    # Top accent stripe
    draw.rectangle([0, 0, TOTAL_W, 5], fill=PRE_ACCENT)
    # Brand
    draw.text((24, 20), "📅  EARNINGS CALENDAR", fill=WHITE, font=f_brand)
    fri = days[-1]
    sub = (f"Week of {mon.strftime('%b %-d')} – {fri.strftime('%b %-d, %Y')}"
           f"   ·   {data['count']} companies   ·   {data['universe'].upper()}")
    draw.text((26, 56), sub, fill=MUTED, font=f_weekof)
    # Bottom line under header
    draw.line([(0, HEADER_H), (TOTAL_W, HEADER_H)], fill=GRID_LINE, width=1)

    # ── Day column headers ────────────────────────────────────────────────
    y_dh = HEADER_H
    draw.rectangle([0, y_dh, TOTAL_W, y_dh + DAY_HDR_H], fill=DAY_HDR_BG)
    for ci, (dname, ddate) in enumerate(zip(day_names, day_dates)):
        cx = ci * COL_W + COL_W // 2
        # Vertical separator
        if ci > 0:
            draw.line([(ci * COL_W, y_dh), (ci * COL_W, TOTAL_H - FOOTER_H)],
                      fill=GRID_LINE, width=1)
        ctext(cx, y_dh + 10, dname,  f_day_name, WHITE)
        ctext(cx, y_dh + 34, ddate,  f_day_date, MUTED)
    draw.line([(0, y_dh + DAY_HDR_H), (TOTAL_W, y_dh + DAY_HDR_H)], fill=GRID_LINE, width=1)

    # ── BEFORE THE OPEN bar ───────────────────────────────────────────────
    y_pre_bar = y_dh + DAY_HDR_H
    draw.rectangle([0, y_pre_bar, TOTAL_W, y_pre_bar + BAR_H], fill=PRE_BAR_BG)
    draw.rectangle([0, y_pre_bar, 4, y_pre_bar + BAR_H], fill=PRE_ACCENT)
    label = "▲   BEFORE THE OPEN"
    draw.text((TOTAL_W // 2 - 90, y_pre_bar + 12), label, fill=PRE_ACCENT, font=f_bar)
    draw.line([(0, y_pre_bar + BAR_H), (TOTAL_W, y_pre_bar + BAR_H)], fill=GRID_LINE, width=1)

    # ── Pre-market cards ──────────────────────────────────────────────────
    y_pre = y_pre_bar + BAR_H
    draw.rectangle([0, y_pre, TOTAL_W, y_pre + PRE_ROW_H], fill=BG)
    for ci, cards in enumerate(pre_cols):
        if not cards:
            # "—" placeholder
            cx = ci * COL_W + COL_W // 2
            ctext(cx, y_pre + PRE_ROW_H // 2 - 8, "—", f_empty, DIM)
        else:
            for pi, (ticker, company) in enumerate(cards):
                card_top = y_pre + CARD_PAD_V + pi * (CARD_H + CARD_GAP)
                draw_card(ci, card_top, ticker, company, is_pre=True)

    draw.line([(0, y_pre + PRE_ROW_H), (TOTAL_W, y_pre + PRE_ROW_H)], fill=GRID_LINE, width=1)

    # ── AFTER THE CLOSE bar ───────────────────────────────────────────────
    y_post_bar = y_pre + PRE_ROW_H
    draw.rectangle([0, y_post_bar, TOTAL_W, y_post_bar + BAR_H], fill=POST_BAR_BG)
    draw.rectangle([0, y_post_bar, 4, y_post_bar + BAR_H], fill=POST_ACCENT)
    label2 = "▼   AFTER THE CLOSE"
    draw.text((TOTAL_W // 2 - 90, y_post_bar + 12), label2, fill=POST_ACCENT, font=f_bar)
    draw.line([(0, y_post_bar + BAR_H), (TOTAL_W, y_post_bar + BAR_H)], fill=GRID_LINE, width=1)

    # ── Post-market cards ─────────────────────────────────────────────────
    y_post = y_post_bar + BAR_H
    draw.rectangle([0, y_post, TOTAL_W, y_post + POST_ROW_H], fill=BG)
    for ci, cards in enumerate(post_cols):
        if not cards:
            cx = ci * COL_W + COL_W // 2
            ctext(cx, y_post + POST_ROW_H // 2 - 8, "—", f_empty, DIM)
        else:
            for pi, (ticker, company) in enumerate(cards):
                card_top = y_post + CARD_PAD_V + pi * (CARD_H + CARD_GAP)
                draw_card(ci, card_top, ticker, company, is_pre=False)

    draw.line([(0, y_post + POST_ROW_H), (TOTAL_W, y_post + POST_ROW_H)], fill=GRID_LINE, width=1)

    # ── Footer ────────────────────────────────────────────────────────────
    y_foot = y_post + POST_ROW_H
    draw.rectangle([0, y_foot, TOTAL_W, TOTAL_H], fill=HEADER_BG)
    foot = f"Stocks RSI Dashboard   ·   Data via Yahoo Finance   ·   {data['last_updated']}"
    draw.text((TOTAL_W // 2 - 220, y_foot + 9), foot, fill=MUTED, font=f_footer)
    # Bottom accent stripe
    draw.rectangle([0, TOTAL_H - 4, TOTAL_W, TOTAL_H], fill=POST_ACCENT)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()



    HEADER_BG   = (20,  25,  45)
    COL_HDR_BG  = (28,  35,  60)
    GRID_LINE   = (40,  50,  80)
    PRE_BG      = (50,  38,   8)       # amber-dark row bg
    POST_BG     = (28,  20,  55)       # purple-dark row bg
    PRE_PILL    = (180, 120,  20)
    POST_PILL   = (110,  70, 200)
    PRE_TEXT    = (255, 200,  80)
    POST_TEXT   = (200, 165, 255)
    WHITE       = (230, 235, 250)
    MUTED       = (120, 130, 160)
    ACCENT      = (100, 102, 241)
    EMPTY_TEXT  = (50,  60,  90)

    # ── Dimensions ───────────────────────────────────────────────────────
    COLS        = 5
    LEFT_W      = 110   # timing label column
    COL_W       = 210   # each day column
    HEADER_H    = 90    # title banner
    COL_HDR_H   = 54    # day name header row
    ROW_H_BASE  = 52    # base row height (grows with pills)
    PILL_H      = 42    # each pill height
    PILL_PAD    = 6     # gap between pills
    PADDING     = 14    # cell inner padding

    # Build Mon-Fri dates
    mon = datetime.strptime(data["week_start"], "%Y-%m-%d")
    days = [(mon + timedelta(days=i)) for i in range(5)]
    day_isos = [d.strftime("%Y-%m-%d") for d in days]
    day_labels = [d.strftime("%A") for d in days]     # Monday, Tuesday…
    day_dates  = [d.strftime("%b %-d") for d in days] # Jun 2

    # Pre/post pills per column
    pre_pills  = []   # list of list of (ticker, name)
    post_pills = []
    for iso in day_isos:
        bucket = data["days"].get(iso, {})
        pre  = [(x["ticker"], x["name"]) for x in (bucket.get("pre")  or []) + (bucket.get("unknown") or [])]
        post = [(x["ticker"], x["name"]) for x in  bucket.get("post") or []]
        pre_pills.append(pre)
        post_pills.append(post)

    max_pre  = max((len(p) for p in pre_pills),  default=0)
    max_post = max((len(p) for p in post_pills), default=0)

    def row_height(n):
        if n == 0:
            return ROW_H_BASE
        return PADDING + n * (PILL_H + PILL_PAD) + PADDING

    PRE_ROW_H  = row_height(max_pre)
    POST_ROW_H = row_height(max_post)

    TOTAL_W = LEFT_W + COLS * COL_W
    TOTAL_H = HEADER_H + COL_HDR_H + PRE_ROW_H + POST_ROW_H + 30  # +30 footer

    img  = Image.new("RGB", (TOTAL_W, TOTAL_H), BG)
    draw = ImageDraw.Draw(img)

    # ── Fonts ────────────────────────────────────────────────────────────
    def _font(path, size):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    FONT_PATH_BOLD   = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    FONT_PATH_REG    = "/System/Library/Fonts/Supplemental/Arial.ttf"

    f_title   = _font(FONT_PATH_BOLD, 26)
    f_sub     = _font(FONT_PATH_REG,  14)
    f_day     = _font(FONT_PATH_BOLD, 16)
    f_date    = _font(FONT_PATH_REG,  12)
    f_timing  = _font(FONT_PATH_BOLD, 13)
    f_ticker  = _font(FONT_PATH_BOLD, 16)
    f_company = _font(FONT_PATH_REG,  10)
    f_empty   = _font(FONT_PATH_REG,  13)
    f_footer  = _font(FONT_PATH_REG,  11)

    # ── Header banner ────────────────────────────────────────────────────
    draw.rectangle([0, 0, TOTAL_W, HEADER_H], fill=HEADER_BG)
    # Gradient-like accent bar on top
    draw.rectangle([0, 0, TOTAL_W, 4], fill=ACCENT)

    title = "📅  Earnings Calendar"
    draw.text((24, 16), title, fill=WHITE, font=f_title)
    fri = days[-1]
    sub = f"Week of {mon.strftime('%b %-d')} – {fri.strftime('%b %-d, %Y')}   •   {data['count']} companies   •   {data['universe'].upper()}"
    draw.text((26, 54), sub, fill=MUTED, font=f_sub)

    # ── Day column headers ────────────────────────────────────────────────
    y_col_hdr = HEADER_H
    draw.rectangle([0, y_col_hdr, TOTAL_W, y_col_hdr + COL_HDR_H], fill=COL_HDR_BG)
    # left gutter header
    draw.rectangle([0, y_col_hdr, LEFT_W, y_col_hdr + COL_HDR_H], fill=HEADER_BG)

    for ci, (dname, ddate) in enumerate(zip(day_labels, day_dates)):
        x0 = LEFT_W + ci * COL_W
        x1 = x0 + COL_W
        # vertical separator
        draw.line([(x0, y_col_hdr), (x0, TOTAL_H - 30)], fill=GRID_LINE, width=1)
        # day name
        cx = (x0 + x1) // 2
        bb = draw.textbbox((0, 0), dname, font=f_day)
        tw = bb[2] - bb[0]
        draw.text((cx - tw // 2, y_col_hdr + 8), dname, fill=WHITE, font=f_day)
        bb2 = draw.textbbox((0, 0), ddate, font=f_date)
        tw2 = bb2[2] - bb2[0]
        draw.text((cx - tw2 // 2, y_col_hdr + 30), ddate, fill=MUTED, font=f_date)

    # bottom border of col header
    draw.line([(0, y_col_hdr + COL_HDR_H), (TOTAL_W, y_col_hdr + COL_HDR_H)], fill=GRID_LINE, width=1)

    # ── Helper: draw pill ────────────────────────────────────────────────
    def draw_pill(x, y, ticker, company, is_pre):
        pill_bg  = PRE_PILL  if is_pre else POST_PILL
        tick_col = PRE_TEXT  if is_pre else POST_TEXT

        # Pill background (rounded via small rect)
        r = 6
        draw.rounded_rectangle([x, y, x + COL_W - PADDING * 2, y + PILL_H],
                                radius=r, fill=(*pill_bg, 60) if False else pill_bg)
        # Thin border
        border_col = PRE_TEXT if is_pre else POST_TEXT
        draw.rounded_rectangle([x, y, x + COL_W - PADDING * 2, y + PILL_H],
                                radius=r, outline=border_col, width=1)
        # Ticker
        draw.text((x + 8, y + 5), ticker, fill=tick_col, font=f_ticker)
        # Company name (truncate)
        max_chars = 26
        cname = company if len(company) <= max_chars else company[:max_chars - 1] + "…"
        draw.text((x + 8, y + 24), cname, fill=MUTED, font=f_company)

    # ── Before Open row ──────────────────────────────────────────────────
    y_pre = y_col_hdr + COL_HDR_H
    draw.rectangle([0, y_pre, TOTAL_W, y_pre + PRE_ROW_H], fill=PRE_BG)
    # row border bottom
    draw.line([(0, y_pre + PRE_ROW_H), (TOTAL_W, y_pre + PRE_ROW_H)], fill=GRID_LINE, width=1)
    # timing label
    timing_label_pre = "🌅  Before\n  Open"
    draw.text((8, y_pre + PRE_ROW_H // 2 - 18), "🌅", fill=PRE_TEXT, font=f_timing)
    draw.text((8, y_pre + PRE_ROW_H // 2 - 2),  "Before", fill=PRE_TEXT, font=f_timing)
    draw.text((8, y_pre + PRE_ROW_H // 2 + 16), "Open",   fill=PRE_TEXT, font=f_timing)

    for ci, pills in enumerate(pre_pills):
        x0 = LEFT_W + ci * COL_W + PADDING
        if not pills:
            bb = draw.textbbox((0, 0), "—", font=f_empty)
            tw = bb[2] - bb[0]
            draw.text((LEFT_W + ci * COL_W + COL_W // 2 - tw // 2,
                        y_pre + PRE_ROW_H // 2 - 8),
                       "—", fill=EMPTY_TEXT, font=f_empty)
        else:
            for pi, (ticker, company) in enumerate(pills):
                py = y_pre + PADDING + pi * (PILL_H + PILL_PAD)
                draw_pill(x0, py, ticker, company, is_pre=True)

    # ── After Close row ───────────────────────────────────────────────────
    y_post = y_pre + PRE_ROW_H
    draw.rectangle([0, y_post, TOTAL_W, y_post + POST_ROW_H], fill=POST_BG)
    draw.line([(0, y_post + POST_ROW_H), (TOTAL_W, y_post + POST_ROW_H)], fill=GRID_LINE, width=1)
    # timing label
    draw.text((8, y_post + POST_ROW_H // 2 - 18), "🌆", fill=POST_TEXT, font=f_timing)
    draw.text((8, y_post + POST_ROW_H // 2 - 2),  "After",  fill=POST_TEXT, font=f_timing)
    draw.text((8, y_post + POST_ROW_H // 2 + 16), "Close",  fill=POST_TEXT, font=f_timing)

    for ci, pills in enumerate(post_pills):
        x0 = LEFT_W + ci * COL_W + PADDING
        if not pills:
            bb = draw.textbbox((0, 0), "—", font=f_empty)
            tw = bb[2] - bb[0]
            draw.text((LEFT_W + ci * COL_W + COL_W // 2 - tw // 2,
                        y_post + POST_ROW_H // 2 - 8),
                       "—", fill=EMPTY_TEXT, font=f_empty)
        else:
            for pi, (ticker, company) in enumerate(pills):
                py = y_post + PADDING + pi * (PILL_H + PILL_PAD)
                draw_pill(x0, py, ticker, company, is_pre=False)

    # vertical separators over both rows
    for ci in range(1, COLS):
        x = LEFT_W + ci * COL_W
        draw.line([(x, y_pre), (x, y_post + POST_ROW_H)], fill=GRID_LINE, width=1)
    # left gutter separator
    draw.line([(LEFT_W, y_pre), (LEFT_W, y_post + POST_ROW_H)], fill=GRID_LINE, width=1)

    # ── Footer ────────────────────────────────────────────────────────────
    y_footer = y_post + POST_ROW_H + 4
    draw.rectangle([0, y_footer, TOTAL_W, TOTAL_H], fill=HEADER_BG)
    footer_txt = f"Stocks RSI Dashboard  •  Data via Yahoo Finance  •  Generated {data['last_updated']}"
    draw.text((24, y_footer + 8), footer_txt, fill=MUTED, font=f_footer)

    # Accent bar on bottom
    draw.rectangle([0, TOTAL_H - 4, TOTAL_W, TOTAL_H], fill=ACCENT)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


@app.route("/api/earnings/calendar/image")
def get_earnings_calendar_image():
    """Return the earnings calendar as a PNG image."""
    from flask import request as flask_request, send_file, Response
    import io

    week_param = flask_request.args.get("week",      "next").lower()
    universe   = flask_request.args.get("universe",  "sp500").lower()
    if universe not in INDICES:
        universe = "sp500"

    # Reuse the JSON calendar endpoint logic (check cache first)
    today   = datetime.utcnow().date()
    weekday = today.weekday()
    if week_param == "next":
        days_ahead = (7 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        mon = today + timedelta(days=days_ahead)
    else:
        mon = today - timedelta(days=weekday)

    week_start = mon
    week_end   = mon + timedelta(days=4)

    # Fetch data (reuse JSON cache if available)
    json_cache_key = f"earnings_cal:{universe}:{week_start}"
    data = None
    if json_cache_key in _cache:
        cached_time, cached_data = _cache[json_cache_key]
        if time.time() - cached_time < EARNINGS_CACHE_TTL:
            data = cached_data

    if data is None:
        tickers = load_tickers(INDICES[universe]["file"])
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=12) as executor:
            futs = {executor.submit(_fetch_cal_ticker, tk, week_start, week_end): tk for tk in tickers}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    results.append(res)
        results.sort(key=lambda x: (x["earnings_date"], x["ticker"]))
        import collections
        day_buckets: dict[str, dict] = {}
        for r in results:
            d = r["earnings_date"]
            if d not in day_buckets:
                day_buckets[d] = {"pre": [], "post": [], "unknown": []}
            day_buckets[d][r["timing"]].append({"ticker": r["ticker"], "name": r["name"]})
        data = {
            "week_start":   str(week_start),
            "week_end":     str(week_end),
            "universe":     universe,
            "days":         day_buckets,
            "count":        len(results),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _cache[json_cache_key] = (time.time(), data)

    try:
        png_bytes = _make_calendar_image(data)
        return Response(
            png_bytes,
            mimetype="image/png",
            headers={"Content-Disposition": f'inline; filename="earnings_calendar_{week_start}.png"'},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Earnings History (post-earnings price moves) ─────────

# ── 8-K Guidance helpers ─────────────────────────────────
# EDGAR company_tickers.json: maps ticker → {cik_str, title, ticker}
_TICKER_CIK_MAP: dict[str, str] = {}   # ticker → zero-padded 10-digit CIK
_TICKER_CIK_MAP_TS: float = 0.0        # timestamp of last load


def _ensure_ticker_cik_map():
    """Load (or refresh once per day) the SEC ticker→CIK map."""
    global _TICKER_CIK_MAP, _TICKER_CIK_MAP_TS
    if _TICKER_CIK_MAP and time.time() - _TICKER_CIK_MAP_TS < 86400:
        return
    try:
        r = _http.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=EDGAR_HEADERS, timeout=15,
        )
        r.raise_for_status()
        for entry in r.json().values():
            tkr = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if tkr:
                _TICKER_CIK_MAP[tkr] = cik
        _TICKER_CIK_MAP_TS = time.time()
        print(f"✅ Loaded {len(_TICKER_CIK_MAP)} ticker→CIK entries")
    except Exception as e:
        print(f"⚠️  ticker→CIK map load failed: {e}")


def _get_8k_filings_near_date(cik: str, earn_date) -> list[dict]:
    """Return 8-K filings filed within ±4 days of earn_date, sorted by closeness."""
    from datetime import date as _date
    try:
        padded = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        r = _http.get(url, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
        forms   = recent.get("form", [])
        dates   = recent.get("filingDate", [])
        accs    = recent.get("accessionNumber", [])
        earn_dt = earn_date if isinstance(earn_date, _date) else _date.fromisoformat(str(earn_date))
        results = []
        for form, filing_date, acc in zip(forms, dates, accs):
            if form not in ("8-K", "8-K/A"):
                continue
            try:
                fd    = _date.fromisoformat(filing_date)
                delta = abs((fd - earn_dt).days)
                if delta <= 4:
                    results.append({"date": filing_date, "accession": acc, "delta": delta})
            except Exception:
                continue
        results.sort(key=lambda x: x["delta"])
        return results
    except Exception as e:
        print(f"⚠️  8-K lookup for CIK {cik}: {e}")
        return []


def _get_8k_exhibit_text(cik: str, accession: str) -> str | None:
    """Fetch the press-release text from an 8-K filing (exhibit 99.1 preferred)."""
    try:
        cik_int   = str(int(cik))
        acc_clean = accession.replace("-", "")
        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_int}/{acc_clean}/{accession}-index.htm"
        )
        r = _http.get(index_url, headers=EDGAR_HEADERS, timeout=12)
        if not r.ok:
            return None
        # Find all .htm / .txt document links
        hrefs = re.findall(
            r'href="(/Archives/edgar/data/[^"]+\.(?:htm|txt))"',
            r.text, re.IGNORECASE,
        )
        # Prefer exhibit 99 (press release); fall back to first document
        ex99  = [h for h in hrefs if re.search(r'ex.?99', h, re.IGNORECASE)]
        links = ex99 or hrefs
        if not links:
            return None
        doc_r = _http.get(
            "https://www.sec.gov" + links[0],
            headers=EDGAR_HEADERS, timeout=15,
        )
        if not doc_r.ok:
            return None
        # Strip HTML tags → plain text
        text = re.sub(r'<[^>]+>', ' ', doc_r.text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&#\d+;', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text[:60000]   # cap at 60 K chars
    except Exception as e:
        print(f"⚠️  8-K exhibit fetch {accession}: {e}")
        return None


# Regex banks for guidance classification
_GUIDANCE_RAISE = re.compile(
    r"raise[sd]?\s+(?:its\s+)?(?:full[- ]year\s+|fiscal[- ]year\s+|annual\s+)?(?:revenue\s+|eps\s+|earnings\s+|sales\s+)?guidance"
    r"|increas(?:es?|ed|ing)\s+(?:its\s+)?(?:full[- ]year\s+|fiscal[- ]year\s+)?(?:revenue\s+|eps\s+|earnings\s+)?(?:guidance|outlook|forecast)"
    r"|above\s+(?:prior|previous|its\s+prior)\s+(?:guidance|outlook)"
    r"|upward(?:ly)?\s+revis(?:es?|ed|ing)"
    r"|guidance\s+(?:range\s+)?(?:to|of)\s+\$[0-9]"   # raises guidance to $X
    r"|increas(?:es?|ed|ing)\s+(?:its\s+)?(?:revenue|eps|earnings)\s+(?:guidance|outlook|forecast|expectation)",
    re.IGNORECASE,
)
_GUIDANCE_LOWER = re.compile(
    r"lower(?:s|ed|ing)?\s+(?:its\s+)?(?:full[- ]year\s+|fiscal[- ]year\s+)?(?:revenue\s+|eps\s+|earnings\s+)?(?:guidance|outlook|forecast)"
    r"|reduc(?:es?|ed|ing)\s+(?:its\s+)?(?:revenue\s+|eps\s+|earnings\s+)?(?:guidance|outlook|forecast)"
    r"|cut(?:s|ting)?\s+(?:its\s+)?(?:revenue\s+|eps\s+|earnings\s+)?(?:guidance|outlook|forecast)"
    r"|below\s+(?:prior|previous|its\s+prior)\s+(?:guidance|outlook)"
    r"|downward(?:ly)?\s+revis(?:es?|ed|ing)",
    re.IGNORECASE,
)
_GUIDANCE_REAFFIRM = re.compile(
    r"reaffirm|reiterat|maintain(?:s|ed|ing)\s+(?:its\s+)?(?:full[- ]year\s+|fiscal[- ]year\s+)?(?:revenue\s+|eps\s+|earnings\s+)?(?:guidance|outlook|forecast)"
    r"|consistent\s+with\s+(?:prior|previous)\s+guidance"
    r"|no\s+change\s+to\s+(?:its\s+)?(?:guidance|outlook)",
    re.IGNORECASE,
)
_GUIDANCE_CAUTIOUS = re.compile(
    r"caut(?:ious|ion)|uncertain(?:ty|ties)?|macro(?:economic)?\s+(?:headwind|pressure|challenge)"
    r"|headwind|challenging\s+(?:environment|macro|demand|conditions)"
    r"|soften(?:ing)?\s+demand|demand\s+(?:softness|weakness)"
    r"|conservative\s+(?:guidance|outlook)|below\s+(?:street|analyst|consensus)\s+(?:estimate|expectation|forecast)",
    re.IGNORECASE,
)
_OUTLOOK_SECTION = re.compile(
    r"(?:financial\s+|business\s+|full[- ]year\s+|fiscal[- ]year\s+)?(?:outlook|guidance|forecast)"
    r"[\s:—\-]{0,10}(.{50,800}?)(?:\n\n|\.\s+[A-Z]|$)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_guidance(text: str) -> dict | None:
    """Parse an 8-K press-release text for guidance signal + snippet."""
    if not text:
        return None

    signal  = None
    snippet = None

    # Try to find an Outlook/Guidance section first for the snippet
    m = _OUTLOOK_SECTION.search(text)
    if m:
        raw     = re.sub(r'\s+', ' ', m.group(0)).strip()
        snippet = raw[:500]

    if snippet:
        # We have an explicit outlook section — classify within it only
        if _GUIDANCE_RAISE.search(snippet):
            signal = "Raised"
        elif _GUIDANCE_LOWER.search(snippet):
            signal = "Lowered"
        elif _GUIDANCE_REAFFIRM.search(snippet):
            signal = "Reaffirmed"
        elif _GUIDANCE_CAUTIOUS.search(snippet):
            signal = "Cautious"
        else:
            signal = "Provided"   # has outlook section but no directional language
    else:
        # No labeled Outlook section — scan full text for directional guidance language
        # but skip if only risk-factor boilerplate (look for forward-looking $ amounts)
        has_forward_numbers = bool(re.search(
            r"(?:expect|anticipate|project|forecast)\s+(?:revenue|sales|eps|earnings|net\s+income)"
            r".*?\$[0-9]",
            text[:10000], re.IGNORECASE | re.DOTALL,
        ))
        if has_forward_numbers:
            full = text[:8000]
            if _GUIDANCE_RAISE.search(full):
                signal = "Raised"
            elif _GUIDANCE_LOWER.search(full):
                signal = "Lowered"
            elif _GUIDANCE_REAFFIRM.search(full):
                signal = "Reaffirmed"
            # Don't apply CAUTIOUS to full text — too many false positives from risk disclosures

    if not signal:
        return None

    return {
        "signal":  signal,
        "snippet": snippet,
        "source":  "SEC 8-K press release",
    }


def _fetch_8k_guidance(ticker: str, earn_date) -> dict | None:
    """Top-level: look up the 8-K for ticker around earn_date and parse guidance."""
    _ensure_ticker_cik_map()
    cik = _TICKER_CIK_MAP.get(ticker.upper())
    if not cik:
        return None
    filings = _get_8k_filings_near_date(cik, earn_date)
    for filing in filings[:2]:   # try at most 2 candidate 8-Ks
        text = _get_8k_exhibit_text(cik, filing["accession"])
        result = _extract_guidance(text)
        if result:
            result["filing_date"] = filing["date"]
            return result
    return None


@app.route("/api/earnings/history")
def get_earnings_history():
    """Return past 12 quarters of post-earnings price moves for a ticker.

    Query params:
        ticker  – stock symbol (required)
    """
    from flask import request as flask_request
    ticker = flask_request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    cache_key = f"earn_hist:{ticker}"
    if cache_key in _cache:
        cached_ts, cached_data = _cache[cache_key]
        if time.time() - cached_ts < 3600:  # 1-hour cache
            return jsonify(cached_data)

    try:
        t = yf.Ticker(ticker)

        # ── 1. Get past earnings dates (up to 16, keep past 12) ────────────
        try:
            ed = t.get_earnings_dates(limit=16)
        except Exception:
            ed = getattr(t, "earnings_dates", None)

        if ed is None or (hasattr(ed, "empty") and ed.empty):
            return jsonify({"error": f"No earnings dates found for {ticker}"}), 404

        today_ts = pd.Timestamp.now(tz="UTC")
        # Keep only past dates
        past = ed[ed.index < today_ts]
        if past.empty:
            return jsonify({"error": f"No past earnings dates found for {ticker}"}), 404
        past = past.head(12)

        # ── 2. Fetch 3 years of daily price history ─────────────────────────
        hist = t.history(period="3y")
        if hist is None or hist.empty:
            return jsonify({"error": f"No price history available for {ticker}"}), 404

        # Normalize history index to UTC
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize("UTC")

        # Build a date→row lookup using date strings for fast access
        hist_by_date = {}
        for ts, row in hist.iterrows():
            hist_by_date[ts.date()] = row

        sorted_dates = sorted(hist_by_date.keys())

        # ── 3. For each earnings date, find pre/post close ─────────────────
        quarters = []
        for earn_ts, row_data in past.iterrows():
            try:
                if earn_ts.tzinfo is None:
                    earn_ts = earn_ts.tz_localize("UTC")

                # Convert to ET to reliably determine pre-market vs post-market
                earn_ts_et = earn_ts.tz_convert("America/New_York")
                earn_date  = earn_ts_et.date()
                earn_hour  = earn_ts_et.hour

                # Determine timing:
                #   Pre-market  (hour < 12): earnings before open on earn_date
                #     → pre_close  = previous trading day close
                #     → post_close = earn_date close (market has already priced it in)
                #   Post-market (hour >= 12): earnings after close on earn_date
                #     → pre_close  = earn_date close (market hasn't reacted yet)
                #     → post_close = next trading day close
                is_premarket = earn_hour < 12

                if is_premarket:
                    # pre = day before earnings date, post = earnings date itself
                    pre_date  = next((d for d in reversed(sorted_dates) if d < earn_date), None)
                    post_date = next((d for d in sorted_dates if d >= earn_date), None)
                    timing_label = "Pre-market"
                else:
                    # pre = earnings date itself, post = next trading day
                    pre_date  = next((d for d in sorted_dates if d >= earn_date), None)
                    post_idx_start = sorted_dates.index(pre_date) + 1 if pre_date in sorted_dates else None
                    post_date = sorted_dates[post_idx_start] if post_idx_start is not None and post_idx_start < len(sorted_dates) else None
                    timing_label = "Post-market"

                if post_date is None or pre_date is None:
                    continue

                pre_close  = round(float(hist_by_date[pre_date]["Close"]),  2)
                post_close = round(float(hist_by_date[post_date]["Close"]), 2)
                move  = round(post_close - pre_close, 2)
                pct   = round((move / pre_close * 100) if pre_close else 0.0, 2)

                # EPS data from earnings_dates DataFrame columns
                eps_actual   = _safe_float(row_data.get("Reported EPS"))
                eps_estimate = _safe_float(row_data.get("EPS Estimate"))

                # ── 5-day post-earnings drift ──────────────────────────────
                try:
                    post_idx   = sorted_dates.index(post_date)
                    d5_idx     = min(post_idx + 5, len(sorted_dates) - 1)
                    d5_date    = sorted_dates[d5_idx]
                    close_5d   = round(float(hist_by_date[d5_date]["Close"]), 2)
                    drift_5d   = round(close_5d - post_close, 2)
                    drift_5d_pct = round((drift_5d / post_close * 100) if post_close else 0.0, 2)
                except Exception:
                    close_5d = drift_5d = drift_5d_pct = None
                    d5_date = None

                quarters.append({
                    "earn_date":     str(earn_date),
                    "timing":        timing_label,
                    "pre_date":      str(pre_date),
                    "pre_close":     pre_close,
                    "post_date":     str(post_date),
                    "post_close":    post_close,
                    "move":          move,
                    "pct":           pct,
                    "direction":     "up" if move > 0 else ("down" if move < 0 else "flat"),
                    "eps_actual":    eps_actual,
                    "eps_estimate":  eps_estimate,
                    "close_5d":      close_5d,
                    "d5_date":       str(d5_date) if d5_date else None,
                    "drift_5d_pct":  drift_5d_pct,
                    # guidance filled below via 8-K lookup
                    "guidance_signal":  None,
                    "guidance_snippet": None,
                    "guidance_source":  None,
                })
            except Exception as ex:
                print(f"  ⚠️  earn_hist skip {earn_ts}: {ex}")
                continue

        if not quarters:
            return jsonify({"error": f"Could not compute post-earnings moves for {ticker}"}), 404

        # ── Fetch 8-K guidance in parallel (one request set per quarter) ──
        def _market_implied_signal(q) -> str:
            """Estimate guidance tone from EPS beat/miss + 5-day post-earnings drift."""
            beat = (q["eps_actual"] is not None and q["eps_estimate"] is not None
                    and q["eps_actual"] >= q["eps_estimate"])
            miss = (q["eps_actual"] is not None and q["eps_estimate"] is not None
                    and q["eps_actual"] < q["eps_estimate"])
            d5 = q["drift_5d_pct"] or 0.0
            if beat and d5 > 2:
                return "Raised"
            elif beat and d5 < -2:
                return "Cautious"
            elif miss and d5 < -2:
                return "Weak / Cut"
            elif beat:
                return "In-Line"
            else:
                return "Mixed"

        def _fetch_q_guidance(q):
            try:
                g = _fetch_8k_guidance(ticker, q["earn_date"])
                if g:
                    sig = g["signal"]
                    filing_date = g.get("filing_date", "")
                    if sig == "Provided":
                        # Outlook section found but no explicit directional language —
                        # estimate direction from market reaction and label accordingly
                        estimated = _market_implied_signal(q)
                        q["guidance_signal"]  = estimated
                        q["guidance_snippet"] = g.get("snippet")
                        q["guidance_source"]  = f"~Est. via SEC 8-K ({filing_date})"
                    else:
                        q["guidance_signal"]  = sig
                        q["guidance_snippet"] = g.get("snippet")
                        q["guidance_source"]  = f"SEC 8-K ({filing_date})"
                else:
                    # No 8-K found — fully market-implied
                    q["guidance_signal"]  = _market_implied_signal(q)
                    q["guidance_snippet"] = None
                    q["guidance_source"]  = "~Est. (no 8-K found)"
            except Exception as eg:
                print(f"  ⚠️  guidance for {q['earn_date']}: {eg}")
            return q

        with ThreadPoolExecutor(max_workers=6) as ex:
            quarters = list(ex.map(_fetch_q_guidance, quarters))

        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass

        # Current live price
        curr_price = None
        try:
            fi = t.fast_info
            curr_price = _safe_float(getattr(fi, "last_price", None))
        except Exception:
            curr_price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))

        payload = {
            "ticker":     ticker,
            "name":       info.get("longName") or info.get("shortName") or ticker,
            "curr_price": curr_price,
            "quarters":   quarters,
        }
        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as e:
        print(f"⚠️  earnings history {ticker}: {e}")
        return jsonify({"error": str(e)}), 500


# ─── 13F Whale Tracker ────────────────────────────────────

# Major institutional investors and their SEC EDGAR CIK numbers
WHALES: dict[str, dict] = {
    "ackman": {
        "name": "Bill Ackman",
        "fund": "Pershing Square Capital Management",
        "cik": "1336528",
        "icon": "🐋",
    },
    "cathie_wood": {
        "name": "Cathie Wood",
        "fund": "ARK Investment Management",
        "cik": "1697748",
        "icon": "🚀",
    },
    "buffett": {
        "name": "Warren Buffett",
        "fund": "Berkshire Hathaway",
        "cik": "1067983",
        "icon": "🏛️",
    },
    "burry": {
        "name": "Michael Burry",
        "fund": "Scion Asset Management",
        "cik": "1649339",
        "icon": "🐻",
    },
    "tepper": {
        "name": "David Tepper",
        "fund": "Appaloosa Management",
        "cik": "1656456",
        "icon": "📈",
    },
    "dalio": {
        "name": "Ray Dalio",
        "fund": "Bridgewater Associates",
        "cik": "1350694",
        "icon": "🌊",
    },
    "loeb": {
        "name": "Daniel Loeb",
        "fund": "Third Point LLC",
        "cik": "1040273",
        "icon": "🎯",
    },
    "hohn": {
        "name": "Chris Hohn",
        "fund": "TCI Fund Management",
        "cik": "1647251",
        "icon": "🏹",
    },
    "aschenbrenner": {
        "name": "Leopold Aschenbrenner",
        "fund": "Situational Awareness LP",
        "cik": "2045724",
        "icon": "🤖",
    },
    "wedbush": {
        "name": "Dan Ives / Wedbush",
        "fund": "Wedbush Securities Inc",
        "cik": "1142495",
        "icon": "📡",
    },
    # ── Major Banks & Asset Managers ──────────────────────────────────────
    "blackrock": {
        "name": "Blackrock",
        "fund": "Blackrock Inc.",
        "cik": "1364742",
        "icon": "🏢",
    },
    "vanguard": {
        "name": "Vanguard Group",
        "fund": "Vanguard Group Inc",
        "cik": "102909",
        "icon": "🏛️",
    },
    "state_street": {
        "name": "State Street",
        "fund": "State Street Corporation",
        "cik": "93751",
        "icon": "🏦",
    },
    "jpmorgan": {
        "name": "JPMorgan Chase",
        "fund": "JPMorgan Chase & Co",
        "cik": "19617",
        "icon": "🏦",
    },
    "goldman": {
        "name": "Goldman Sachs",
        "fund": "Goldman Sachs Group Inc",
        "cik": "886982",
        "icon": "💼",
    },
    "morgan_stanley": {
        "name": "Morgan Stanley",
        "fund": "Morgan Stanley",
        "cik": "895421",
        "icon": "💼",
    },
    "citigroup": {
        "name": "Citigroup",
        "fund": "Citigroup Inc",
        "cik": "831001",
        "icon": "🏦",
    },
    "geode": {
        "name": "Geode Capital",
        "fund": "Geode Capital Management LLC",
        "cik": "1214717",
        "icon": "📐",
    },
    "t_rowe": {
        "name": "T. Rowe Price",
        "fund": "T. Rowe Price Associates Inc",
        "cik": "80255",
        "icon": "📊",
    },
    "fidelity": {
        "name": "Fidelity (FMR)",
        "fund": "Fidelity Management & Research",
        "cik": "315066",
        "icon": "🏢",
    },
    "invesco": {
        "name": "Invesco",
        "fund": "Invesco Ltd",
        "cik": "914208",
        "icon": "📈",
    },
    "ubs": {
        "name": "UBS Group",
        "fund": "UBS Group AG",
        "cik": "1610520",
        "icon": "🇨🇭",
    },
    "deutsche": {
        "name": "Deutsche Bank",
        "fund": "Deutsche Bank AG",
        "cik": "948046",
        "icon": "🇩🇪",
    },
    "norges": {
        "name": "Norges Bank",
        "fund": "Norges Bank (Norway SWF)",
        "cik": "1374170",
        "icon": "🇳🇴",
    },
    "wells_fargo": {
        "name": "Wells Fargo",
        "fund": "Wells Fargo & Company",
        "cik": "72971",
        "icon": "🐎",
    },
    "bank_of_america": {
        "name": "Bank of America",
        "fund": "Bank of America Corp",
        "cik": "70858",
        "icon": "🏦",
    },
}

# Major institutional fund managers / banks — included in the "Recent Activity" scan
# These are the 13F-HR filing CIKs for each firm's primary investment management entity
MAJOR_INSTITUTIONS: dict[str, dict] = {
    "blackrock": {
        "name": "Blackrock Inc.",
        "cik": "1364742",
        "icon": "🏢",
    },
    "vanguard": {
        "name": "Vanguard Group",
        "cik": "102909",
        "icon": "🏛️",
    },
    "state_street": {
        "name": "State Street Corp",
        "cik": "93751",
        "icon": "🏦",
    },
    "jpmorgan": {
        "name": "JPMorgan Chase & Co",
        "cik": "19617",
        "icon": "🏦",
    },
    "goldman": {
        "name": "Goldman Sachs Group",
        "cik": "886982",
        "icon": "💼",
    },
    "morgan_stanley": {
        "name": "Morgan Stanley",
        "cik": "895421",
        "icon": "💼",
    },
    "citigroup": {
        "name": "Citigroup Inc",
        "cik": "831001",
        "icon": "🏦",
    },
    "geode": {
        "name": "Geode Capital Management",
        "cik": "1214717",
        "icon": "📐",
    },
    "t_rowe": {
        "name": "T. Rowe Price Associates",
        "cik": "80255",
        "icon": "📊",
    },
    "fidelity": {
        "name": "Fidelity Management & Research",
        "cik": "315066",
        "icon": "🏢",
    },
    "invesco": {
        "name": "Invesco Ltd",
        "cik": "914208",
        "icon": "📈",
    },
    "wells_fargo": {
        "name": "Wells Fargo & Co",
        "cik": "72971",
        "icon": "🐎",
    },
    "bank_of_america": {
        "name": "Bank of America Corp",
        "cik": "70858",
        "icon": "🏦",
    },
    "ubs": {
        "name": "UBS Group AG",
        "cik": "1610520",
        "icon": "🇨🇭",
    },
    "deutsche": {
        "name": "Deutsche Bank AG",
        "cik": "948046",
        "icon": "🇩🇪",
    },
    "norges": {
        "name": "Norges Bank (Norway SWF)",
        "cik": "1374170",
        "icon": "🇳🇴",
    },
}

EDGAR_HEADERS = {
    "User-Agent": "StocksDashboard contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}
WHALE_CACHE_TTL  = 3600   # 1 hour — 13F filings only update quarterly
_ticker_cache: dict = {}  # cusip → ticker symbol (persists for process lifetime)


def _yahoo_ticker(cusip: str, name: str) -> str | None:
    """Resolve a CUSIP to a US exchange ticker via Yahoo Finance search.
    Falls back to searching by company name. Result is cached in-process."""
    key = cusip or name
    if key in _ticker_cache:
        return _ticker_cache[key]

    for query in filter(None, [cusip, name]):
        try:
            from urllib.parse import quote as _urlquote
            url = (
                "https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={_urlquote(query)}&quotesCount=3&newsCount=0&enableFuzzyQuery=false"
            )
            r = _http.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            quotes = r.json().get("quotes", [])
            for q in quotes:
                sym = q.get("symbol", "")
                # prefer plain US tickers (no dot, no hyphen suffix)
                if sym and "." not in sym and q.get("exchange", "") in (
                    "NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "", "NYSEArca"
                ):
                    _ticker_cache[key] = sym
                    return sym
        except Exception:
            pass

    _ticker_cache[key] = None
    return None


def _enrich_live_prices(rows: list[dict]) -> list[dict]:
    """Add ticker, current_price, and return-since-quarter-end to each row.
    Ticker lookups run in parallel; prices are batch-fetched via yfinance."""
    if not rows:
        return rows

    # ── Step 1: resolve tickers in parallel ──────────────────────────────
    def _lookup(row):
        return row, _yahoo_ticker(row.get("cusip", ""), row.get("name", ""))

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_lookup, r): r for r in rows}
        ticker_map = {}   # row index → ticker
        for fut in as_completed(futures):
            row, ticker = fut.result()
            ticker_map[id(row)] = ticker

    # ── Step 2: batch-download current prices via yfinance ────────────────
    unique_tickers = list({t for t in ticker_map.values() if t})
    price_map: dict[str, float] = {}
    if unique_tickers:
        try:
            raw = yf.download(
                unique_tickers,
                period="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            closes = raw["Close"] if "Close" in raw else raw
            last = closes.iloc[-1] if not closes.empty else pd.Series(dtype=float)
            for sym in unique_tickers:
                if sym in last.index and not pd.isna(last[sym]):
                    price_map[sym] = round(float(last[sym]), 2)
        except Exception as e:
            print(f"⚠️  yfinance batch price fetch failed: {e}")

    # ── Step 3: annotate each row ─────────────────────────────────────────
    for row in rows:
        ticker    = ticker_map.get(id(row))
        curr_px   = price_map.get(ticker) if ticker else None
        implied   = row.get("implied_price")   # quarter-end price from 13F

        row["ticker"]             = ticker or "—"
        row["current_price"]      = curr_px
        row["current_price_fmt"]  = f"${curr_px:,.2f}" if curr_px is not None else "—"

        if curr_px is not None and implied:
            ret = round((curr_px - implied) / implied * 100, 1)
            row["return_since_qend"]     = ret
            row["return_since_qend_fmt"] = f"{'+' if ret >= 0 else ''}{ret:.1f}%"
        else:
            row["return_since_qend"]     = None
            row["return_since_qend_fmt"] = "—"

    return rows


def _get_recent_13f_accessions(cik: str, count: int = 2) -> list[dict]:
    """Return the most-recent `count` 13F-HR filings for the given CIK."""
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    r = _http.get(url, headers=EDGAR_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    recent   = data.get("filings", {}).get("recent", {})
    forms    = recent.get("form", [])
    accs     = recent.get("accessionNumber", [])
    dates    = recent.get("filingDate", [])
    reports  = recent.get("reportDate", [])

    results = []
    for i, form in enumerate(forms):
        if form == "13F-HR":
            results.append({
                "accession":   accs[i],
                "filed":       dates[i],
                "report_date": reports[i] if i < len(reports) else dates[i],
            })
            if len(results) >= count:
                break
    return results


def _get_infotable_xml(cik: str, accession: str) -> str | None:
    """Download the 13F information-table XML for a specific filing."""
    cik_int   = str(int(cik))
    acc_clean = accession.replace("-", "")
    base_url  = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int}/{acc_clean}/"
    )
    # Fetch the HTML directory listing for this filing
    r = _http.get(base_url, headers=EDGAR_HEADERS, timeout=15)
    if r.status_code != 200:
        return None
    # Extract all .xml hrefs from the directory listing
    hrefs = re.findall(r'href="([^"]+\.xml)"', r.text, re.IGNORECASE)
    keywords = ("infotable", "information_table", "13finfo", "form13finfo")
    # First pass: look for well-known infotable filenames
    for href in hrefs:
        fname = href.split("/")[-1]
        if any(k in fname.lower() for k in keywords):
            file_url = (
                f"https://www.sec.gov{href}"
                if href.startswith("/")
                else base_url + fname
            )
            fr = _http.get(file_url, headers=EDGAR_HEADERS, timeout=20)
            if fr.status_code == 200:
                return fr.text
    # Second pass: try each XML file and detect by <infoTable> content
    skip_names = ("primary_doc", "primarydoc", "form13f-hr", "cover")
    for href in hrefs:
        fname = href.split("/")[-1]
        if any(s in fname.lower() for s in skip_names):
            continue
        file_url = (
            f"https://www.sec.gov{href}"
            if href.startswith("/")
            else base_url + fname
        )
        fr = _http.get(file_url, headers=EDGAR_HEADERS, timeout=20)
        if fr.status_code == 200 and "infoTable" in fr.text:
            return fr.text
    return None


def _strip_ns_parse(xml_text: str):
    """Parse XML, stripping all namespace declarations and prefixes."""
    # remove namespace prefixes from tags  (<ns1:foo> → <foo>)
    clean = re.sub(r"(</?)\w+:", r"\1", xml_text)
    # remove xmlns declarations
    clean = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", clean)
    # remove namespace-prefixed attributes (e.g. xsi:schemaLocation="...")
    clean = re.sub(r'\s+\w+:\w+="[^"]*"', "", clean)
    return ET.fromstring(clean)


def _parse_infotable(xml_text: str) -> list[dict]:
    """Parse a 13F infotable XML string into a list of holding dicts."""
    try:
        root = _strip_ns_parse(xml_text)
    except ET.ParseError as e:
        print(f"⚠️  13F XML parse error: {e}")
        return []

    holdings = []
    for info in root.iter("infoTable"):
        issuer   = (info.findtext("nameOfIssuer")  or "").strip()
        cusip    = (info.findtext("cusip")          or "").strip()
        val_raw  = (info.findtext("value")          or "0").replace(",", "")
        put_call = (info.findtext("putCall")        or "").strip()

        shr_el = info.find("shrsOrPrnAmt")
        shares = 0
        if shr_el is not None:
            try:
                shares = int((shr_el.findtext("sshPrnamt") or "0").replace(",", ""))
            except ValueError:
                pass

        try:
            value_k = int(val_raw)
        except ValueError:
            value_k = 0

        if issuer and cusip:
            implied_price = round(value_k / shares, 2) if shares else None
            holdings.append({
                "name":          issuer,
                "cusip":         cusip,
                "value_k":       value_k,   # raw as-reported units (dollars OR thousands)
                "shares":        shares,
                "put_call":      put_call,
                "implied_price": implied_price,
            })

    # ── Auto-detect value units: most filers report in thousands, but some large
    # filers (e.g. Berkshire, Bridgewater) report in whole dollars.
    # Heuristic: if the median implied price across the portfolio is < $2.00,
    # the values are almost certainly in thousands — multiply by 1000 to normalise.
    if holdings:
        prices = [h["implied_price"] for h in holdings
                  if h["implied_price"] is not None and h["implied_price"] > 0]
        if prices:
            median_price = sorted(prices)[len(prices) // 2]
            if median_price < 2.0:                       # thousands format detected
                for h in holdings:
                    h["value_k"] *= 1000                 # normalise → whole dollars
                    h["implied_price"] = (
                        round(h["value_k"] / h["shares"], 2) if h["shares"] else None
                    )

    return holdings


def _has_real_13f_holdings(holdings: list[dict]) -> bool:
    """Return true when a parsed 13F has usable holdings, not a placeholder row."""
    for h in holdings:
        issuer = (h.get("name") or "").strip().upper()
        cusip = (h.get("cusip") or "").strip()
        if (
            issuer
            and issuer not in {"NA", "N/A", "NONE"}
            and cusip
            and cusip != "000000000"
            and (h.get("shares") or 0) > 0
            and (h.get("value_k") or 0) > 0
        ):
            return True
    return False


def _fmt_value(value_k: int) -> str:
    """Format a dollar value (normalised to whole dollars after parse) into a readable string."""
    v = value_k  # value is in whole dollars after auto-normalisation in _parse_infotable
    sign = "+" if v >= 0 else "-"
    av = abs(v)
    if av >= 1e12:
        return f"${sign}{av / 1e12:.2f}T"
    if av >= 1e9:
        return f"${sign}{av / 1e9:.2f}B"
    if av >= 1e6:
        return f"${sign}{av / 1e6:.2f}M"
    return f"${sign}{av / 1e3:.0f}K"


def _compare_holdings(current: list[dict], previous: list[dict]) -> dict:
    """Diff two holdings lists → top-10 buys and top-10 sells."""
    curr_map = {h["cusip"]: h for h in current  if h["cusip"]}
    prev_map = {h["cusip"]: h for h in previous if h["cusip"]}

    buys, sells = [], []

    for cusip in set(curr_map) | set(prev_map):
        c = curr_map.get(cusip)
        p = prev_map.get(cusip)

        if c and not p:                                    # brand-new position
            buys.append({
                "name":              c["name"],
                "cusip":             cusip,
                "action":            "NEW",
                "shares_change":     c["shares"],
                "value_change_k":    c["value_k"],
                "pct_change":        None,
                "curr_value_k":      c["value_k"],
                "curr_shares":       c["shares"],
                "put_call":          c.get("put_call", ""),
                "implied_price":     c.get("implied_price"),
                "prev_implied_price": None,
            })
        elif p and not c:                                  # fully exited
            sells.append({
                "name":              p["name"],
                "cusip":             cusip,
                "action":            "EXIT",
                "shares_change":     -p["shares"],
                "value_change_k":    -p["value_k"],
                "pct_change":        -100.0,
                "curr_value_k":      0,
                "curr_shares":       0,
                "put_call":          p.get("put_call", ""),
                "implied_price":     None,
                "prev_implied_price": p.get("implied_price"),
            })
        elif c and p:
            delta_sh = c["shares"] - p["shares"]
            delta_vk = c["value_k"] - p["value_k"]
            pct = round(delta_sh / p["shares"] * 100, 1) if p["shares"] else 0.0

            item = {
                "name":              c["name"],
                "cusip":             cusip,
                "shares_change":     delta_sh,
                "value_change_k":    delta_vk,
                "pct_change":        pct,
                "curr_value_k":      c["value_k"],
                "curr_shares":       c["shares"],
                "put_call":          c.get("put_call", ""),
                "implied_price":     c.get("implied_price"),
                "prev_implied_price": p.get("implied_price"),
            }
            if delta_sh > 0:
                buys.append({**item, "action": "ADD"})
            elif delta_sh < 0:
                sells.append({**item, "action": "REDUCE"})

    buys.sort(key=lambda x: abs(x["value_change_k"]),  reverse=True)
    sells.sort(key=lambda x: abs(x["value_change_k"]), reverse=True)

    def _fmt(row: dict) -> dict:
        row["value_change_fmt"]   = _fmt_value(row["value_change_k"])
        row["curr_value_fmt"]     = _fmt_value(row["curr_value_k"])
        ip  = row.get("implied_price")
        pip = row.get("prev_implied_price")
        row["implied_price_fmt"]      = f"${ip:,.2f}"  if ip  is not None else "—"
        row["prev_implied_price_fmt"] = f"${pip:,.2f}" if pip is not None else "—"
        # price change vs prior quarter (for ADD/REDUCE)
        if ip is not None and pip is not None:
            price_chg_pct = round((ip - pip) / pip * 100, 1) if pip else None
            row["price_chg_pct"] = price_chg_pct
            row["price_chg_fmt"] = (
                f"{'+' if price_chg_pct >= 0 else ''}{price_chg_pct:.1f}%"
                if price_chg_pct is not None else "—"
            )
        else:
            row["price_chg_pct"] = None
            row["price_chg_fmt"] = "—"
        return row

    return {
        "top_buys":  [_fmt(r) for r in buys[:10]],
        "top_sells": [_fmt(r) for r in sells[:10]],
    }


def _get_all_13f_accessions(cik: str) -> list[dict]:
    """Return all 13F-HR filings available in EDGAR recent (~last 3 years)."""
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    r = _http.get(url, headers=EDGAR_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    recent  = data.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accs    = recent.get("accessionNumber", [])
    dates   = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])

    results = []
    for i, form in enumerate(forms):
        if form == "13F-HR":
            report_date = reports[i] if i < len(reports) else dates[i]
            # only keep last ~3 years (12 quarters + a little buffer)
            try:
                from datetime import datetime as _dt
                rdate = _dt.strptime(report_date, "%Y-%m-%d")
                if (_dt.now() - rdate).days > 365 * 3 + 90:
                    break
            except Exception:
                pass
            results.append({
                "accession":   accs[i],
                "filed":       dates[i],
                "report_date": report_date,
            })
    return results


def _fetch_whale_data(whale_key: str, accession: str | None = None) -> dict | None:
    """Fetch, compare, and cache 13F data for one whale investor.

    If `accession` is given, use that filing as 'current'; otherwise use latest.
    The previous quarter is always the filing immediately before in the list.
    """
    info = WHALES.get(whale_key)
    if not info:
        return None

    cache_key = f"whale:{whale_key}:{accession or 'latest'}"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < WHALE_CACHE_TTL:
            print(f"📦 Whale cache hit: {cache_key}")
            return cached

    try:
        cik      = info["cik"]
        # always fetch all available filings so we can find the prev quarter
        filings  = _get_all_13f_accessions(cik)
        if not filings:
            return None

        # Pick the target filing, skipping placeholder filings with no real holdings
        # (some large foreign managers file an empty/NA 13F row for a quarter).
        start_idx = next((i for i, f in enumerate(filings) if f["accession"] == accession), 0) if accession else 0

        curr_filing = None
        curr_holdings = []
        idx = start_idx
        for cand_idx in range(start_idx, len(filings)):
            cand = filings[cand_idx]
            cand_xml = _get_infotable_xml(cik, cand["accession"])
            if not cand_xml:
                continue
            cand_holdings = _parse_infotable(cand_xml)
            if _has_real_13f_holdings(cand_holdings):
                curr_filing = cand
                curr_holdings = cand_holdings
                idx = cand_idx
                break
            print(f"⚠️  Skipping placeholder 13F for {info['name']}: {cand['accession']}")

        if not curr_filing:
            return None

        prev_filing = None
        prev_holdings = []
        for cand in filings[idx + 1:]:
            prev_xml = _get_infotable_xml(cik, cand["accession"])
            if not prev_xml:
                continue
            cand_holdings = _parse_infotable(prev_xml)
            if _has_real_13f_holdings(cand_holdings):
                prev_filing = cand
                prev_holdings = cand_holdings
                break
            print(f"⚠️  Skipping placeholder prior 13F for {info['name']}: {cand['accession']}")

        diff = _compare_holdings(curr_holdings, prev_holdings)
        total_value = sum(h["value_k"] for h in curr_holdings)

        # Enrich buys and sells with live price + return since quarter-end
        try:
            all_rows = diff["top_buys"] + diff["top_sells"]
            enriched = _enrich_live_prices(all_rows)
            n = len(diff["top_buys"])
            diff["top_buys"]  = enriched[:n]
            diff["top_sells"] = enriched[n:]
        except Exception as e:
            print(f"⚠️  Live price enrichment skipped: {e}")

        result = {
            "whale_key":       whale_key,
            "name":            info["name"],
            "fund":            info["fund"],
            "icon":            info["icon"],
            "cik":             cik,
            "current_period":  curr_filing["report_date"],
            "previous_period": prev_filing["report_date"] if prev_filing else None,
            "filed_date":      curr_filing["filed"],
            "accession":       curr_filing["accession"],
            "total_positions": len(curr_holdings),
            "portfolio_value": _fmt_value(total_value),
            "top_buys":        diff["top_buys"],
            "top_sells":       diff["top_sells"],
            "last_updated":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _cache[cache_key] = (time.time(), result)
        print(f"✅ Whale 13F fetched: {info['name']} — {curr_filing['report_date']} — {len(curr_holdings)} positions")
        return result

    except Exception as e:
        print(f"⚠️  _fetch_whale_data({whale_key}): {e}")
        return None


@app.route("/api/whales")
def list_whales():
    """Return the list of tracked whale investors."""
    BANK_KEYS = {"blackrock","vanguard","state_street","jpmorgan","goldman",
                 "morgan_stanley","citigroup","geode","t_rowe","fidelity",
                 "invesco","ubs","deutsche","norges","wells_fargo","bank_of_america"}
    return jsonify([
        {
            "key":   k,
            "name":  v["name"],
            "fund":  v["fund"],
            "icon":  v["icon"],
            "group": "bank" if k in BANK_KEYS else "fund",
        }
        for k, v in WHALES.items()
    ])


@app.route("/api/whales/<whale_key>/quarters")
def get_whale_quarters(whale_key: str):
    """Return all available 13F quarters for a whale investor."""
    info = WHALES.get(whale_key)
    if not info:
        return jsonify({"error": f"Unknown whale: {whale_key}"}), 404
    cache_key = f"whale_quarters:{whale_key}"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < WHALE_CACHE_TTL:
            return jsonify(cached)
    try:
        filings = _get_all_13f_accessions(info["cik"])
        result  = [
            {"accession": f["accession"], "report_date": f["report_date"], "filed": f["filed"]}
            for f in filings
        ]
        _cache[cache_key] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/whales/<whale_key>")
def get_whale(whale_key: str):
    """Return parsed 13F buy/sell activity for one whale investor.

    Optional query param: ?quarter=<accession-number>
    """
    if whale_key not in WHALES:
        return jsonify({"error": f"Unknown whale: {whale_key}"}), 404
    accession = request.args.get("quarter")   # e.g. ?quarter=0001336528-26-000004
    data = _fetch_whale_data(whale_key, accession or None)
    if not data:
        return jsonify({"error": "Could not fetch 13F data from SEC EDGAR"}), 500
    return jsonify(data)


# ─── Institutional Ownership ───────────────────────────────

@app.route("/api/ownership")
def get_ownership():
    """Return institutional and mutual-fund holders for a ticker.

    Query params:
        ticker – stock symbol (required)
    """
    ticker = request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    cache_key = f"ownership:{ticker}"
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < 3600:
            return jsonify(data)

    try:
        t = yf.Ticker(ticker)

        # ── Major-holder breakdown ────────────────────────────────────
        # yfinance returns a DataFrame with index = label, single "Value" column
        major: dict[str, str] = {}
        _PCT_LABELS = {
            "insidersPercentHeld":          "Held by Insiders",
            "institutionsPercentHeld":       "Held by Institutions",
            "institutionsFloatPercentHeld":  "% of Float (Institutions)",
            "institutionsCount":             "# of Institutions",
        }
        try:
            mh = t.major_holders
            if mh is not None and not (hasattr(mh, "empty") and mh.empty):
                for label, val in mh["Value"].items():
                    label_str = _PCT_LABELS.get(str(label), str(label))
                    if "Count" in label_str or "institutionsCount" in str(label):
                        major[label_str] = str(int(val))
                    else:
                        major[label_str] = f"{round(float(val) * 100, 2)}%"
        except Exception:
            pass

        def _parse_holders(df) -> list[dict]:
            rows = []
            if df is None or (hasattr(df, "empty") and df.empty):
                return rows
            for _, row in df.iterrows():
                pct = row.get("pctHeld") or row.get("% Out") or 0
                date_raw = row.get("Date Reported") or row.get("dateReported") or ""
                try:
                    date_str = str(date_raw.date()) if hasattr(date_raw, "date") else str(date_raw)[:10]
                except Exception:
                    date_str = str(date_raw)[:10]
                rows.append({
                    "holder":        str(row.get("Holder") or row.get("holder") or ""),
                    "shares":        int(row.get("Shares") or row.get("shares") or 0),
                    "date_reported": date_str,
                    "pct_out":       round(float(pct) * 100, 4),
                    "value":         int(row.get("Value") or row.get("value") or 0),
                })
            return rows

        # ── Institutional holders ─────────────────────────────────────
        inst: list[dict] = []
        try:
            inst = _parse_holders(t.institutional_holders)
        except Exception:
            pass

        # ── Mutual-fund holders ───────────────────────────────────────
        mf: list[dict] = []
        try:
            mf = _parse_holders(t.mutualfund_holders)
        except Exception:
            pass

        payload = {
            "ticker":                ticker,
            "major_holders":         major,
            "institutional_holders": inst,
            "mutualfund_holders":    mf,
            "last_updated":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as e:
        print(f"⚠️  ownership {ticker}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ownership/whales")
def get_whale_ownership():
    """Return which tracked whale investors hold a given ticker in their latest 13F.

    Query params:
        ticker – stock symbol (required)
    """
    ticker = request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    cache_key = f"whale_own:{ticker}"
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < 3600:
            return jsonify(data)

    # Get company name keywords for fuzzy name matching
    company_keywords: list[str] = []
    try:
        info = yf.Ticker(ticker).info or {}
        name = (info.get("longName") or info.get("shortName") or "").lower()
        # keep words >3 chars and drop very common words
        _stop = {"inc", "corp", "llc", "ltd", "plc", "the", "and", "class", "common"}
        company_keywords = [w for w in re.split(r'[\s,\.]+', name) if len(w) > 3 and w not in _stop]
    except Exception:
        pass

    def _check_whale(whale_key: str) -> dict | None:
        info = WHALES[whale_key]
        try:
            filings = _get_recent_13f_accessions(info["cik"], count=1)
            if not filings:
                return None
            filing = filings[0]
            xml = _get_infotable_xml(info["cik"], filing["accession"])
            if not xml:
                return None
            holdings = _parse_infotable(xml)
            total_value = sum(h["value_k"] for h in holdings)

            for h in holdings:
                cusip = h.get("cusip", "")
                hname = h.get("name", "").lower()

                # Fast path: CUSIP already resolved and cached
                cached_tkr = _ticker_cache.get(cusip)
                if cached_tkr is not None:
                    if cached_tkr.upper() != ticker:
                        continue
                    # Match — fall through to build result
                else:
                    # Slow path: check if holding name resembles the target company
                    if not company_keywords or not any(kw in hname for kw in company_keywords):
                        continue
                    resolved = _yahoo_ticker(cusip, h.get("name", ""))
                    if not resolved or resolved.upper() != ticker:
                        continue

                pct_portfolio = round(h["value_k"] / total_value * 100, 2) if total_value else None
                ip = h.get("implied_price")
                return {
                    "whale_key":          whale_key,
                    "name":               info["name"],
                    "fund":               info["fund"],
                    "icon":               info["icon"],
                    "period":             filing["report_date"],
                    "shares":             h["shares"],
                    "value_k":            h["value_k"],
                    "value_fmt":          _fmt_value(h["value_k"]),
                    "implied_price":      ip,
                    "implied_price_fmt":  f"${ip:,.2f}" if ip is not None else "—",
                    "pct_portfolio":      pct_portfolio,
                    "put_call":           h.get("put_call", ""),
                }
        except Exception as exc:
            print(f"⚠️  whale_own check {whale_key}: {exc}")
        return None

    with ThreadPoolExecutor(max_workers=len(WHALES)) as ex:
        raw = list(ex.map(_check_whale, WHALES.keys()))

    results = sorted(
        [r for r in raw if r],
        key=lambda x: x["value_k"],
        reverse=True,
    )

    payload = {
        "ticker":       ticker,
        "whales":       results,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ─── Institutional Activity (Who Added / Reduced) ──────────────

@app.route("/api/ownership/activity")
def get_ownership_activity():
    """Return which major institutions/whales added, reduced, entered or exited
    a given ticker between their two most-recent 13F-HR filings.

    Query params:
        ticker – stock symbol (required)
    """
    ticker = request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    cache_key = f"own_activity:{ticker}"
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < 21600:      # 6-hour cache (13F data is quarterly)
            return jsonify(data)

    # Build keyword list for fuzzy name matching
    company_keywords: list[str] = []
    try:
        yinfo = yf.Ticker(ticker).info or {}
        raw_name = (yinfo.get("longName") or yinfo.get("shortName") or "").lower()
        _stop = {"inc", "corp", "llc", "ltd", "plc", "the", "and", "class", "common", "group", "holdings"}
        company_keywords = [w for w in re.split(r"[\s,\.\-]+", raw_name)
                            if len(w) > 3 and w not in _stop]
    except Exception:
        pass

    # Merge WHALES + MAJOR_INSTITUTIONS into one combined scan dict
    ALL_FILERS: dict[str, dict] = {}
    for k, v in WHALES.items():
        ALL_FILERS[k] = {
            "name":  v.get("fund") or v["name"],
            "label": v["name"],
            "cik":   v["cik"],
            "icon":  v["icon"],
            "type":  "whale",
        }
    for k, v in MAJOR_INSTITUTIONS.items():
        if k not in ALL_FILERS:
            ALL_FILERS[k] = {
                "name":  v["name"],
                "label": v["name"],
                "cik":   v["cik"],
                "icon":  v["icon"],
                "type":  "institution",
            }

    def _find_holding(holdings: list[dict]) -> dict | None:
        """Find the matching holding entry for our target ticker."""
        for h in holdings:
            cusip = h.get("cusip", "")
            hname = h.get("name", "").lower()
            cached_tkr = _ticker_cache.get(cusip)
            if cached_tkr is not None:
                if cached_tkr.upper() == ticker:
                    return h
                continue
            if not company_keywords:
                continue
            if any(kw in hname for kw in company_keywords):
                resolved = _yahoo_ticker(cusip, h.get("name", ""))
                if resolved and resolved.upper() == ticker:
                    return h
        return None

    def _check_filer(filer_key: str) -> dict | None:
        info = ALL_FILERS[filer_key]
        try:
            filings = _get_recent_13f_accessions(info["cik"], count=2)
            if not filings:
                return None
            curr_filing = filings[0]
            prev_filing = filings[1] if len(filings) > 1 else None

            curr_xml = _get_infotable_xml(info["cik"], curr_filing["accession"])
            if not curr_xml:
                return None
            curr_holdings = _parse_infotable(curr_xml)
            curr_h = _find_holding(curr_holdings)

            prev_h = None
            if prev_filing:
                prev_xml = _get_infotable_xml(info["cik"], prev_filing["accession"])
                if prev_xml:
                    prev_holdings = _parse_infotable(prev_xml)
                    prev_h = _find_holding(prev_holdings)

            # Determine action
            if curr_h is None and prev_h is None:
                return None   # not held in either quarter

            if curr_h and not prev_h:
                action = "NEW"
                shares_change = curr_h["shares"]
                value_change  = curr_h["value_k"]
                pct_change    = None
            elif prev_h and not curr_h:
                action = "EXIT"
                shares_change = -prev_h["shares"]
                value_change  = -prev_h["value_k"]
                pct_change    = -100.0
            else:
                delta_sh = curr_h["shares"] - prev_h["shares"]
                delta_vk = curr_h["value_k"] - prev_h["value_k"]
                if delta_sh == 0:
                    return None   # unchanged — not interesting
                pct_change = round(delta_sh / prev_h["shares"] * 100, 1) if prev_h["shares"] else 0.0
                if delta_sh > 0:
                    action = "ADD"
                else:
                    action = "REDUCE"
                shares_change = delta_sh
                value_change  = delta_vk

            h_ref = curr_h or prev_h
            ip = h_ref.get("implied_price")

            return {
                "key":               filer_key,
                "name":              info["label"],
                "fund":              info["name"],
                "icon":              info["icon"],
                "type":              info["type"],
                "action":            action,
                "shares_change":     shares_change,
                "shares_change_fmt": (("+" if shares_change > 0 else "") + fmtShares_py(abs(shares_change))),
                "value_change_k":    value_change,
                "value_change_fmt":  _fmt_value(value_change),
                "pct_change":        pct_change,
                "curr_shares":       (curr_h["shares"] if curr_h else 0),
                "curr_value_fmt":    _fmt_value(curr_h["value_k"]) if curr_h else "—",
                "implied_price":     ip,
                "implied_price_fmt": f"${ip:,.2f}" if ip is not None else "—",
                "period":            curr_filing["report_date"],
                "prev_period":       prev_filing["report_date"] if prev_filing else None,
            }
        except Exception as exc:
            print(f"⚠️  own_activity {filer_key}: {exc}")
        return None

    def fmtShares_py(s: int) -> str:
        if s >= 1_000_000_000:
            return f"{s / 1e9:.2f}B"
        if s >= 1_000_000:
            return f"{s / 1e6:.2f}M"
        if s >= 1_000:
            return f"{s / 1e3:.1f}K"
        return str(s)

    max_workers = min(32, len(ALL_FILERS))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        raw = list(ex.map(_check_filer, ALL_FILERS.keys()))

    # Split into buyers (NEW/ADD) and sellers (REDUCE/EXIT)
    buys  = sorted([r for r in raw if r and r["action"] in ("NEW", "ADD")],
                   key=lambda x: abs(x["value_change_k"]), reverse=True)
    sells = sorted([r for r in raw if r and r["action"] in ("REDUCE", "EXIT")],
                   key=lambda x: abs(x["value_change_k"]), reverse=True)

    payload = {
        "ticker":       ticker,
        "buys":         buys,
        "sells":        sells,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ═══════════════════════════════════════════════════════════════
# POLITICIAN TRADES — STOCK Act Disclosures
# ═══════════════════════════════════════════════════════════════
import zipfile
import io as _io
import pdfplumber

POL_CACHE_TTL    = 3600   # 1 hour
POL_HOUSE_INDEX  = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
POL_HOUSE_PDF    = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
POL_HEADERS      = {"User-Agent": "StocksDashboard contact@example.com"}

# Known party affiliations for current House members (D / R / I)
# We derive this from the state/district using a built-in lookup
# (kept trimmed — covers the most active traders)
_PARTY_LOOKUP: dict[str, str] = {}   # populated lazily

# Company-name keyword → ticker for common stocks that appear in PTR filings
# without a parenthetical ticker symbol.  Longest matches checked first.
_COMPANY_TICKER: dict[str, str] = {
    "microsoft corporation":      "MSFT",
    "apple inc":                  "AAPL",
    "nvidia corporation":         "NVDA",
    "alphabet inc":               "GOOGL",
    "amazon.com":                 "AMZN",
    "meta platforms":             "META",
    "tesla, inc":                 "TSLA",
    "berkshire hathaway":         "BRK-B",
    "unitedhealth group":         "UNH",
    "jpmorgan chase":             "JPM",
    "exxon mobil":                "XOM",
    "johnson & johnson":          "JNJ",
    "visa inc":                   "V",
    "mastercard":                 "MA",
    "procter & gamble":           "PG",
    "home depot":                 "HD",
    "chevron corporation":        "CVX",
    "abbvie inc":                 "ABBV",
    "merck & co":                 "MRK",
    "costco wholesale":           "COST",
    "bank of america":            "BAC",
    "pepsico, inc":               "PEP",
    "coca-cola":                  "KO",
    "wells fargo":                "WFC",
    "salesforce":                 "CRM",
    "adobe inc":                  "ADBE",
    "netflix, inc":               "NFLX",
    "broadcom inc":               "AVGO",
    "intel corporation":          "INTC",
    "advanced micro devices":     "AMD",
    "qualcomm":                   "QCOM",
    "micron technology":          "MU",
    "applied materials":          "AMAT",
    "lam research":               "LRCX",
    "palo alto networks":         "PANW",
    "cloudflare":                 "NET",
    "servicenow":                 "NOW",
    "intuit inc":                 "INTU",
    "paypal":                     "PYPL",
    "uber technologies":          "UBER",
    "airbnb":                     "ABNB",
    "snowflake":                  "SNOW",
    "palantir":                   "PLTR",
    "coinbase":                   "COIN",
    "goldman sachs":              "GS",
    "morgan stanley":             "MS",
    "citigroup":                  "C",
    "blackrock":                  "BLK",
    "american express":           "AXP",
    "charles schwab":             "SCHW",
    "at&t inc":                   "T",
    "verizon":                    "VZ",
    "walt disney":                "DIS",
    "comcast":                    "CMCSA",
    "gilead sciences":            "GILD",
    "eli lilly":                  "LLY",
    "pfizer inc":                 "PFE",
    "bristol-myers squibb":       "BMY",
    "amgen inc":                  "AMGN",
    "boeing company":             "BA",
    "lockheed martin":            "LMT",
    "raytheon":                   "RTX",
    "general electric":           "GE",
    "caterpillar":                "CAT",
    "deere & company":            "DE",
    "eaton corporation":          "ETN",
    "3m company":                 "MMM",
    "union pacific":              "UNP",
    "fedex corporation":          "FDX",
    "united parcel service":      "UPS",
    "ford motor":                 "F",
    "general motors":             "GM",
    "crown castle":               "CCI",
    "american tower":             "AMT",
    "prologis":                   "PLD",
    "simon property":             "SPG",
    "international business machines": "IBM",
    "lumentum":                   "LITE",
    "freeport-mcmoran":           "FCX",
    "utz brands":                 "UTZ",
    "commvault":                  "CVLT",
    "martin marietta":            "MLM",
    "tri pointe homes":           "TPH",
    "darden restaurants":         "DRI",
    "tractor supply":             "TSCO",
    "dollar tree":                "DLTR",
    "marketaxess":                "MKTX",
    "synchrony financial":        "SYF",
    "valley national":            "VLY",
    "cbiz, inc":                  "CBZ",
    "fair isaac":                 "FICO",
    "intercontinental exchange":  "ICE",
    "cheniere energy":            "LNG",
    "air products":               "APD",
    "exxon mobil":                "XOM",
    "monster beverage":           "MNST",
    "ge vernova":                 "GEV",
    "cummins inc":                "CMI",
    "carvana":                    "CVNA",
    "twilio":                     "TWLO",
    "fabrinet":                   "FN",
    "sandisk":                    "SNDK",
    "performance food group":     "PFGC",
    "columbia banking":           "COLB",
    "phreesia":                   "PHR",
    "federal signal":             "FSS",
    "first watch":                "FWRG",
    "netease":                    "NTES",
    "sea limited":                "SE",
    "schneider national":         "SNDR",
    "ally financial":             "ALLY",
    "la-z-boy":                   "LZB",
    "dollar general":             "DG",
    # Additional mid-caps common in PTR filings
    "state street":               "STT",
    "devon energy":               "DVN",
    "morningstar":                "MORN",
    "service corporation":        "SCI",
    "atlas energy solutions":     "AESI",
    "packaging corporation":      "PKG",
    "c.h. robinson":              "CHRW",
    "fortune brands":             "FBIN",
    "tenet healthcare":           "THC",
    "taiwan semiconductor":       "TSM",
    "american water works":       "AWK",
    "ferguson enterprises":       "FERG",
    "kinder morgan":              "KMI",
    "s&p global":                 "SPGI",
    "paychex":                    "PAYX",
    "intel corporation":          "INTC",
    "state street corporation":   "STT",
    "m3 inc":                     "MTHRY",
    "sysmex":                     "SSMXY",
    "hdfc bank":                  "HDB",
    "infineon":                   "IFNNY",
    "nestle":                     "NSRGY",
    "intapp":                     "INTA",
    "ee energy":                  "XNGSY",
    "cbre group":                 "CBRE",
    "crowdstrike":                "CRWD",
    "datadog":                    "DDOG",
    "fortinet":                   "FTNT",
    "zscaler":                    "ZS",
    "okta":                       "OKTA",
    "veeva systems":              "VEEV",
    "workday":                    "WDAY",
    "hubspot":                    "HUBS",
    "trade desk":                 "TTD",
    "mongodb":                    "MDB",
    "cloudflare":                 "NET",
    "digital realty":             "DLR",
    "equinix":                    "EQIX",
    "prologis":                   "PLD",
    "realty income":              "O",
    "boston properties":          "BXP",
    "alexandria real estate":     "ARE",
    "healthpeak":                 "DOC",
    "welltower":                  "WELL",
    "ventas":                     "VTR",
    "iron mountain":              "IRM",
    "extra space":                "EXR",
    "camden property":            "CPT",
    "essex property":             "ESS",
    "mid-america apartment":      "MAA",
    "sun communities":            "SUI",
    "avalonbay":                  "AVB",
    "equity residential":         "EQR",
    "invitation homes":           "INVH",
    "american tower":             "AMT",
    "sbac communications":        "SBAC",
    "uniti group":                "UNIT",
    "antero resources":           "AR",
    "coterra energy":             "CTRA",
    "pioneer natural":            "PXD",
    "diamondback energy":         "FANG",
    "marathon oil":               "MRO",
    "hess corporation":           "HES",
    "occidental petroleum":       "OXY",
    "halliburton":                "HAL",
    "schlumberger":               "SLB",
    "baker hughes":               "BKR",
    "williams companies":         "WMB",
    "oneok":                      "OKE",
    "targa resources":            "TRGP",
    "energy transfer":            "ET",
    "enterprise products":        "EPD",
    "mplx":                       "MPLX",
    "marathon petroleum":         "MPC",
    "valero energy":              "VLO",
    "phillips 66":                "PSX",
    "conocophillips":             "COP",
    "southwest airlines":         "LUV",
    "delta air lines":            "DAL",
    "american airlines":          "AAL",
    "united airlines":            "UAL",
    "alaska air":                 "ALK",
    "carnival corporation":       "CCL",
    "royal caribbean":            "RCL",
    "norwegian cruise":           "NCLH",
    "hilton worldwide":           "HLT",
    "marriott":                   "MAR",
    "hyatt hotels":               "H",
    "mgm resorts":                "MGM",
    "las vegas sands":            "LVS",
    "wynn resorts":               "WYNN",
    "caesars entertainment":      "CZR",
    "draftkings":                 "DKNG",
    "penn entertainment":         "PENN",
    "darden":                     "DRI",
    "yum! brands":                "YUM",
    "mcdonald":                   "MCD",
    "starbucks":                  "SBUX",
    "chipotle":                   "CMG",
    "domino":                     "DPZ",
    "restaurant brands":          "QSR",
    "jack in the box":            "JACK",
    "wingstop":                   "WING",
    "dutch bros":                 "BROS",
    "shake shack":                "SHAK",
    "sweetgreen":                 "SG",
    "uipath":                     "PATH",
    "gitlab":                     "GTLB",
    "hashicorp":                  "HCP",
    "confluent":                  "CFLT",
    "dynatrace":                  "DT",
    "elastic":                    "ESTC",
    "solarwinds":                 "SWI",
    "ping identity":              "PING",
    "sailpoint":                  "SAIL",
    "cylance":                    "BB",
    "varonis":                    "VRNS",
    "rapid7":                     "RPD",
    "tenable":                    "TENB",
    "qualys":                     "QLYS",
    "veracode":                   "VERA",
    "cyberark":                   "CYBR",
    "sentinelone":                "S",
    "exlservice":                 "EXLS",
    "kforce":                     "KFRC",
    "maximus":                    "MMS",
    "booz allen":                 "BAH",
    "leidos":                     "LDOS",
    "saic":                       "SAIC",
    "perspecta":                  "PRSP",
    "caci international":         "CACI",
    "mantech":                    "MANT",
    "amentum":                    "AMTM",
    "parsons corporation":        "PSN",
    "kratos defense":             "KTOS",
    "transdigm":                  "TDG",
    "l3harris":                   "LHX",
    "northrop grumman":           "NOC",
    "general dynamics":           "GD",
    "textron":                    "TXT",
    "huntington ingalls":         "HII",
    "curtiss-wright":             "CW",
    "heico":                      "HEI",
    "moog inc":                   "MOG-A",
    "woodward":                   "WWD",
    "kaman":                      "KAMN",
    "triumph group":              "TGI",
    "spirit aerosystems":         "SPR",
    "ducommun":                   "DCO",
    # From live PTR filings (screenshot additions)
    "sonida senior living":       "SNR",
    "exact sciences":             "EXAS",
    "sinclair corporation":       "SBGI",
    "sinclair broadcast":         "SBGI",
    "barclays bank":              "BCS",
    "barclays plc":               "BCS",
    "middeby corporation":        "MIDD",
    "the middeby":                "MIDD",
    "shopify":                    "SHOP",
    "siriusxm":                   "SIRI",
    "sirius xm":                  "SIRI",
    "chord energy":               "CHRD",
    "otc markets group":          "OTCM",
    "smartstop self storage":     "",    # non-traded REIT — no exchange ticker
    "connectivity plc":           "",    # private / foreign unlisted
}


# Cache for dynamically resolved company→ticker lookups (persists for Flask lifetime)
_ticker_dyn_cache: dict[str, str] = {}
_ticker_dyn_lock  = threading.Lock()

# Asset type tags that mean the asset is NOT a public equity
_NON_EQUITY_TAGS = {"OI", "RE", "RP", "FE", "HN", "DC", "BD", "MF"}

def _resolve_ticker_from_name(asset_lower: str) -> str:
    """Return a ticker symbol by matching company name keywords (static table)."""
    for name, sym in sorted(_COMPANY_TICKER.items(), key=lambda x: -len(x[0])):
        if name in asset_lower:
            return sym
    return ""


def _resolve_ticker_dynamic(raw_asset: str) -> str:
    """
    Dynamically resolve a company name to a ticker using yfinance Search.
    Results are cached for the lifetime of the Flask process.
    Returns "" if no confident match found.
    """
    # Strip common suffixes that confuse search
    clean = re.sub(
        r'\s*[\(\[].*?[\)\]]\s*',  # remove (TICKER) and [TAG]
        '', raw_asset, flags=re.S
    ).strip()
    clean = re.sub(
        r'\b(common stock|common share|ordinary share|class [ab]|inc\.?|corp\.?|'
        r'corporation|ltd\.?|plc|llc|lp|na|n\.a\.)\b',
        '', clean, flags=re.I
    ).strip()
    clean = re.sub(r'\s*[-–,]\s*$', '', clean).strip()  # trailing punctuation
    clean = re.sub(r'\s+', ' ', clean)

    if not clean or len(clean) < 4:
        return ""

    cache_key = clean.lower()
    with _ticker_dyn_lock:
        if cache_key in _ticker_dyn_cache:
            return _ticker_dyn_cache[cache_key]

    try:
        results = yf.Search(clean, max_results=5, enable_fuzzy_query=True).quotes
        # The first significant word of the company name must appear in the result
        first_word = clean.split()[0].lower() if clean.split() else ""
        for r in results:
            qt  = r.get("quoteType", "")
            ex  = r.get("exchange", "")
            sym = r.get("symbol", "")
            name_match = (r.get("longname", "") or r.get("shortname", "")).lower()
            if (qt == "EQUITY"
                    and ex in ("NMS", "NYQ", "NGM", "NCM", "PCX", "ASE", "BTS")
                    and sym and "." not in sym and len(sym) <= 5
                    and first_word and first_word in name_match):
                with _ticker_dyn_lock:
                    _ticker_dyn_cache[cache_key] = sym
                return sym
    except Exception:
        pass

    with _ticker_dyn_lock:
        _ticker_dyn_cache[cache_key] = ""   # negative cache — don't retry
    return ""


def _parse_ptr_pdf(pdf_bytes: bytes, member_name: str, chamber: str,
                   state_dist: str, filing_date: str) -> list[dict]:
    """Extract individual stock transactions from a House PTR PDF."""
    trades = []
    try:
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                lines = text.splitlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    # Rows look like:
                    # "SP  Tesla, Inc. (TSLA)  P  01/15/2026  01/20/2026  $15,001 - $50,000"
                    # Pattern: starts with owner code, then asset, then type, then dates, then amount
                    m = re.match(
                        r'^(SP|JT|DC|[A-Z]{1,3})\s+(.+?)\s+'
                        r'(P|S|S \(partial\)|E|W)\s+'
                        r'(\d{2}/\d{2}/\d{4})\s+'
                        r'(\d{2}/\d{2}/\d{4})\s+'
                        r'(\$[\d,]+(?:\s*-\s*\$[\d,]+)?)',
                        line
                    )
                    if m:
                        owner_code = m.group(1).strip()
                        asset      = m.group(2).strip()
                        tx_type    = m.group(3).strip()
                        tx_date    = m.group(4).strip()
                        notif_date = m.group(5).strip()
                        amount     = m.group(6).strip()

                        # Normalise type
                        if tx_type == "P":
                            tx_type_label = "Purchase"
                        elif tx_type.startswith("S"):
                            tx_type_label = "Sale"
                        elif tx_type == "E":
                            tx_type_label = "Exchange"
                        else:
                            tx_type_label = tx_type

                        # Extract ticker from parentheses e.g. "Tesla, Inc. (TSLA) [ST]"
                        ticker_match = re.search(r'\(([A-Z]{1,5})\)', asset)
                        # Also check the next line — some PDFs wrap the ticker there
                        if not ticker_match and i + 1 < len(lines):
                            ticker_match = re.search(r'^\(([A-Z]{1,5})\)', lines[i + 1].strip())
                        ticker = ticker_match.group(1) if ticker_match else ""
                        asset_name = re.sub(r'\s*\([^)]+\)\s*(?:\[[A-Z]+\])?', '', asset).strip()

                        # Parse transaction date
                        try:
                            from datetime import datetime as _dt
                            tx_dt = _dt.strptime(tx_date, "%m/%d/%Y")
                            tx_date_iso = tx_dt.strftime("%Y-%m-%d")
                        except Exception:
                            tx_date_iso = tx_date

                        # ── Classify asset type ───────────────────────────
                        asset_lower = asset.lower()
                        # Check original asset string for asset type tag [ST], [OP], etc.
                        asset_type_tag = ""
                        tag_match = re.search(r'\[([A-Z]{2,4})\]', asset)
                        if tag_match:
                            asset_type_tag = tag_match.group(1)

                        if asset_type_tag in ("OP", "OC", "OP-C", "OP-P"):
                            asset_type = "option"
                        elif asset_type_tag in ("ST",):
                            asset_type = "stock"
                        elif asset_type_tag in ("FU", "FE", "MF"):
                            asset_type = "fund"
                        elif asset_type_tag in ("CB", "BN", "DB", "BD"):
                            asset_type = "bond"
                        elif asset_type_tag in _NON_EQUITY_TAGS:
                            # OI = Other Investment (private/OTC), RE = Real Estate, etc.
                            asset_type = "other"
                        elif any(kw in asset_lower for kw in (
                            " call", " put", "option", "call option", "put option",
                            "strike", "expir", "covered call", "callable"
                        )) and not any(bad in asset_lower for bad in (
                            "treasury", "municipal", "california st", "note due",
                        )):
                            asset_type = "option"
                        elif any(kw in asset_lower for kw in (
                            "treasury", "t-bill", "t-note", "municipal", "note due",
                            "reit property", "real estate", "etf", "spac"
                        )):
                            asset_type = "bond"
                        elif any(kw in asset_lower for kw in (
                            "fund", "trust", "etf", "index"
                        )):
                            asset_type = "fund"
                        elif any(kw in asset_lower for kw in (
                            "common stock", "common share", "- common",
                            "ordinary shares", "class a", "class b",
                            "inc.", "corp.", "corporation", "ltd.", "plc",
                        )):
                            asset_type = "stock"
                            if not ticker:
                                # Try static table first (fast), then yfinance (accurate)
                                ticker = (_resolve_ticker_from_name(asset_lower)
                                          or _resolve_ticker_dynamic(asset))
                        elif ticker:
                            asset_type = "stock"
                        else:
                            # Last-chance: try both resolvers
                            ticker = (_resolve_ticker_from_name(asset_lower)
                                      or _resolve_ticker_dynamic(asset))
                            asset_type = "stock" if ticker else "other"

                        # ── Suspicious flag heuristics ────────────────────
                        # Flag if: options trade + large amount + has a ticker
                        amount_mid = 0
                        try:
                            nums = re.findall(r'[\d,]+', (amount or "").replace(",", ""))
                            if nums:
                                amount_mid = int(nums[-1]) if len(nums) == 1 else (int(nums[0]) + int(nums[-1])) // 2
                        except Exception:
                            pass
                        suspicious = bool(
                            asset_type == "option" and amount_mid >= 50_000 and ticker
                        )

                        # tradeable = can be bought/sold on a standard brokerage
                        # (has a ticker AND is a stock/option/ETF — not a bond,
                        # non-traded REIT, private equity, or unlisted foreign share)
                        tradeable = bool(
                            ticker
                            and asset_type in ("stock", "option", "fund")
                            and asset_type_tag not in _NON_EQUITY_TAGS
                        )

                        trades.append({
                            "member":      member_name.strip(),
                            "chamber":     chamber,
                            "state_dist":  state_dist,
                            "party":       _PARTY_LOOKUP.get(state_dist, "?"),
                            "owner":       owner_code,
                            "asset":       asset_name,
                            "ticker":      ticker,
                            "type":        tx_type_label,
                            "asset_type":  asset_type,
                            "tradeable":   tradeable,
                            "suspicious":  suspicious,
                            "amount_mid":  amount_mid,
                            "tx_date":     tx_date_iso,
                            "notif_date":  notif_date,
                            "amount":      amount,
                            "filing_date": filing_date,
                        })
                    i += 1
    except Exception as e:
        print(f"⚠️  PDF parse error for {member_name}: {e}")
    return trades


def _fetch_house_trades(year: int) -> list[dict]:
    """Fetch and parse all House PTR disclosures for a given year."""
    # Step 1: get index ZIP
    idx_url = POL_HOUSE_INDEX.format(year=year)
    r = _http.get(idx_url, headers=POL_HEADERS, timeout=20)
    r.raise_for_status()
    z = zipfile.ZipFile(_io.BytesIO(r.content))

    import xml.etree.ElementTree as _ET
    xml_names = [n for n in z.namelist() if n.endswith(".xml")]
    if not xml_names:
        return []
    root = _ET.fromstring(z.read(xml_names[0]))

    # Step 2: collect all P-type (Periodic Transaction Report) entries
    ptr_entries = []
    for member in root:
        if member.findtext("FilingType") == "P":
            ptr_entries.append({
                "doc_id":    member.findtext("DocID", ""),
                "last":      member.findtext("Last", ""),
                "first":     member.findtext("First", ""),
                "state_dst": member.findtext("StateDst", ""),
                "filed":     member.findtext("FilingDate", ""),
            })

    # Seed live poller's seen-ID set so it only fetches genuinely NEW filings
    with _ptr_lock:
        for e in ptr_entries:
            if e["doc_id"]:
                _ptr_seen_ids.add(e["doc_id"])

    print(f"🏛️  House PTR entries for {year}: {len(ptr_entries)}")

    # Step 3: fetch each PTR PDF in parallel and parse
    all_trades: list[dict] = []

    def _process(entry: dict) -> list[dict]:
        url = POL_HOUSE_PDF.format(year=year, doc_id=entry["doc_id"])
        try:
            rp = _http.get(url, headers=POL_HEADERS, timeout=20)
            if rp.status_code != 200:
                return []
            name = f"{entry['first']} {entry['last']}"
            return _parse_ptr_pdf(rp.content, name, "House",
                                  entry["state_dst"], entry["filed"])
        except Exception as e:
            print(f"⚠️  PTR fetch error {entry['doc_id']}: {e}")
            return []

    # Limit parallelism to avoid rate-limiting; process in batches
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_process, e): e for e in ptr_entries}
        for fut in as_completed(futures):
            all_trades.extend(fut.result())

    return all_trades


# ════════════════════════════════════════════════════════════════
# PTR LIVE POLLER — background thread, polls every 5 min
# House Clerk shows new filings instantly on the live search page
# while the bulk ZIP is updated only in batches (every few hours).
# ════════════════════════════════════════════════════════════════

POL_LIVE_INTERVAL   = 300  # seconds between polls (5 min)
POL_LIVE_INTERVAL = 300   # seconds between polls (5 min)

_ptr_seen_ids:    set[str]   = set()   # DocIDs already parsed (from ZIP + live)
_ptr_live_trades: list[dict] = []      # brand-new trades not yet in ZIP
_ptr_last_poll:   float      = 0.0
_ptr_poll_status: str        = "starting"
_ptr_new_count:   int        = 0       # new filings found this session
_ptr_lock = threading.Lock()


def _ptr_poll_once() -> int:
    """
    Re-download the House Clerk ZIP XML index and compare DocIDs against
    _ptr_seen_ids.  For any genuinely new PTR filing, fetch its PDF and
    parse trades immediately so they appear in /api/politician-trades.
    Returns the number of new filings discovered.

    Why ZIP, not search page:
      The search page uses DataTables with server-side JS rendering —
      the HTML tbody is always empty on a plain HTTP POST.  The ZIP XML
      is a static file updated in batches (typically every 10-30 min)
      and is entirely parseable without a browser.
    """
    global _ptr_live_trades

    year    = datetime.now().year
    idx_url = POL_HOUSE_INDEX.format(year=year)

    r = _http.get(idx_url, headers=POL_HEADERS, timeout=30)
    r.raise_for_status()

    import xml.etree.ElementTree as _ET
    import zipfile as _zf
    z    = _zf.ZipFile(_io.BytesIO(r.content))
    xml_names = [n for n in z.namelist() if n.endswith(".xml")]
    if not xml_names:
        return 0
    root = _ET.fromstring(z.read(xml_names[0]))

    # Collect all current PTR entries from the index
    all_entries: list[dict] = []
    for member in root:
        if member.findtext("FilingType") == "P":
            doc_id = member.findtext("DocID", "")
            if doc_id:
                all_entries.append({
                    "doc_id":    doc_id,
                    "last":      member.findtext("Last", ""),
                    "first":     member.findtext("First", ""),
                    "state_dst": member.findtext("StateDst", ""),
                    "filed":     member.findtext("FilingDate", ""),
                })

    # Only process DocIDs we haven't seen yet
    new_entries = [e for e in all_entries if e["doc_id"] not in _ptr_seen_ids]
    if not new_entries:
        return 0

    print(f"🔴 PTR live poller: {len(new_entries)} new filing(s) detected in ZIP")

    new_trades: list[dict] = []
    for entry in new_entries:
        doc_id  = entry["doc_id"]
        pdf_url = POL_HOUSE_PDF.format(year=year, doc_id=doc_id)
        name    = f"{entry['first']} {entry['last']}".strip()
        try:
            rp = _http.get(pdf_url, headers=POL_HEADERS, timeout=20)
            if rp.status_code == 200:
                trades = _parse_ptr_pdf(
                    rp.content, name or "Unknown", "House",
                    entry["state_dst"], entry["filed"],
                )
                for t in trades:
                    t["live"]   = True   # flag for UI highlight
                    t["doc_id"] = doc_id
                new_trades.extend(trades)
                print(f"  📄 {name} (DocID {doc_id}) — {len(trades)} trade(s)")
        except Exception as exc:
            print(f"  ⚠️  PDF fetch error {doc_id}: {exc}")

        with _ptr_lock:
            _ptr_seen_ids.add(doc_id)

    if new_trades:
        with _ptr_lock:
            _ptr_live_trades = new_trades + _ptr_live_trades

        # Merge into the in-memory cache so /api/politician-trades
        # returns the new trades immediately (no re-fetch needed)
        cache_key = f"pol_trades:{year}"
        if cache_key in _cache:
            ts, cached = _cache[cache_key]
            seen_doc_ids = {x["doc_id"] for x in new_trades}
            merged = new_trades + [t for t in cached
                                   if t.get("doc_id") not in seen_doc_ids]
            merged.sort(key=lambda x: x.get("tx_date", ""), reverse=True)
            _cache[cache_key] = (ts, merged)

    return len(new_entries)


def _ptr_live_poller_thread() -> None:
    """Daemon thread: poll for new PTR filings every POL_LIVE_INTERVAL seconds."""
    global _ptr_last_poll, _ptr_poll_status, _ptr_new_count

    print("🔴 PTR live poller started (interval: 5 min)")
    time.sleep(15)   # let Flask fully boot before first poll

    while True:
        try:
            _ptr_poll_status = "polling"
            found = _ptr_poll_once()
            _ptr_new_count  += found
            _ptr_last_poll   = time.time()
            _ptr_poll_status = "ok"
            if found:
                print(f"🔴 Live poller: {found} new filing(s) added to trades list")
        except Exception as exc:
            _ptr_last_poll   = time.time()
            _ptr_poll_status = "error"
            print(f"⚠️  PTR live poller: {exc}")

        time.sleep(POL_LIVE_INTERVAL)


def _fetch_politician_trades(year: int | None = None, force: bool = False) -> list[dict]:
    """Return all STOCK Act disclosures for the given year (default: current year)."""
    if year is None:
        year = datetime.now().year

    cache_key = f"pol_trades:{year}"
    if not force and cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < POL_CACHE_TTL:
            print(f"📦 Pol-trades cache hit: {year}")
            return cached

    trades = _fetch_house_trades(year)

    # Sort newest first
    trades.sort(key=lambda x: x.get("tx_date", ""), reverse=True)
    _cache[cache_key] = (time.time(), trades)
    print(f"✅ Politician trades fetched: {len(trades)} trades for {year}")
    return trades


@app.route("/api/politician-trades")
def get_politician_trades():
    """Return STOCK Act disclosure trades.

    Query params:
      year       = 2026 (default: current year)
      chamber    = house | senate | all  (default: all)
      type       = purchase | sale | all  (default: all)
      asset_type = stock | option | all  (default: all)
      ticker     = NVDA  (optional filter)
      q          = free-text search on member name or asset
      force      = 1  (bypass cache and re-fetch index ZIP)
    """
    year       = int(request.args.get("year", datetime.now().year))
    chamber    = request.args.get("chamber", "all").lower()
    tx_type    = request.args.get("type", "all").lower()
    asset_type = request.args.get("asset_type", "all").lower()
    ticker     = request.args.get("ticker", "").upper()
    q          = request.args.get("q", "").lower()
    force      = request.args.get("force", "0") == "1"

    trades = _fetch_politician_trades(year, force=force)
    fetched_at = _cache.get(f"pol_trades:{year}", (0, []))[0]

    # Apply filters
    if chamber != "all":
        trades = [t for t in trades if t.get("chamber", "").lower() == chamber]
    if tx_type != "all":
        trades = [t for t in trades if tx_type in t.get("type", "").lower()]
    if asset_type != "all":
        trades = [t for t in trades if t.get("asset_type", "other") == asset_type]
    if ticker:
        trades = [t for t in trades if t.get("ticker", "").upper() == ticker]
    if q:
        trades = [t for t in trades
                  if q in t.get("member", "").lower() or q in t.get("asset", "").lower()
                  or q in t.get("ticker", "").lower()]

    # Collect suspicious trades (options + large + ticker known)
    suspicious = [t for t in trades if t.get("suspicious")]
    suspicious.sort(key=lambda x: x.get("amount_mid", 0), reverse=True)

    import datetime as _dt_mod
    return jsonify({
        "year":       year,
        "count":      len(trades),
        "trades":     trades,
        "alerts":     suspicious[:10],
        "fetched_at": _dt_mod.datetime.fromtimestamp(fetched_at).strftime("%Y-%m-%d %H:%M:%S") if fetched_at else None,
        "source_url": f"https://disclosures-clerk.house.gov/FinancialDisclosure",
    })


@app.route("/api/ptr-live-status")
def ptr_live_status():
    """Return status of the background PTR live poller + any brand-new trades."""
    import datetime as _dt_mod
    last_str = (
        _dt_mod.datetime.fromtimestamp(_ptr_last_poll).strftime("%H:%M:%S")
        if _ptr_last_poll else None
    )
    with _ptr_lock:
        live = list(_ptr_live_trades[:30])   # up to 30 most recent live trades

    suspicious_live = [t for t in live if t.get("suspicious")]
    return jsonify({
        "status":          _ptr_poll_status,
        "last_poll":       last_str,
        "new_count":       _ptr_new_count,
        "live_trades":     live,
        "suspicious_live": suspicious_live,
        "interval_sec":    POL_LIVE_INTERVAL,
    })


@app.route("/api/clear-pol-cache", methods=["POST"])
def clear_pol_cache():
    """Clear the politician-trades in-memory cache so next request re-parses all PDFs."""
    year = datetime.now().year
    removed = []
    for y in [year, year - 1, year + 1]:
        key = f"pol_trades:{y}"
        if key in _cache:
            del _cache[key]
            removed.append(y)
    # Also clear the ticker dynamic cache so improved resolvers run fresh
    with _ticker_dyn_lock:
        _ticker_dyn_cache.clear()
    return jsonify({"cleared": removed, "ok": True})


# ═══════════════════════════════════════════════════════════════
# CORPORATE EQUITY HOLDINGS
# Curated database of strategic investments by top tech companies.
# Sources: SEC EDGAR SC 13D/13G, press releases, 10-K disclosures.
# Enriched with live prices via yfinance.
# ═══════════════════════════════════════════════════════════════

CORP_INVESTORS = {
    "nvidia":    {"name": "NVIDIA Corp",      "ticker": "NVDA",  "color": "#76b900"},
    "alphabet":  {"name": "Alphabet / Google","ticker": "GOOGL", "color": "#4285f4"},
    "microsoft": {"name": "Microsoft Corp",   "ticker": "MSFT",  "color": "#00a4ef"},
    "amazon":    {"name": "Amazon.com",       "ticker": "AMZN",  "color": "#ff9900"},
    "softbank":  {"name": "SoftBank Group",   "ticker": "SFTBY", "color": "#cc0000"},
    "meta":      {"name": "Meta Platforms",   "ticker": "META",  "color": "#1877f2"},
    "intel":     {"name": "Intel Corp",       "ticker": "INTC",  "color": "#0071c5"},
    "qualcomm":  {"name": "Qualcomm Inc",     "ticker": "QCOM",  "color": "#3253dc"},
    "apple":     {"name": "Apple Inc",        "ticker": "AAPL",  "color": "#555555"},
    "samsung":   {"name": "Samsung Electronics","ticker":"SSNLF","color": "#1428a0"},
}

# Each holding has a list of "events" (acquisitions, IPOs, sales, increases, reductions).
# Dates are ISO strings. action ∈ {Acquired, IPO, Increased, Reduced, Sold, Exited}.
# status ∈ {active, exited, partial}
CORP_HOLDINGS_SEED: dict[str, list[dict]] = {
    "nvidia": [
        {
            "target": "CoreWeave", "ticker": "CRWV", "status": "active",
            "pct": 5.6, "total_invested_usd": 100_000_000,
            "events": [
                {"date": "2023-08-01", "action": "Acquired", "note": "Strategic investment in AI cloud provider; received equity as part of GPU supply deal"},
                {"date": "2025-03-28", "action": "IPO",      "note": "CoreWeave IPO on Nasdaq; NVIDIA held ~5.6% stake"},
            ],
        },
        {
            "target": "Nebius Group", "ticker": "NBIS", "status": "active",
            "total_invested_usd": 700_000_000,
            "events": [
                {"date": "2024-10-22", "action": "Acquired", "note": "$700M strategic investment in AI infrastructure company (former Yandex NV)"},
            ],
        },
        {
            "target": "Serve Robotics", "ticker": "SERV", "status": "active",
            "pct": 10.1,
            "events": [
                {"date": "2024-07-18", "action": "Acquired", "pct": 10.1, "note": "SC 13G filed with SEC; 10.1% stake in autonomous delivery robot company"},
            ],
        },
        {
            "target": "Recursion Pharmaceuticals", "ticker": "RXRX", "status": "active",
            "total_invested_usd": 50_000_000,
            "events": [
                {"date": "2023-07-18", "action": "Acquired", "note": "$50M strategic investment; AI-powered drug discovery partnership"},
            ],
        },
        {
            "target": "Arm Holdings", "ticker": "ARM", "status": "active",
            "pct": 1.7,
            "events": [
                {"date": "2020-09-13", "action": "Acquired", "note": "NVIDIA announced $40B acquisition bid for ARM (shares already owned)"},
                {"date": "2022-02-07", "action": "Reduced",  "note": "Acquisition abandoned; NVIDIA retained ~1.7% minority stake"},
                {"date": "2023-09-14", "action": "IPO",      "note": "ARM IPO on Nasdaq; NVIDIA stake unlocked"},
            ],
        },
        {
            "target": "Tempus AI", "ticker": "TEM", "status": "active",
            "events": [
                {"date": "2024-07-01", "action": "Acquired", "note": "Strategic investment in AI-powered precision medicine platform"},
            ],
        },
        {
            "target": "SoundHound AI", "ticker": "SOUN", "status": "exited",
            "events": [
                {"date": "2023-01-01", "action": "Acquired", "note": "Strategic stake in voice AI company"},
                {"date": "2024-02-29", "action": "Sold",     "note": "Exited full position; disclosed in Q4 2023 13F filing"},
            ],
        },
        {
            "target": "Nano-X Imaging", "ticker": "NNOX", "status": "active",
            "pct": 11.0,
            "events": [
                {"date": "2020-05-20", "action": "Acquired", "pct": 11.0, "note": "Strategic investment in medical imaging company using digital X-ray tech"},
            ],
        },
    ],
    "alphabet": [
        {
            "target": "Recursion Pharmaceuticals", "ticker": "RXRX", "status": "active",
            "total_invested_usd": 150_000_000,
            "events": [
                {"date": "2023-09-26", "action": "Acquired", "note": "$150M strategic investment via Isomorphic Labs AI drug discovery collaboration"},
            ],
        },
        {
            "target": "Rivian Automotive", "ticker": "RIVN", "status": "active",
            "total_invested_usd": 500_000_000,
            "events": [
                {"date": "2019-02-15", "action": "Acquired", "note": "$500M investment in EV startup"},
                {"date": "2021-11-10", "action": "IPO",      "note": "Rivian IPO on Nasdaq; Alphabet held ~5% stake"},
                {"date": "2023-06-01", "action": "Reduced",  "note": "Gradually selling shares through 2023"},
            ],
        },
        {
            "target": "Duolingo", "ticker": "DUOL", "status": "active",
            "events": [
                {"date": "2020-11-18", "action": "Acquired", "note": "Investment in language-learning platform (Series G)"},
                {"date": "2021-07-28", "action": "IPO",      "note": "Duolingo IPO on Nasdaq"},
            ],
        },
        {
            "target": "Waymo (parent stake)", "ticker": None, "status": "active",
            "total_invested_usd": 5_000_000_000,
            "events": [
                {"date": "2016-01-01", "action": "Acquired", "note": "Spun out from Google X; Alphabet retained majority"},
                {"date": "2024-10-25", "action": "Increased","note": "Waymo raised $5.6B; Alphabet participated"},
            ],
        },
    ],
    "microsoft": [
        {
            "target": "London Stock Exchange Group", "ticker": "LSEG.L", "status": "active",
            "pct": 4.0, "total_invested_usd": 2_800_000_000,
            "events": [
                {"date": "2022-01-27", "action": "Acquired", "pct": 4.0, "note": "$2.8B for 4% stake; Azure 10-year cloud data partnership"},
                {"date": "2023-12-01", "action": "Increased","note": "Stake approached 5% as LSEG shares rose"},
            ],
        },
        {
            "target": "Rubrik", "ticker": "RBRK", "status": "active",
            "events": [
                {"date": "2021-08-17", "action": "Acquired", "note": "Strategic investment in cloud data security & management"},
                {"date": "2024-04-25", "action": "IPO",      "note": "Rubrik IPO on NYSE"},
            ],
        },
        {
            "target": "OpenAI", "ticker": None, "status": "active",
            "total_invested_usd": 13_000_000_000,
            "events": [
                {"date": "2019-07-22", "action": "Acquired", "note": "$1B initial investment in OpenAI"},
                {"date": "2021-07-01", "action": "Increased","note": "$2B follow-on investment"},
                {"date": "2023-01-23", "action": "Increased","note": "$10B multi-year investment; extended Azure partnership"},
            ],
        },
    ],
    "amazon": [
        {
            "target": "Rivian Automotive", "ticker": "RIVN", "status": "active",
            "pct": 16.0, "total_invested_usd": 1_300_000_000,
            "events": [
                {"date": "2019-02-15", "action": "Acquired", "note": "$700M investment; 100,000 electric delivery van order"},
                {"date": "2020-07-01", "action": "Increased","note": "Additional $1.3B investment; order expanded"},
                {"date": "2021-11-10", "action": "IPO",      "note": "Rivian IPO; Amazon held ~20% stake"},
                {"date": "2023-06-01", "action": "Reduced",  "pct": 16.0, "note": "Stake reduced as Amazon sold shares on market"},
            ],
        },
        {
            "target": "Anthropic", "ticker": None, "status": "active",
            "total_invested_usd": 4_000_000_000,
            "events": [
                {"date": "2023-09-25", "action": "Acquired", "note": "$1.25B initial investment; AWS becomes primary cloud provider"},
                {"date": "2024-03-27", "action": "Increased","note": "Total commitment reached $4B as final tranche deployed"},
            ],
        },
        {
            "target": "Grubhub", "ticker": None, "status": "exited",
            "events": [
                {"date": "2022-06-02", "action": "Acquired", "note": "Partnership deal with up to 2% equity in Grubhub"},
                {"date": "2024-01-17", "action": "Sold",     "note": "Grubhub sold to Wonder Group; Amazon stake dissolved"},
            ],
        },
    ],
    "softbank": [
        {
            "target": "Arm Holdings", "ticker": "ARM", "status": "active",
            "pct": 90.0, "total_invested_usd": 32_000_000_000,
            "events": [
                {"date": "2016-07-18", "action": "Acquired", "pct": 100.0, "note": "$32B full acquisition of ARM Holdings"},
                {"date": "2017-08-01", "action": "Reduced",  "pct": 75.0,  "note": "Sold 25% to Vision Fund"},
                {"date": "2023-09-14", "action": "IPO",      "pct": 90.0,  "note": "ARM IPO; SoftBank retained ~90% post-IPO"},
                {"date": "2024-06-01", "action": "Reduced",  "pct": 88.1,  "note": "Secondary offering; still dominant shareholder"},
            ],
        },
        {
            "target": "T-Mobile US", "ticker": "TMUS", "status": "active",
            "pct": 9.4, "total_invested_usd": 38_000_000_000,
            "events": [
                {"date": "2013-07-10", "action": "Acquired", "note": "Acquired Sprint Corp for $21.6B; Sprint held T-Mobile shares post-merger"},
                {"date": "2020-04-01", "action": "IPO",      "note": "Sprint merged into T-Mobile; SoftBank retained ~24% stake"},
                {"date": "2023-06-01", "action": "Reduced",  "pct": 9.4,  "note": "Sold large block to fund AI investments"},
            ],
        },
        {
            "target": "Uber Technologies", "ticker": "UBER", "status": "partial",
            "total_invested_usd": 9_300_000_000,
            "events": [
                {"date": "2017-01-17", "action": "Acquired", "note": "$9.3B — largest startup investment in history at the time"},
                {"date": "2019-05-10", "action": "IPO",      "note": "Uber IPO on NYSE; SoftBank held ~16%"},
                {"date": "2020-02-01", "action": "Reduced",  "note": "Began selling shares to fund losses"},
                {"date": "2023-08-01", "action": "Reduced",  "note": "Sold remaining large block; retained small residual stake"},
            ],
        },
        {
            "target": "DoorDash", "ticker": "DASH", "status": "active",
            "events": [
                {"date": "2019-05-01", "action": "Acquired", "note": "Vision Fund I investment in food delivery platform"},
                {"date": "2020-12-09", "action": "IPO",      "note": "DoorDash IPO; SoftBank retained stake"},
            ],
        },
        {
            "target": "Alibaba Group", "ticker": "BABA", "status": "exited",
            "total_invested_usd": 20_000_000,
            "events": [
                {"date": "2000-01-20", "action": "Acquired", "note": "$20M seed investment; stake grew to >$100B"},
                {"date": "2023-06-01", "action": "Reduced",  "note": "Sold large block to fund ARM/AI strategy"},
                {"date": "2024-06-01", "action": "Sold",     "note": "Sold remaining voting stake; exited Alibaba board"},
            ],
        },
    ],
    "meta": [
        {
            "target": "EssilorLuxottica (Ray-Ban)", "ticker": "EL.PA", "status": "active",
            "total_invested_usd": 0,
            "events": [
                {"date": "2021-09-09", "action": "Acquired", "note": "Ray-Ban Stories smart glasses partnership; equity component undisclosed"},
            ],
        },
        {
            "target": "Jio Platforms", "ticker": None, "status": "active",
            "pct": 9.9, "total_invested_usd": 5_700_000_000,
            "events": [
                {"date": "2020-07-15", "action": "Acquired", "pct": 9.9, "note": "$5.7B for 9.9% stake in Reliance Industries' digital subsidiary"},
            ],
        },
    ],
    "intel": [
        {
            "target": "Astera Labs", "ticker": "ALAB", "status": "active",
            "events": [
                {"date": "2023-02-01", "action": "Acquired", "note": "Intel Capital strategic investment in semiconductor connectivity startup"},
                {"date": "2024-03-20", "action": "IPO",      "note": "Astera Labs IPO; Intel retained stake"},
            ],
        },
        {
            "target": "Mobileye Global", "ticker": "MBLY", "status": "active",
            "pct": 88.0, "total_invested_usd": 15_300_000_000,
            "events": [
                {"date": "2017-08-13", "action": "Acquired", "pct": 100.0, "note": "$15.3B acquisition of Mobileye (autonomous driving)"},
                {"date": "2022-10-26", "action": "IPO",      "pct": 88.0,  "note": "Mobileye IPO on Nasdaq; Intel retained ~88%"},
                {"date": "2023-12-01", "action": "Reduced",  "note": "Intel sold small secondary block"},
            ],
        },
        {
            "target": "Tower Semiconductor", "ticker": "TSEM", "status": "active",
            "events": [
                {"date": "2021-02-15", "action": "Acquired", "note": "Intel announced $5.4B acquisition bid"},
                {"date": "2022-08-16", "action": "Reduced",  "note": "Deal abandoned due to Chinese regulatory block; Intel retains legacy partnership"},
            ],
        },
    ],
    "qualcomm": [
        {
            "target": "Arista Networks", "ticker": "ANET", "status": "active",
            "events": [
                {"date": "2023-01-01", "action": "Acquired", "note": "Qualcomm Ventures strategic investment in cloud networking company"},
            ],
        },
        {
            "target": "Fisker Inc", "ticker": "FSR", "status": "exited",
            "events": [
                {"date": "2022-06-02", "action": "Acquired", "note": "Strategic investment in EV startup; chip supply partnership"},
                {"date": "2024-06-17", "action": "Sold",     "note": "Fisker filed for Chapter 11 bankruptcy; equity wiped"},
            ],
        },
        {
            "target": "Vivo (BBK Electronics)", "ticker": None, "status": "active",
            "events": [
                {"date": "2023-01-01", "action": "Acquired", "note": "Snapdragon partnership includes equity component with smartphone OEM"},
            ],
        },
    ],
    "samsung": [
        {
            "target": "ASML Holding", "ticker": "ASML", "status": "exited",
            "total_invested_usd": 975_000_000,
            "events": [
                {"date": "2012-08-28", "action": "Acquired", "pct": 3.0, "note": "$975M for 3% stake; co-investment in EUV lithography tech"},
                {"date": "2016-11-01", "action": "Sold",     "note": "Divested remaining stake after lock-up expiry"},
            ],
        },
        {
            "target": "Harman International", "ticker": None, "status": "active",
            "total_invested_usd": 8_000_000_000,
            "events": [
                {"date": "2017-03-10", "action": "Acquired", "pct": 100.0, "note": "$8B full acquisition of Harman (JBL, Harman Kardon, connected car tech)"},
            ],
        },
        {
            "target": "Seagate Technology", "ticker": "STX", "status": "active",
            "events": [
                {"date": "2011-04-19", "action": "Acquired", "pct": 9.6,  "note": "Strategic investment; $1.5B for 9.6% stake"},
                {"date": "2021-01-01", "action": "Reduced",  "note": "Reduced stake below 5% reporting threshold"},
            ],
        },
    ],
}

CORP_CACHE_TTL = 3600

def _enrich_corp_entry(entry: dict) -> dict:
    """Add live price + return data for public tickers."""
    ticker = entry.get("ticker")
    if not ticker or "." in ticker:          # skip non-US / private
        entry["curr_price"] = None
        entry["curr_price_fmt"] = "N/A"
        entry["return_pct"] = None
        return entry
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.last_price) if hasattr(info, "last_price") else None
        entry["curr_price"] = price
        entry["curr_price_fmt"] = f"${price:,.2f}" if price else "N/A"
    except Exception:
        entry["curr_price"] = None
        entry["curr_price_fmt"] = "N/A"
    return entry

def _fetch_corp_holdings(key: str) -> dict:
    """Return enriched holdings data for a corporate investor."""
    if key not in CORP_INVESTORS:
        return {"error": "Unknown investor"}

    cache_key = f"corp:{key}"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < CORP_CACHE_TTL:
            return cached

    info   = CORP_INVESTORS[key]
    seed   = CORP_HOLDINGS_SEED.get(key, [])

    active  = []
    exited  = []
    history = []   # flat list of ALL events

    tickers_needed = [e["ticker"] for e in seed if e.get("ticker") and "." not in e["ticker"]]

    # Bulk fetch prices
    price_map: dict[str, float] = {}
    if tickers_needed:
        try:
            raw = yf.download(tickers_needed, period="1d", progress=False, auto_adjust=True)
            if "Close" in raw.columns:
                cl = raw["Close"].iloc[-1] if not raw.empty else {}
                for t in tickers_needed:
                    try:
                        price_map[t] = float(cl[t]) if t in cl else 0.0
                    except Exception:
                        price_map[t] = 0.0
        except Exception:
            pass

    for h in seed:
        ticker = h.get("ticker", "")
        price  = price_map.get(ticker or "", 0.0)

        entry = {
            "target":           h["target"],
            "ticker":           ticker or "",
            "status":           h.get("status", "active"),
            "pct":              h.get("pct", 0.0),
            "total_invested":   h.get("total_invested_usd", 0),
            "total_invested_fmt": (
                f"${h['total_invested_usd']/1e9:.1f}B" if h.get("total_invested_usd", 0) >= 1e9
                else f"${h['total_invested_usd']/1e6:.0f}M" if h.get("total_invested_usd", 0) >= 1e6
                else ("Undisclosed" if not h.get("total_invested_usd") else f"${h['total_invested_usd']:,}")
            ),
            "curr_price":       price,
            "curr_price_fmt":   f"${price:,.2f}" if price else "—",
            "events":           sorted(h.get("events", []), key=lambda e: e["date"]),
            "first_event_date": min((e["date"] for e in h.get("events", [])), default=""),
            "last_event_date":  max((e["date"] for e in h.get("events", [])), default=""),
        }

        if h.get("status") in ("exited", "partial"):
            exited.append(entry)
        else:
            active.append(entry)

        # Flatten all events into history
        for ev in h.get("events", []):
            history.append({
                "date":     ev["date"],
                "action":   ev["action"],
                "target":   h["target"],
                "ticker":   ticker or "",
                "pct":      ev.get("pct", h.get("pct", 0.0)),
                "note":     ev.get("note", ""),
                "status":   h.get("status", "active"),
                "curr_price_fmt": f"${price:,.2f}" if price else "—",
            })

    history.sort(key=lambda e: e["date"], reverse=True)
    active.sort(key=lambda e: e["last_event_date"], reverse=True)
    exited.sort(key=lambda e: e["last_event_date"], reverse=True)

    result = {
        "investor":     info["name"],
        "ticker":       info["ticker"],
        "color":        info.get("color", "#3b82f6"),
        "active":       active,
        "exited":       exited,
        "history":      history,
        "updated_at":   datetime.now().isoformat(),
    }
    _cache[cache_key] = (time.time(), result)
    print(f"✅ Corp holdings: {info['name']} — {len(active)} active, {len(exited)} exited, {len(history)} events")
    return result


@app.route("/api/corp-holdings")
def list_corp_investors():
    return jsonify([
        {"key": k, "name": v["name"], "ticker": v["ticker"], "color": v.get("color", "")}
        for k, v in CORP_INVESTORS.items()
    ])


@app.route("/api/corp-holdings/<key>")
def get_corp_holdings(key: str):
    data = _fetch_corp_holdings(key)
    if "error" in data:
        return jsonify(data), 404
    return jsonify(data)


# ─── Market Events Sidebar ────────────────────────────────────────────────
# Hardcoded 2026 economic calendar (BLS, Fed, BEA schedules)
_ECON_CAL_2026 = [
    # ── June 2026 ──────────────────────────────────────────────────────────
    {"date": "2026-06-01", "name": "ISM Manufacturing (May)",    "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "🏭"},
    {"date": "2026-06-03", "name": "ISM Services (May)",         "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "🏭"},
    {"date": "2026-06-05", "name": "Jobs Report — May",          "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-06-11", "name": "CPI — May",                  "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-06-12", "name": "PPI — May",                  "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-06-13", "name": "Consumer Sentiment (Prel.)", "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "📊"},
    {"date": "2026-06-17", "name": "Retail Sales — May",         "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "🛒"},
    {"date": "2026-06-17", "name": "Industrial Production",      "type": "economic", "imp": "low",    "time": "9:15 AM ET",  "icon": "🏭"},
    {"date": "2026-06-18", "name": "FOMC Rate Decision",         "type": "fed",      "imp": "high",   "time": "2:00 PM ET",  "icon": "🏦"},
    {"date": "2026-06-18", "name": "Fed Press Conference",       "type": "fed",      "imp": "high",   "time": "2:30 PM ET",  "icon": "🏦"},
    {"date": "2026-06-19", "name": "Housing Starts — May",       "type": "economic", "imp": "low",    "time": "8:30 AM ET",  "icon": "🏠"},
    {"date": "2026-06-25", "name": "New Home Sales — May",       "type": "economic", "imp": "low",    "time": "10:00 AM ET", "icon": "🏠"},
    {"date": "2026-06-25", "name": "Consumer Confidence",        "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "📊"},
    {"date": "2026-06-26", "name": "GDP Q1 — Final",             "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📈"},
    {"date": "2026-06-27", "name": "PCE Price Index — May",      "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── July 2026 ──────────────────────────────────────────────────────────
    {"date": "2026-07-01", "name": "ISM Manufacturing (Jun)",    "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "🏭"},
    {"date": "2026-07-03", "name": "Jobs Report — June",         "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-07-10", "name": "CPI — June",                 "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-07-11", "name": "PPI — June",                 "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-07-16", "name": "Retail Sales — June",        "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "🛒"},
    {"date": "2026-07-25", "name": "GDP Q2 Advance",             "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📈"},
    {"date": "2026-07-29", "name": "Consumer Confidence",        "type": "economic", "imp": "medium", "time": "10:00 AM ET", "icon": "📊"},
    {"date": "2026-07-30", "name": "FOMC Rate Decision",         "type": "fed",      "imp": "high",   "time": "2:00 PM ET",  "icon": "🏦"},
    {"date": "2026-07-30", "name": "Fed Press Conference",       "type": "fed",      "imp": "high",   "time": "2:30 PM ET",  "icon": "🏦"},
    {"date": "2026-07-31", "name": "PCE Price Index — June",     "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── August 2026 ────────────────────────────────────────────────────────
    {"date": "2026-08-07", "name": "Jobs Report — July",         "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-08-13", "name": "CPI — July",                 "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-08-14", "name": "PPI — July",                 "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-08-15", "name": "Retail Sales — July",        "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "🛒"},
    {"date": "2026-08-28", "name": "PCE Price Index — July",     "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── September 2026 ─────────────────────────────────────────────────────
    {"date": "2026-09-04", "name": "Jobs Report — August",       "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-09-10", "name": "CPI — August",               "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-09-11", "name": "PPI — August",               "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-09-17", "name": "FOMC Rate Decision",         "type": "fed",      "imp": "high",   "time": "2:00 PM ET",  "icon": "🏦"},
    {"date": "2026-09-17", "name": "Fed Press Conference",       "type": "fed",      "imp": "high",   "time": "2:30 PM ET",  "icon": "🏦"},
    {"date": "2026-09-25", "name": "GDP Q2 — Final",             "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📈"},
    {"date": "2026-09-30", "name": "PCE Price Index — August",   "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── October 2026 ───────────────────────────────────────────────────────
    {"date": "2026-10-02", "name": "Jobs Report — September",    "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-10-14", "name": "CPI — September",            "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-10-15", "name": "PPI — September",            "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-10-16", "name": "Retail Sales — September",   "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "🛒"},
    {"date": "2026-10-29", "name": "GDP Q3 Advance",             "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📈"},
    {"date": "2026-10-29", "name": "FOMC Rate Decision",         "type": "fed",      "imp": "high",   "time": "2:00 PM ET",  "icon": "🏦"},
    {"date": "2026-10-29", "name": "Fed Press Conference",       "type": "fed",      "imp": "high",   "time": "2:30 PM ET",  "icon": "🏦"},
    {"date": "2026-10-30", "name": "PCE Price Index — September","type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── November 2026 ──────────────────────────────────────────────────────
    {"date": "2026-11-06", "name": "Jobs Report — October",      "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-11-12", "name": "CPI — October",              "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-11-13", "name": "PPI — October",              "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-11-14", "name": "Retail Sales — October",     "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "🛒"},
    {"date": "2026-11-25", "name": "PCE Price Index — October",  "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    # ── December 2026 ──────────────────────────────────────────────────────
    {"date": "2026-12-04", "name": "Jobs Report — November",     "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "💼"},
    {"date": "2026-12-10", "name": "FOMC Rate Decision",         "type": "fed",      "imp": "high",   "time": "2:00 PM ET",  "icon": "🏦"},
    {"date": "2026-12-10", "name": "Fed Press Conference",       "type": "fed",      "imp": "high",   "time": "2:30 PM ET",  "icon": "🏦"},
    {"date": "2026-12-11", "name": "CPI — November",             "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-12-12", "name": "PPI — November",             "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-12-19", "name": "PCE Price Index — November", "type": "economic", "imp": "high",   "time": "8:30 AM ET",  "icon": "📊"},
    {"date": "2026-12-23", "name": "GDP Q3 — Final",             "type": "economic", "imp": "medium", "time": "8:30 AM ET",  "icon": "📈"},
]


@app.route("/api/market-events")
def market_events():
    """Key economic/macro events for this week + next (no earnings)."""
    cache_key = "market_events_v2"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < 3600:
            return jsonify(cached)

    today = datetime.now().date()
    monday_this = today - timedelta(days=today.weekday())
    friday_this = monday_this + timedelta(days=4)
    monday_next = monday_this + timedelta(days=7)
    friday_next = monday_next + timedelta(days=4)

    def _fmt_range(m, f):
        return f"{m.strftime('%b %-d')} – {f.strftime('%b %-d')}"

    econ_events = []
    for ev in _ECON_CAL_2026:
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        if monday_this <= d <= friday_next:
            econ_events.append({
                "date":       ev["date"],
                "name":       ev["name"],
                "type":       ev["type"],
                "importance": ev["imp"],
                "time":       ev["time"],
                "icon":       ev["icon"],
            })

    imp_rank  = {"high": 0, "medium": 1, "low": 2}
    type_rank = {"fed": 0, "economic": 1}
    econ_events.sort(key=lambda e: (
        e["date"],
        type_rank.get(e["type"], 9),
        imp_rank.get(e["importance"], 9),
    ))

    this_week = [e for e in econ_events if str(monday_this) <= e["date"] <= str(friday_this)]
    next_week = [e for e in econ_events if str(monday_next) <= e["date"] <= str(friday_next)]

    payload = {
        "this_week":       this_week,
        "next_week":       next_week,
        "week_label":      _fmt_range(monday_this, friday_this),
        "next_week_label": _fmt_range(monday_next, friday_next),
        "updated_at":      datetime.now().strftime("%H:%M"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


@app.route("/api/market-news")
def market_news():
    """
    Latest financial market news via yfinance news API.
    Pulls headlines for major market indices + key tickers.
    Cached 15 minutes.
    """
    cache_key = "market_news"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < 900:
            return jsonify(cached)

    # Pull news from a spread of market-relevant tickers
    NEWS_TICKERS = ["^GSPC", "^IXIC", "NVDA", "AAPL", "MSFT", "GOOGL", "META", "TSLA", "BTC-USD", "GLD"]

    seen_ids  = set()
    articles  = []
    now_ts    = time.time()

    for symbol in NEWS_TICKERS:
        try:
            t     = yf.Ticker(symbol)
            items = t.news or []
            for item in items[:6]:
                # yfinance 0.2+ nests article data under 'content'
                content   = item.get("content") or item
                uid       = item.get("id") or content.get("id") or content.get("uuid", "")
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)

                title     = (content.get("title") or item.get("title", "")).strip()
                # link: try clickThroughUrl → canonicalUrl → link → url
                link      = ""
                for lk in ["clickThroughUrl", "canonicalUrl"]:
                    obj = content.get(lk)
                    if obj and isinstance(obj, dict):
                        link = obj.get("url", "")
                        break
                if not link:
                    link = content.get("link") or item.get("link") or item.get("url", "")

                # publisher
                provider  = content.get("provider") or {}
                publisher = provider.get("displayName") or item.get("publisher", "")

                # publish time
                pub_raw   = content.get("pubDate") or content.get("displayTime") or ""
                pub_ts    = 0
                if pub_raw:
                    try:
                        import dateutil.parser
                        pub_ts = int(dateutil.parser.parse(pub_raw).timestamp())
                    except Exception:
                        pass

                # Relative time
                if pub_ts:
                    mins = int((now_ts - pub_ts) / 60)
                    if mins < 60:
                        time_str = f"{mins}m ago"
                    elif mins < 1440:
                        time_str = f"{mins // 60}h ago"
                    else:
                        time_str = f"{mins // 1440}d ago"
                else:
                    time_str = ""

                if not title:
                    continue

                articles.append({
                    "title":     title,
                    "link":      link,
                    "time":      time_str,
                    "publisher": publisher,
                    "pub_ts":    pub_ts,
                })
        except Exception:
            continue

    # Sort by most recent, dedupe by title
    seen_titles = set()
    unique = []
    for a in sorted(articles, key=lambda x: x.get("pub_ts", 0), reverse=True):
        if a["title"] not in seen_titles:
            seen_titles.add(a["title"])
            unique.append(a)

    payload = {
        "articles":   unique[:30],
        "count":      len(unique),
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ─────────────────────────────────────────────────────────────
#  WHALE CONSENSUS HEATMAP
# ─────────────────────────────────────────────────────────────

# Smart-money whales included in cross-fund consensus analysis
# (excludes big banks/passive funds — their size swamps the signal)
CONSENSUS_WHALE_KEYS = [
    "ackman", "buffett", "burry", "tepper", "dalio",
    "cathie_wood", "loeb", "hohn",
]

@app.route("/api/whales/consensus")
def whale_consensus():
    """Return latest 13F moves for all smart-money whales + cross-fund consensus.

    Fetches each whale in parallel (ThreadPoolExecutor) with a per-whale timeout.
    Cached 6 hours — 13F filings are quarterly so data rarely changes mid-day.

    Response structure:
      {
        "quarter":   "2026-03-31",
        "investors": [
          { "key", "name", "fund", "icon",
            "period", "positions", "portfolio_value",
            "moves": [{"ticker","action","pct_change","value_delta_k","shares_delta"}, ...] }
        ],
        "consensus": [
          { "ticker", "buyer_count", "seller_count",
            "buyers": ["ackman",...], "sellers": ["loeb","hohn",...],
            "net_signal": "strong_sell"|"sell"|"neutral"|"buy"|"strong_buy",
            "conviction": 3 }   ← |buyers - sellers|
        ]
      }
    """
    cache_key = "whale:consensus:v1"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < 21600:   # 6 hours
            return jsonify(cached)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    investors_out = []
    # key → list of de-duplicated ticker moves for that whale.
    moves_by_whale: dict[str, list] = {}

    def _fetch_one(key):
        try:
            data = _fetch_whale_data(key)
            if not data:
                return key, None

            raw_moves = []
            for row in (data.get("top_buys") or []):
                raw_moves.append({
                    "ticker":       row.get("ticker", ""),
                    "name":         row.get("name", ""),
                    "action":       row.get("action", "BUY"),
                    "direction":    "buy",
                    "pct_change":   row.get("pct_change"),
                    "value_change_k": row.get("value_change_k") or 0,
                    "shares_change": row.get("shares_change") or 0,
                })
            for row in (data.get("top_sells") or []):
                raw_moves.append({
                    "ticker":       row.get("ticker", ""),
                    "name":         row.get("name", ""),
                    "action":       row.get("action", "SELL"),
                    "direction":    "sell",
                    "pct_change":   row.get("pct_change"),
                    "value_change_k": row.get("value_change_k") or 0,
                    "shares_change": row.get("shares_change") or 0,
                })

            # Multiple share classes or CUSIPs can resolve to the same public ticker.
            # The consensus view should count each whale once per ticker.
            by_ticker: dict[str, dict] = {}
            for move in raw_moves:
                ticker = (move.get("ticker") or "").strip().upper()
                if not ticker or ticker == "—":
                    continue
                value_change = move.get("value_change_k") or 0
                shares_change = move.get("shares_change") or 0
                existing = by_ticker.get(ticker)
                if not existing:
                    by_ticker[ticker] = {
                        **move,
                        "ticker": ticker,
                        "_largest_leg_value": value_change,
                    }
                    continue

                existing["value_change_k"] = (existing.get("value_change_k") or 0) + value_change
                existing["shares_change"] = (existing.get("shares_change") or 0) + shares_change
                if not existing.get("name") and move.get("name"):
                    existing["name"] = move["name"]

                old_pct = existing.get("pct_change")
                new_pct = move.get("pct_change")
                if old_pct is None or (new_pct is not None and abs(new_pct) > abs(old_pct)):
                    existing["pct_change"] = new_pct

                if abs(value_change) > abs(existing.get("_largest_leg_value") or 0):
                    existing["action"] = move.get("action", existing["action"])
                    existing["_largest_leg_value"] = value_change

            moves = []
            for move in by_ticker.values():
                move.pop("_largest_leg_value", None)
                net_value = move.get("value_change_k") or 0
                net_shares = move.get("shares_change") or 0
                if net_shares < 0 or (net_shares == 0 and net_value < 0):
                    move["direction"] = "sell"
                    if move.get("action") not in ("EXIT", "REDUCE"):
                        move["action"] = "REDUCE"
                elif net_shares > 0 or (net_shares == 0 and net_value > 0):
                    move["direction"] = "buy"
                    if move.get("action") not in ("NEW", "ADD"):
                        move["action"] = "ADD"
                else:
                    continue
                # Backward-compatible aliases for older frontend code.
                move["value_delta_k"] = move["value_change_k"]
                move["shares_delta"] = move["shares_change"]
                moves.append(move)

            investor = {
                "key":             key,
                "name":            data["name"],
                "fund":            data["fund"],
                "icon":            data["icon"],
                "period":          data.get("current_period", ""),
                "positions":       data.get("total_positions", 0),
                "portfolio_value": data.get("portfolio_value", ""),
                "moves":           moves,
            }
            return key, investor
        except Exception as exc:
            print(f"⚠️  consensus fetch {key}: {exc}")
            return key, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_one, k): k for k in CONSENSUS_WHALE_KEYS}
        for fut in as_completed(futures, timeout=60):
            key, investor = fut.result()
            if investor:
                investors_out.append(investor)
                moves_by_whale[key] = investor["moves"]

    # ── Build consensus table ─────────────────────────────────────────────
    # Aggregate by ticker across all whales
    ticker_agg: dict[str, dict] = {}
    for whale_key, moves in moves_by_whale.items():
        for m in moves:
            t = m["ticker"]
            if not t:
                continue
            if t not in ticker_agg:
                ticker_agg[t] = {"ticker": t, "buyers": set(), "sellers": set(), "name": m.get("name", t)}
            if m["direction"] == "buy":
                ticker_agg[t]["buyers"].add(whale_key)
            else:
                ticker_agg[t]["sellers"].add(whale_key)

    consensus = []
    for t, agg in ticker_agg.items():
        buyers = sorted(agg["buyers"])
        sellers = sorted(agg["sellers"])
        bc = len(buyers)
        sc = len(sellers)
        conviction = abs(bc - sc)
        if bc + sc < 2:
            continue   # need at least 2 whales touching the same stock
        if bc > sc:
            net = "strong_buy" if bc >= 3 else "buy"
        elif sc > bc:
            net = "strong_sell" if sc >= 3 else "sell"
        else:
            net = "neutral"
        consensus.append({
            "ticker":        t,
            "name":          agg["name"],
            "buyer_count":   bc,
            "seller_count":  sc,
            "buyers":        buyers,
            "sellers":       sellers,
            "net_signal":    net,
            "conviction":    conviction,
            "total_whales":  bc + sc,
        })

    # Sort by most whales involved, then conviction
    consensus.sort(key=lambda x: (x["total_whales"], x["conviction"]), reverse=True)

    # Most common quarter period among fetched investors
    periods = [inv["period"] for inv in investors_out if inv.get("period")]
    quarter = max(set(periods), key=periods.count) if periods else ""

    payload = {
        "quarter":   quarter,
        "investors": sorted(investors_out, key=lambda x: x["name"]),
        "consensus": consensus[:30],
        "updated_at": datetime.now().strftime("%H:%M"),
        "whale_count": len(investors_out),
    }
    _cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


# ─────────────────────────────────────────────────────────────
#  POLITICAL CYCLE CHART
# ─────────────────────────────────────────────────────────────
@app.route("/api/cycle-chart")
def cycle_chart():
    """Return monthly normalised % returns grouped by US political-cycle year type.

    Year classification (based on US presidential-election cycle):
      election      : year % 4 == 0  → 2016, 2020, 2024 …
      post-election : year % 4 == 1  → 2017, 2021, 2025 …
      midterm       : year % 4 == 2  → 2018, 2022, 2026 …
      pre-election  : year % 4 == 3  → 2015, 2019, 2023 …

    Each month's value is % return relative to the year's first trading-day close
    (Jan 1 = 0%), so different years/price-levels are directly comparable.

    Query params:
      ticker  – stock symbol (required)
    """
    from flask import request

    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400

    cache_key = f"cycle_chart:{ticker}"
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < 3600:          # 1-hour cache for historical data
            return jsonify(cached)

    try:
        import pandas as pd

        t    = yf.Ticker(ticker)
        hist = t.history(start="2000-01-01")   # go back to 2000 → 7 full election cycles

        if hist is None or hist.empty:
            return jsonify({"error": f"No historical data found for {ticker}"}), 404

        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        hist["year"]  = hist.index.year
        hist["month"] = hist.index.month

        MONTH_LABELS  = ["Jan","Feb","Mar","Apr","May","Jun",
                         "Jul","Aug","Sep","Oct","Nov","Dec"]
        current_year  = datetime.now().year
        current_month = datetime.now().month

        def _year_type(y):
            r = int(y) % 4
            if r == 0: return "election"
            if r == 1: return "post-election"
            if r == 2: return "midterm"
            return "pre-election"

        years_data = {}
        for year, ydf in hist.groupby("year"):
            year = int(year)

            # Reference: first trading-day close of the year
            jan_rows = ydf[ydf["month"] == 1]
            if jan_rows.empty:
                continue
            jan_ref = float(jan_rows["Close"].iloc[0])
            if jan_ref == 0:
                continue

            months_list = []
            for m in range(1, 13):
                # Skip future months in the current year
                if year == current_year and m > current_month:
                    months_list.append(None)
                    continue

                mdf = ydf[ydf["month"] == m]
                if mdf.empty:
                    months_list.append(None)
                    continue

                m_high  = float(mdf["High"].max())
                m_low   = float(mdf["Low"].min())
                m_close = float(mdf["Close"].iloc[-1])  # month-end close

                pct_close = round((m_close - jan_ref) / jan_ref * 100, 2)
                pct_high  = round((m_high  - jan_ref) / jan_ref * 100, 2)
                pct_low   = round((m_low   - jan_ref) / jan_ref * 100, 2)

                months_list.append({
                    "m":         m,
                    "label":     MONTH_LABELS[m - 1],
                    "close":     round(m_close, 2),
                    "high":      round(m_high,  2),
                    "low":       round(m_low,   2),
                    "pct_close": pct_close,
                    "pct_high":  pct_high,
                    "pct_low":   pct_low,
                })

            # Full-year return (first-day close → last-day close of the year)
            annual_ret = round(
                (float(ydf["Close"].iloc[-1]) - jan_ref) / jan_ref * 100, 1
            )

            years_data[str(year)] = {
                "year_type":     _year_type(year),
                "annual_return": annual_ret,
                "months":        months_list,
            }

        payload = {"ticker": ticker, "years": years_data}
        _cache[cache_key] = (time.time(), payload)
        return jsonify(payload)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    # Start PTR live poller daemon (polls House Clerk search page every 5 min)
    _ptr_thread = threading.Thread(target=_ptr_live_poller_thread, daemon=True, name="PTRLivePoller")
    _ptr_thread.start()
    app.run(debug=True, port=5051, use_reloader=False)
