// dashboard/src/App.jsx
import { useEffect, useMemo, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";

// ---- CONFIG
const API_BASE = "http://127.0.0.1:8000";

// ---- UI helpers (no alias imports; simple Tailwind cards & selects)
function Card({ children, className = "" }) {
  return (
    <div className={`bg-zinc-900/90 border border-zinc-800 rounded-2xl p-4 shadow-sm ${className}`}>
      {children}
    </div>
  );
}
function Stat({ label, value, sub }) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="text-xl font-semibold text-zinc-100">{value}</div>
      {sub ? <div className="text-[11px] text-zinc-500">{sub}</div> : null}
    </div>
  );
}
function Select({ value, onChange, children, id }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-zinc-900 border border-zinc-800 text-zinc-100 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
    >
      {children}
    </select>
  );
}

// ---- Timeframe map (dashboard asks for 1D, 1W, 1M, 3M, YTD, 1Y, 2Y, 3Y, 5Y, Max)
const TF = [
  { id: "1D", label: "1D", days: 2 },      // need prev day for % change
  { id: "1W", label: "1W", days: 7 },
  { id: "1M", label: "1M", days: 30 },
  { id: "3M", label: "3M", days: 90 },
  { id: "YTD", label: "YTD", days: null },  // computed below
  { id: "1Y", label: "1Y", days: 365 },
  { id: "2Y", label: "2Y", days: 365 * 2 },
  { id: "3Y", label: "3Y", days: 365 * 3 },
  { id: "5Y", label: "5Y", days: 365 * 5 },
  { id: "MAX", label: "Max", days: 365 * 30 }, // approximate
];

function ytdDays() {
  const today = new Date();
  const start = new Date(today.getFullYear(), 0, 1);
  const diff = Math.ceil((today - start) / (1000 * 60 * 60 * 24)) + 1;
  return Math.max(diff, 2);
}

// ---- Main App
export default function App() {
  // Selectors
  const [categories, setCategories] = useState([]); // [{id,label}]
  const [categoryId, setCategoryId] = useState("");
  const [symbols, setSymbols] = useState([]);       // ["AAPL", "NVDA", ...]
  const [symbol, setSymbol] = useState("");

  // Timeframe
  const [tf, setTf] = useState("1M");

  // Data
  const [pricebars, setPricebars] = useState([]); // [{date, open, high, low, close, adjusted_close, volume}]
  const [deltas, setDeltas] = useState([]);       // computed client-side
  const [latest, setLatest] = useState({ date: null, close: null });

  // Options tables
  const [topToday, setTopToday] = useState([]);
  const [top1W, setTop1W] = useState([]);
  const [top1M, setTop1M] = useState([]);

  // Status
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");

  // ---- bootstrap: load categories, pick first category → symbols → first symbol
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setMsg("");
        const r = await fetch(`${API_BASE}/categories`);
        if (!r.ok) throw new Error(`GET /categories -> ${r.status}`);
        const j = await r.json();

        // normalize: allow either [{id,label}] or ["aiSector", ...]
        const cats = Array.isArray(j)
          ? j.map((c) => (typeof c === "string" ? { id: c, label: c } : c))
          : [];
        if (!alive) return;

        setCategories(cats);
        const first = cats[0]?.id || "";
        setCategoryId(first);
      } catch (e) {
        if (!alive) return;
        setMsg(`Failed to load categories: ${e.message || e}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // ---- whenever category changes, load its symbols and pick first
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!categoryId) return;
      try {
        setMsg("");
        const r = await fetch(`${API_BASE}/category/${encodeURIComponent(categoryId)}/symbols`);
        if (!r.ok) throw new Error(`GET /category/${categoryId}/symbols -> ${r.status}`);
        const j = await r.json();
        const syms = Array.isArray(j) ? j.filter((s) => typeof s === "string") : [];
        if (!alive) return;

        setSymbols(syms);
        if (syms.length && !syms.includes(symbol)) {
          setSymbol(syms[0]);
        }
      } catch (e) {
        if (!alive) return;
        setMsg(`Failed to load symbols for ${categoryId}: ${e.message || e}`);
        setSymbols([]);
        setSymbol("");
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryId]);

  // ---- timeframe → compute days for API
  const daysForTf = useMemo(() => {
    const item = TF.find((t) => t.id === tf);
    if (!item) return 30;
    if (item.id === "YTD") return ytdDays();
    return item.days ?? 30;
  }, [tf]);

  // ---- load pricebars + latest + options tables whenever symbol/timeframe changes
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!symbol) return;
      setLoading(true);
      setMsg("");
      try {
        // pricebars
        const barsRes = await fetch(`${API_BASE}/pricebars/${encodeURIComponent(symbol)}?days=${daysForTf}`);
        if (!barsRes.ok) throw new Error(`GET /pricebars/${symbol}?days=${daysForTf} -> ${barsRes.status}`);
        const barsJson = await barsRes.json();

        // latest
        const latestRes = await fetch(`${API_BASE}/pricebars/${encodeURIComponent(symbol)}/latest`);
        if (!latestRes.ok) throw new Error(`GET /pricebars/${symbol}/latest -> ${latestRes.status}`);
        const latestJson = await latestRes.json();

        // options tables
        const [t0, t1, t2] = await Promise.all([
          fetch(`${API_BASE}/options/top/${encodeURIComponent(symbol)}?range=today&limit=10`),
          fetch(`${API_BASE}/options/top/${encodeURIComponent(symbol)}?range=1w&limit=10`),
          fetch(`${API_BASE}/options/top/${encodeURIComponent(symbol)}?range=1m&limit=10`),
        ]);
        if (!t0.ok) throw new Error(`GET /options/top/${symbol}?range=today -> ${t0.status}`);
        if (!t1.ok) throw new Error(`GET /options/top/${symbol}?range=1w -> ${t1.status}`);
        if (!t2.ok) throw new Error(`GET /options/top/${symbol}?range=1m -> ${t2.status}`);

        const todayTop = await t0.json();
        const weekTop = await t1.json();
        const monthTop = await t2.json();

        if (!alive) return;

        // Format numerics defensively
        const pb = (Array.isArray(barsJson) ? barsJson : []).map((r) => ({
          date: r.date,
          open: r.open != null ? Number(r.open) : null,
          high: r.high != null ? Number(r.high) : null,
          low: r.low != null ? Number(r.low) : null,
          close: r.close != null ? Number(r.close) : null,
          adjusted_close: r.adjusted_close != null ? Number(r.adjusted_close) : null,
          volume: r.volume != null ? Number(r.volume) : 0,
        }));

        setPricebars(pb);

        const last = {
          date: latestJson?.date ?? null,
          close: latestJson?.close != null ? Number(latestJson.close) : null,
        };
        setLatest(last);

        setTopToday(Array.isArray(todayTop) ? todayTop : []);
        setTop1W(Array.isArray(weekTop) ? weekTop : []);
        setTop1M(Array.isArray(monthTop) ? monthTop : []);

        // compute deltas client-side for stat
        const dd = [];
        let prev = null;
        for (const row of pb) {
          const c = row.close;
          let pct = null;
          if (c != null && prev != null && prev !== 0) {
            pct = ((c - prev) / prev) * 100.0;
          }
          dd.push({
            date: row.date,
            close: c,
            volume: row.volume,
            pct_change: pct,
          });
          if (c != null) prev = c;
        }
        setDeltas(dd);
      } catch (e) {
        if (!alive) return;
        setMsg(e.message || String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [symbol, daysForTf]);

  // latest % change (day)
  const lastPct = useMemo(() => {
    if (!deltas.length) return null;
    const last = deltas[deltas.length - 1]?.pct_change;
    return last == null ? null : Number(last);
  }, [deltas]);

  return (
    <div className="min-h-screen bg-[#0b0f14] text-zinc-100">
      <div className="mx-auto max-w-7xl p-6 space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Trading Dashboard</h1>
            <p className="text-sm text-zinc-400">FastAPI + Postgres (local) · Robinhood-style dark</p>
          </div>

          {/* Selectors row */}
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="cat" className="text-sm text-zinc-300">Category</label>
            <Select id="cat" value={categoryId} onChange={setCategoryId}>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.label || c.id}</option>
              ))}
            </Select>

            <label htmlFor="sym" className="text-sm text-zinc-300">Symbol</label>
            <Select id="sym" value={symbol} onChange={setSymbol}>
              {symbols.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>

            <label htmlFor="tf" className="text-sm text-zinc-300">Timeframe</label>
            <Select id="tf" value={tf} onChange={setTf}>
              {TF.map((t) => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </Select>
          </div>
        </div>

        {/* Status */}
        {msg && (
          <Card className="border-red-500/40 bg-red-950/20">
            <div className="text-sm text-red-300">Error: {msg}</div>
          </Card>
        )}
        {loading && (
          <Card>
            <div className="text-sm text-zinc-400">Loading {symbol}…</div>
          </Card>
        )}

        {/* Stat cards — fixed grid, even gaps, never overlap */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Card>
            <Stat
              label="Last Close"
              value={latest?.close != null ? `$${latest.close.toFixed(2)}` : "--"}
              sub={latest?.date || ""}
            />
          </Card>
          <Card>
            <Stat
              label="% Change (Day)"
              value={
                lastPct == null
                  ? "--"
                  : `${lastPct >= 0 ? "+" : ""}${lastPct.toFixed(2)}%`
              }
              sub="vs previous close"
            />
          </Card>
          <Card>
            <Stat
              label="Volume (Last Day)"
              value={
                pricebars.length
                  ? (pricebars[pricebars.length - 1].volume ?? 0).toLocaleString()
                  : "--"
              }
              sub="shares"
            />
          </Card>
        </div>

        {/* Price chart row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Close Price Line (2/3 width on large) */}
          <Card className="lg:col-span-2">
            <div className="mb-2 text-sm text-zinc-300 font-medium">
              {symbol} Close Price — {tf}
            </div>
            <div className="text-[11px] text-zinc-500 mb-3">
              Hover for values; zoom via timeframe.
            </div>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={pricebars}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="close"
                    stroke="#00c2ff"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          {/* Daily % Change bars (1/3 width) */}
          <Card>
            <div className="mb-2 text-sm text-zinc-300 font-medium">
              {symbol} Daily % Change
            </div>
            <div className="text-[11px] text-zinc-500 mb-3">
              Computed client-side from closes
            </div>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={deltas}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" hide />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip />
                  <Bar
                    dataKey="pct_change"
                    fill="#16c784"
                    background={{ fill: "rgba(255,255,255,0.03)" }}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        {/* Options Activity tables */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card>
            <div className="mb-2 text-sm text-zinc-300 font-medium">Options Activity — Today</div>
            <TableOptions rows={topToday} />
            <p className="mt-2 text-[11px] text-zinc-500">
              Fast glance at most-traded contracts today. Useful for spotting unusual flow.
            </p>
          </Card>
          <Card>
            <div className="mb-2 text-sm text-zinc-300 font-medium">Options Activity — 1W</div>
            <TableOptions rows={top1W} />
            <p className="mt-2 text-[11px] text-zinc-500">
              1-week view smooths noise; look for clusters around strikes/expiries.
            </p>
          </Card>
          <Card>
            <div className="mb-2 text-sm text-zinc-300 font-medium">Options Activity — 1M</div>
            <TableOptions rows={top1M} />
            <p className="mt-2 text-[11px] text-zinc-500">
              1-month aggregates conviction; rising OI with volume can indicate trend.
            </p>
          </Card>
        </div>

        <footer className="pt-4 pb-10 text-[11px] text-zinc-500 text-center">
          Data from your local API at {API_BASE}. Layout uses even grid gaps to prevent overlap.
        </footer>
      </div>
    </div>
  );
}

// ---- Options table renderer
function TableOptions({ rows }) {
  if (!rows || rows.length === 0) {
    return <div className="text-sm text-zinc-400">No data.</div>;
  }
  return (
    <div className="overflow-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-950/60">
          <tr className="[&>th]:px-3 [&>th]:py-2 text-left text-zinc-400">
            <th>Type</th>
            <th>Expiry</th>
            <th className="text-right">Strike</th>
            <th className="text-right">Vol</th>
            <th className="text-right">OI</th>
            <th className="text-right">Notional</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
              <td className="px-3 py-2 font-mono">{r.opt_type}</td>
              <td className="px-3 py-2">{r.expiry}</td>
              <td className="px-3 py-2 text-right">{fmtNum(r.strike)}</td>
              <td className="px-3 py-2 text-right">{fmtInt(r.volume)}</td>
              <td className="px-3 py-2 text-right">{fmtInt(r.open_interest)}</td>
              <td className="px-3 py-2 text-right">${fmtNum(r.notional_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- format helpers
function fmtInt(n) {
  const v = Number(n);
  if (!isFinite(v)) return "-";
  return Math.round(v).toLocaleString();
}
function fmtNum(n) {
  const v = Number(n);
  if (!isFinite(v)) return "-";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

