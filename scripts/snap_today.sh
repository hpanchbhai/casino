#!/usr/bin/env bash
# scripts/snap_today.sh
# Runs daily: snapshots options, recomputes insights, and warms API endpoints.

set -euo pipefail

# --- Project root (adjust only if you moved the repo) ---
PROJECT_ROOT="$HOME/stock-tracker"

# --- Activate venv + env vars ---
cd "$PROJECT_ROOT"
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi
export PYTHONPATH="."

# --- Target symbols (edit to your liking) ---
SYMS="AAPL,NVDA,MSFT,TSLA,AMZN,GOOGL,META"

# --- Snapshot date (ISO-8601, e.g., 2025-11-04) ---
TODAY="$(date +%F)"

echo "[snap] Ensuring price bars are fresh (safe if already present)…"
# This keeps pricebars fresh for symbols you care about (optional but helpful)
python -m etl.ensure_all --period 1y --lookback 60 --limit 50 --symbols "$SYMS" || true

echo "[snap] Taking options snapshot for: $SYMS @ $TODAY"
python -m etl.snap_options --symbols "$SYMS" --date "$TODAY"

echo "[snap] Computing option insights…"
python -m etl.compute_option_insights

echo "[snap] Warming API endpoints…"
# If your core API is running at 8000, these will help the dashboard feel instant.
curl -s "http://127.0.0.1:8000/insights/AAPL/latest" >/dev/null || true
curl -s "http://127.0.0.1:8000/insights/AAPL/history?days=90" >/dev/null || true

echo "[snap] Done."

