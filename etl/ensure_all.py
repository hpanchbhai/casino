# etl/ensure_all.py
from __future__ import annotations

import argparse
import os
import sys
import json
import time
import subprocess
from typing import List, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

"""
Purpose
-------
Batch ensure price bars exist for a set of symbols.

Key behaviors
-------------
1) Discovery from Core API (preferred):
   GET {CORE_API_BASE}/category/top_volume/symbols?lookback_days=NN&limit=NN

2) Fallback to user-provided --symbols CSV if discovery fails.

3) For each resolved symbol, call your existing module:
   python -m etl.fetch_prices --symbol "<SYM>" --period <period>

Env / Defaults
--------------
- CORE_API_BASE defaults to http://127.0.0.1:8000
- Works with your current fetch_prices signature: --symbol (singular!)
"""

DEFAULT_CORE_API = os.getenv("CORE_API_BASE", "http://127.0.0.1:8000")


def _http_get_json(url: str, timeout: float = 10.0):
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if not data:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return None


def discover_top_volume_symbols(core_base: str, lookback_days: int, limit: int) -> Tuple[List[str], str]:
    """
    Try to fetch dynamic symbols from the running Core API.
    Returns (symbols, error_msg). error_msg is "" on success.
    """
    url = f"{core_base}/category/top_volume/symbols?lookback_days={lookback_days}&limit={limit}"
    try:
        payload = _http_get_json(url, timeout=12.0)
        if isinstance(payload, list):
            # Should be like ["AAPL", "NVDA", ...]
            syms = [str(s).upper() for s in payload if isinstance(s, str)]
            return (syms, "")
        else:
            return ([], f"unexpected payload type from {url}: {type(payload).__name__}")
    except HTTPError as e:
        return ([], f"HTTP {e.code} from {url}")
    except URLError as e:
        return ([], f"URL error contacting {url}: {e.reason}")
    except Exception as e:
        return ([], f"exception contacting {url}: {e}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Ensure price bars for a batch of tickers using API discovery + local fetch_prices."
    )
    ap.add_argument(
        "--period",
        default="1y",
        help="yfinance period for initial ingest (e.g., 6mo, 1y, 5y, max). Ignored if --start/--end are added in future.",
    )
    ap.add_argument(
        "--lookback",
        type=int,
        default=30,
        help="Days for /category/top_volume window (default: 30).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max symbols from top_volume discovery (default: 20).",
    )
    ap.add_argument(
        "--symbols",
        default="",
        help='Optional CSV symbols to include, e.g. "AAPL,NVDA,MSFT". Combined with discovery (deduped).',
    )
    ap.add_argument(
        "--core",
        default=DEFAULT_CORE_API,
        help=f'Core API base URL (default: {DEFAULT_CORE_API})',
    )
    return ap.parse_args()


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        x_up = x.upper()
        if x_up not in seen:
            seen.add(x_up)
            out.append(x_up)
    return out


def run_fetch_prices(symbol: str, period: str) -> Tuple[bool, int]:
    """
    Invoke: python -m etl.fetch_prices --symbol "<SYM>" --period <period>
    Returns (success, rows_ingested_guess). The second is best-effort (0 if unknown).
    """
    cmd = [
        sys.executable,
        "-m",
        "etl.fetch_prices",
        "--symbol",
        symbol,
        "--period",
        period,
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    ok = proc.returncode == 0
    rows = 0

    # Best-effort parse to report rows (if your fetch script prints one)
    if ok:
        # Look for hints like "inserted N rows" or "upserted N rows"
        for line in proc.stdout.splitlines():
            if "rows" in line.lower():
                # naive pull numbers
                import re

                m = re.search(r"(\d+)\s+rows", line.lower())
                if m:
                    try:
                        rows = int(m.group(1))
                    except Exception:
                        pass

    if not ok:
        sys.stderr.write(f"[err] {symbol}: {proc.stderr.strip()}\n")

    return ok, rows


def main():
    args = parse_args()

    # 1) Try discovery
    disc_syms: List[str] = []
    disc_err: str = ""
    print(f"[info] Discovering via {args.core}/category/top_volume/symbols?lookback_days={args.lookback}&limit={args.limit}")
    disc_syms, disc_err = discover_top_volume_symbols(args.core, args.lookback, args.limit)

    if disc_syms:
        print(f"[ok] Discovered {len(disc_syms)} symbols via top_volume")
    else:
        print(f"[warn] Discovery failed or empty: {disc_err}")

    # 2) Merge with explicit CSV
    extra_syms: List[str] = []
    if args.symbols.strip():
        extra_syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    combined = dedupe_preserve_order(disc_syms + extra_syms)
    if not combined:
        print("[error] No symbols to process (discovery empty and --symbols not provided). Exiting with code 2.")
        sys.exit(2)

    print(f"[info] Ensuring {len(combined)} symbols: {', '.join(combined)}")

    # 3) Process each
    total = len(combined)
    ok_count = 0
    total_rows = 0

    started = time.time()
    for sym in combined:
        ok, rows = run_fetch_prices(sym, args.period)
        if ok:
            ok_count += 1
            total_rows += rows
            print(f"[ok] {sym}: ensured (rows ~{rows})")
        else:
            print(f"[err] {sym}: fetch_prices failed")

    dur = time.time() - started
    print(f"[summary] ok={ok_count} total={total} ingested_rows~{total_rows} elapsed={dur:.1f}s")

    # Exit non-zero if we had any failures (so cron can alert)
    sys.exit(0 if ok_count == total else 1)


if __name__ == "__main__":
    main()

