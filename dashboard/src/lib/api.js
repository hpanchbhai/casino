// Simple, dependency-free fetch helpers for the dashboard.
// Uses Vite env var VITE_API_BASE, falls back to local FastAPI.
const BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function fetchJSON(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, { ...opts });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} — ${detail}`);
  }
  return res.json();
}

// --- public helpers ---
export async function getLatestClose(symbol) {
  // returns PriceBarOut
  return fetchJSON(`/pricebars/${encodeURIComponent(symbol)}/latest`);
}

export async function getPriceBars(symbol, days = 365) {
  // returns PriceBarOut[]
  return fetchJSON(`/pricebars/${encodeURIComponent(symbol)}?days=${days}`);
}

