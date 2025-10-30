import { useEffect, useState, useMemo } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  BarChart,
  Bar,
  ResponsiveContainer,
} from "recharts";

const API_BASE = "http://127.0.0.1:8000";

const CATEGORY_PRESETS = [
  { id: "topVolume", label: "Top 10 Traded by Volume" },
  { id: "custom", label: "My Custom List" },
];

const CUSTOM_LIST = ["AAPL", "NVDA", "MSFT", "GOOGL", "TSLA"];

const PRICE_RANGES = [
  "1D", "1W", "1M", "3M", "YTD", "1Y", "2Y", "3Y", "5Y", "MAX"
];

function Card({ title, right, children, className = "" }) {
  return (
    <section
      className={`bg-zinc-900/60 border border-zinc-800 rounded-2xl p-4 ${className}`}
    >
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-zinc-200 font-semibold">{title}</h3>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="bg-zinc-900/60 border border-zinc-800 rounded-2xl p-4">
      <div className="text-xs text-zinc-400 uppercase tracking-wide">
        {label}
      </div>
      <div className="text-xl font-medium text-white">{value}</div>
      {sub && <div className="text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function OptionsTable({ title, rows }) {
  return (
    <Card title={title}>
      {!rows?.length ? (
        <div className="text-sm text-zinc-400">No data</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-zinc-400">
            <tr className="border-b border-zinc-800">
              <th className="py-2 text-left">Contract</th>
              <th className="py-2 text-right">Vol</th>
              <th className="py-2 text-right">OI</th>
              <th className="py-2 text-right">IV</th>
              <th className="py-2 text-right">Notional</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-zinc-900/60">
                <td className="py-2">
                  <div className="font-medium text-zinc-200">{r.contract}</div>
                  <div className="text-xs text-zinc-500">
                    {r.type} • {r.strike} • {r.expiry}
                  </div>
                </td>
                <td className="py-2 text-right">{r.volume?.toLocaleString?.()}</td>
                <td className="py-2 text-right">{r.open_interest?.toLocaleString?.()}</td>
                <td className="py-2 text-right">{r.iv ?? "-"}</td>
                <td className="py-2 text-right">{r.notional ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export default function App() {
  const [category, setCategory] = useState("custom");
  const [symbols, setSymbols] = useState(CUSTOM_LIST);
  const [symbol, setSymbol] = useState("AAPL");

  const [range, setRange] = useState("2Y");
  const [loading, setLoading] = useState(false);
  const [pageError, setPageError] = useState("");

  const [bars, setBars] = useState([]);
  const [deltas, setDeltas] = useState([]);
  const [top5, setTop5] = useState([]);
  const [unusual, setUnusual] = useState([]);

  const log = (label, data) => console.log(`[Dashboard] ${label}`, data);

  // Load category options
  useEffect(() => {
    async function loadCategory() {
      if (category === "custom") {
        setSymbols(CUSTOM_LIST);
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/top-traded`);
        if (!res.ok) throw new Error(`Top traded failed: ${res.status}`);
        const json = await res.json();
        const syms = Array.isArray(json) ? json.map((x) => x.symbol) : [];
        setSymbols(syms.length ? syms : CUSTOM_LIST);
      } catch (err) {
        setPageError(`Top traded load failed: ${err.message}`);
        setSymbols(CUSTOM_LIST);
      }
    }
    loadCategory();
  }, [category]);

  // Load main data
  useEffect(() => {
    async function loadData() {
      setLoading(true);
      setPageError("");
      try {
        const urlBars = `${API_BASE}/pricebars/${symbol}`;
        const barsRes = await fetch(urlBars);
        if (!barsRes.ok) throw new Error(`GET ${urlBars} → ${barsRes.status}`);
        const barsJson = await barsRes.json();
        const fmtBars = (Array.isArray(barsJson) ? barsJson : []).map((r) => ({
          ...r,
          date: r.date,
          close: Number(r.close),
          volume: Number(r.volume),
        }));
        setBars(fmtBars);

        // --- Try fetching deltas ---
        const deltasUrl = `${API_BASE}/pricebars/${symbol}/deltas`;
        const deltasRes = await fetch(deltasUrl);
        let fmtDeltas = [];
        if (deltasRes.ok) {
          const deltasJson = await deltasRes.json();
          fmtDeltas = (Array.isArray(deltasJson) ? deltasJson : []).map((r) => ({
            ...r,
            date: r.date,
            pct_change: r.pct_change !== null ? Number(r.pct_change) : null,
          }));
        } else {
          // fallback compute
          fmtDeltas = fmtBars.map((b, i) => {
            const prev = i > 0 ? fmtBars[i - 1].close : null;
            const pct = prev && prev !== 0 ? ((b.close - prev) / prev) * 100.0 : null;
            return { date: b.date, close: b.close, pct_change: pct, volume: b.volume };
          });
        }
        setDeltas(fmtDeltas);

        // --- Options data ---
        const topRes = await fetch(`${API_BASE}/options/activity/top5?symbol=${symbol}`);
        const unRes = await fetch(`${API_BASE}/options/activity/unusual?symbol=${symbol}`);
        const topJson = topRes.ok ? await topRes.json() : [];
        const unJson = unRes.ok ? await unRes.json() : [];
        setTop5(Array.isArray(topJson) ? topJson : []);
        setUnusual(Array.isArray(unJson) ? unJson : []);
      } catch (err) {
        setPageError(err.message);
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [symbol]);

  const latest = useMemo(
    () => (deltas.length ? deltas[deltas.length - 1] : null),
    [deltas]
  );

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="mx-auto max-w-7xl p-6 flex flex-col gap-6">
        <h1 className="text-2xl font-semibold">Trading Dashboard</h1>
        <p className="text-sm text-zinc-400">
          Pick a category, pick a ticker, view price + options flow.
        </p>

        {pageError && (
          <div className="bg-red-900/30 border border-red-800 text-red-300 rounded-xl px-3 py-2 text-sm">
            {pageError}
          </div>
        )}

        <Card className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="flex flex-col gap-2">
            <label className="text-sm text-zinc-400">Category</label>
            <select
              className="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-sm"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              {CATEGORY_PRESETS.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-sm text-zinc-400">Symbol</label>
            <select
              className="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-sm"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            >
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </Card>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Stat label="Selected Symbol" value={symbol} />
          <Stat
            label="% Change (Day)"
            value={
              latest && latest.pct_change !== null
                ? `${latest.pct_change.toFixed(2)}%`
                : "--"
            }
            sub="vs previous close"
          />
          <Stat
            label="Volume (Last Day)"
            value={latest ? latest.volume?.toLocaleString() : "--"}
            sub="shares traded"
          />
        </div>

        <Card title="Price Range">
          <div className="flex flex-wrap gap-2">
            {PRICE_RANGES.map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`px-3 py-1.5 rounded-xl text-sm border ${
                  range === r
                    ? "bg-emerald-600/90 border-emerald-500 text-white"
                    : "bg-zinc-900 border-zinc-800 text-zinc-300 hover:bg-zinc-800"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </Card>

        {/* Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card title={`${symbol} Close Price (Daily)`} className="lg:col-span-2">
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={bars}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" hide />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip />
                  <Line type="monotone" dataKey="close" stroke="#37b6ff" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title={`${symbol} Daily % Change`}>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={deltas}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" hide />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip />
                  <Bar dataKey="pct_change" fill="#a78bfa" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <Card title="Volume">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bars}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" hide />
                <YAxis />
                <Tooltip />
                <Bar dataKey="volume" fill="#16a34a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Options Section */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <div>
            <h2 className="text-lg font-semibold mb-3">Options Activity</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <OptionsTable title="Today" rows={top5} />
              <OptionsTable title="Past Week" rows={top5} />
              <OptionsTable title="Past Month" rows={top5} />
            </div>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-3">Unusual Options Activity</h2>
            <Card>
              {!unusual.length ? (
                <div className="text-sm text-zinc-400">
                  No unusual flow flagged.
                </div>
              ) : (
                <OptionsTable title="Unusual" rows={unusual} />
              )}
            </Card>
          </div>
        </div>

        {loading && <div className="text-sm text-zinc-400">Loading…</div>}

        <footer className="text-xs text-zinc-600 text-center py-6">
          Data from FastAPI backend at {API_BASE}
        </footer>
      </div>
    </div>
  );
}

