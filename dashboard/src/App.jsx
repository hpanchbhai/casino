import React, { useEffect, useMemo, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  BarChart,
  Bar,
  Cell,
} from "recharts";

/**
 * API bases — keep these exactly as your infra:
 * - Core (pricebars, categories, options top): :8000
 * - Insights (iv_rank, pcr history): :8002
 */
const CORE_API = "http://127.0.0.1:8000";
const INSIGHTS_API = "http://127.0.0.1:8002";

/**
 * Visual constants tuned to your current dark UI
 */
const COLORS = {
  text: "#E5E7EB",
  subtext: "#9CA3AF",
  panel: "#0B0F13",
  border: "#1F2937",
  grid: "#2A3441",
  lineClose: "#19c2ff",
  lineMA: "#facc15",
  green: "#16C784",
  red: "#EA3943",
  barNeutral: "#6B7280",
};

const DEFAULT_CATEGORY = "top_volume";

/** Utilities */
function classNames(...a) {
  return a.filter(Boolean).join(" ");
}

function fmtNumber(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "--";
  return Number(x).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

/** Date helpers for client-side range filtering */
function parseISO(d) {
  // some payloads are already strings like "2025-10-30"
  // keep this resilient
  try {
    return new Date(d);
  } catch {
    return null;
  }
}

function startOfYear(dateObj) {
  return new Date(dateObj.getFullYear(), 0, 1);
}

function daysToMs(days) {
  return days * 24 * 60 * 60 * 1000;
}

function filterByRange(rows, rangeId) {
  if (!Array.isArray(rows) || rows.length === 0) return rows;

  const last = rows[rows.length - 1];
  const lastDate = parseISO(last.date ?? last.trade_date ?? last.Date ?? last.d);
  if (!(lastDate instanceof Date) || isNaN(lastDate)) {
    return rows; // fallback: show all
  }

  const now = lastDate; // align to last data date rather than "today"
  let since = null;

  switch (rangeId) {
    case "1D":
      since = new Date(now.getTime() - daysToMs(1));
      break;
    case "1W":
      since = new Date(now.getTime() - daysToMs(7));
      break;
    case "1M":
      since = new Date(now.getTime() - daysToMs(30));
      break;
    case "3M":
      since = new Date(now.getTime() - daysToMs(90));
      break;
    case "6M":
      since = new Date(now.getTime() - daysToMs(182));
      break;
    case "YTD":
      since = startOfYear(now);
      break;
    case "1Y":
      since = new Date(now.getTime() - daysToMs(365));
      break;
    case "2Y":
      since = new Date(now.getTime() - daysToMs(365 * 2));
      break;
    case "3Y":
      since = new Date(now.getTime() - daysToMs(365 * 3));
      break;
    case "5Y":
      since = new Date(now.getTime() - daysToMs(365 * 5));
      break;
    case "Max":
    default:
      return rows;
  }

  // rows might be pricebars (date) or deltas (date). Normalize accessor.
  return rows.filter((r) => {
    const d = parseISO(r.date ?? r.trade_date);
    return d instanceof Date && !isNaN(d) ? d >= since : true;
  });
}

/** Main App */
export default function App() {
  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState(DEFAULT_CATEGORY);
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState("AAPL");

  const [pricebars, setPricebars] = useState([]);
  const [deltas, setDeltas] = useState([]);
  const [latest, setLatest] = useState(null);

  // insights sparkline data
  const [insightPoints, setInsightPoints] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  // timeline
  const TIMEFRAMES = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "2Y", "3Y", "5Y", "Max"];
  const [tf, setTf] = useState("3M"); // default visible range

  /** Load categories once */
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${CORE_API}/categories`);
        if (!res.ok) throw new Error("Failed to load categories");
        const json = await res.json();
        setCategories(json);
      } catch (e) {
        setErr(e.message || "Failed to load categories");
      }
    })();
  }, []);

  /** Load symbols when category changes */
  useEffect(() => {
    (async () => {
      try {
        setErr("");
        const res = await fetch(`${CORE_API}/category/${categoryId}/symbols`);
        if (!res.ok) throw new Error(`Failed to load symbols for ${categoryId}`);
        const list = await res.json();
        setSymbols(Array.isArray(list) ? list : []);
        if (!list.includes(symbol)) {
          setSymbol((list && list[0]) || "AAPL");
        }
      } catch (e) {
        setSymbols([]);
        setErr(e.message || "Failed to load symbols");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryId]);

  /** Load price data + deltas + latest when symbol changes */
  useEffect(() => {
    if (!symbol) return;
    (async () => {
      try {
        setLoading(true);
        setErr("");

        const [barsRes, deltasRes, latestRes] = await Promise.all([
          fetch(`${CORE_API}/pricebars/${symbol}`),
          fetch(`${CORE_API}/pricebars/${symbol}/deltas`),
          fetch(`${CORE_API}/pricebars/${symbol}/latest`),
        ]);

        if (!barsRes.ok) throw new Error("Failed to fetch pricebars");
        if (!deltasRes.ok) throw new Error("Failed to fetch deltas");
        if (!latestRes.ok) throw new Error("Failed to fetch latest");

        const barsJson = await barsRes.json();
        const deltasJson = await deltasRes.json();
        const latestJson = await latestRes.json();

        // Normalize numeric fields
        const normBars = (barsJson || []).map((r) => ({
          ...r,
          close: Number(r.close),
          volume: Number(r.volume),
        }));
        const normDeltas = (deltasJson || []).map((r) => ({
          ...r,
          pct_change:
            r.pct_change === null || r.pct_change === undefined
              ? null
              : Number(r.pct_change),
          volume: Number(r.volume),
        }));

        setPricebars(normBars);
        setDeltas(normDeltas);
        setLatest(latestJson || null);
      } catch (e) {
        setErr(e.message || "Failed to fetch core data");
      } finally {
        setLoading(false);
      }
    })();
  }, [symbol]);

  /** Load insights sparkline (IV-Rank over last 90 days) */
  useEffect(() => {
    if (!symbol) return;
    (async () => {
      try {
        const res = await fetch(
          `${INSIGHTS_API}/insights/${symbol}/history?days=90`
        );
        if (!res.ok) throw new Error("Failed to fetch insights history");
        const json = await res.json();
        const points = (json.points || []).map((p) => ({
          date: p.trade_date,
          iv_rank_1y:
            p.iv_rank_1y === null || p.iv_rank_1y === undefined
              ? null
              : Number(p.iv_rank_1y),
        }));
        setInsightPoints(points);
      } catch (e) {
        // don’t crash dashboard if insights are missing
        setInsightPoints([]);
        console.warn("Insights history error:", e.message);
      }
    })();
  }, [symbol]);

  const latestPct =
    deltas.length > 0 && deltas[deltas.length - 1].pct_change !== null
      ? Number(deltas[deltas.length - 1].pct_change)
      : null;

  const ivRankLatest =
    insightPoints.length > 0
      ? insightPoints[insightPoints.length - 1].iv_rank_1y
      : null;

  /** Derived chart-friendly data for IV sparkline (strip nulls) */
  const ivSpark = useMemo(
    () => insightPoints.filter((p) => p.iv_rank_1y !== null),
    [insightPoints]
  );

  /** Apply timeframe filter to pricebars & deltas (client-side only) */
  const filteredBars = useMemo(() => filterByRange(pricebars, tf), [pricebars, tf]);
  const filteredDeltas = useMemo(() => filterByRange(deltas, tf), [deltas, tf]);

  return (
    <div className="min-h-screen bg-zinc-950">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex flex-col gap-1 mb-4">
          <h1 className="text-2xl font-semibold" style={{ color: COLORS.text }}>
            Trading Dashboard
          </h1>
          <p className="text-sm" style={{ color: COLORS.subtext }}>
            Live data from your FastAPI services (core + insights)
          </p>
        </div>

        {/* Selectors */}
        <div
          className="w-full rounded-xl border mb-5"
          style={{ background: COLORS.panel, borderColor: COLORS.border }}
        >
          <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <label className="text-xs" style={{ color: COLORS.subtext }}>
                Category
              </label>
              <select
                className="px-3 py-2 rounded-lg bg-zinc-900 border outline-none"
                style={{ color: COLORS.text, borderColor: COLORS.border }}
                value={categoryId}
                onChange={(e) => setCategoryId(e.target.value)}
              >
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <label className="text-xs" style={{ color: COLORS.subtext }}>
                Symbol
              </label>
              <select
                className="px-3 py-2 rounded-lg bg-zinc-900 border outline-none"
                style={{ color: COLORS.text, borderColor: COLORS.border }}
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
          </div>
        </div>

        {/* Metrics row + IV sparkline */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          {/* Last Close */}
          <div
            className="rounded-xl border p-4"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              Last Close
            </div>
            <div className="text-xl font-medium" style={{ color: COLORS.text }}>
              {latest && latest.close !== undefined
                ? `$${fmtNumber(latest.close, 2)}`
                : "--"}
            </div>
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              {latest?.date || ""}
            </div>
          </div>

          {/* % Change (Day) */}
          <div
            className="rounded-xl border p-4"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              % Change (Day)
            </div>
            <div
              className="text-xl font-medium"
              style={{
                color:
                  latestPct === null
                    ? COLORS.text
                    : latestPct > 0
                    ? COLORS.green
                    : latestPct < 0
                    ? COLORS.red
                    : COLORS.text,
              }}
            >
              {latestPct === null ? "--" : `${fmtNumber(latestPct, 2)}%`}
            </div>
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              vs previous close
            </div>
          </div>

          {/* Volume (Last Day) */}
          <div
            className="rounded-xl border p-4"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              Volume (Last Day)
            </div>
            <div className="text-xl font-medium" style={{ color: COLORS.text }}>
              {latest && latest.volume !== undefined
                ? Number(latest.volume).toLocaleString()
                : "--"}
            </div>
            <div className="text-xs" style={{ color: COLORS.subtext }}>
              shares traded
            </div>
          </div>

          {/* IV-Rank sparkline mini-panel */}
          <div
            className="rounded-xl border p-4 flex flex-col"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs" style={{ color: COLORS.subtext }}>
                IV-Rank (90d)
              </div>
              <div
                className="text-sm font-medium"
                style={{ color: COLORS.text }}
                title="Latest IV-Rank out of the last year"
              >
                {ivRankLatest === null ? "--" : fmtNumber(ivRankLatest, 2)}
              </div>
            </div>
            <div style={{ width: "100%", height: 56 }}>
              <ResponsiveContainer width="100%" height={56}>
                <LineChart data={ivSpark}>
                  <XAxis dataKey="date" hide />
                  <YAxis hide domain={["auto", "auto"]} />
                  <Tooltip
                    contentStyle={{
                      background: "#0b0f13",
                      border: `1px solid ${COLORS.border}`,
                      color: COLORS.text,
                      borderRadius: 8,
                    }}
                    formatter={(v) => [fmtNumber(v, 2), "IV-Rank"]}
                    labelFormatter={(d) => d}
                  />
                  <Line
                    type="monotone"
                    dataKey="iv_rank_1y"
                    stroke={COLORS.lineClose}
                    strokeWidth={1.75}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Charts row: Close line + Daily % change, with timeframe toggles */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          {/* Close Line */}
          <div
            className="lg:col-span-2 rounded-xl border p-4"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div className="flex items-center justify-between mb-1">
              <div
                className="text-sm font-medium"
                style={{ color: COLORS.text }}
              >
                {symbol} Close (Daily)
              </div>
              <div className="flex flex-wrap gap-1">
                {TIMEFRAMES.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTf(t)}
                    className={classNames(
                      "px-2 py-1 text-[11px] rounded border",
                      t === tf ? "font-semibold" : ""
                    )}
                    style={{
                      color: t === tf ? COLORS.text : COLORS.subtext,
                      background: t === tf ? "#0f141a" : "#0b0f13",
                      borderColor: COLORS.border,
                    }}
                    aria-pressed={t === tf}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <div className="text-xs mb-3" style={{ color: COLORS.subtext }}>
              Showing {tf} — {filteredBars.length} points
            </div>
            <div style={{ width: "100%", height: 260 }}>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={filteredBars}>
                  <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" />
                  <XAxis dataKey="date" hide />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip
                    contentStyle={{
                      background: "#0b0f13",
                      border: `1px solid ${COLORS.border}`,
                      color: COLORS.text,
                      borderRadius: 8,
                    }}
                    formatter={(v, k) => [
                      k === "close" ? `$${fmtNumber(v, 2)}` : fmtNumber(v, 2),
                      k,
                    ]}
                    labelFormatter={(d) => d}
                  />
                  <Line
                    type="monotone"
                    dataKey="close"
                    stroke={COLORS.lineClose}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Daily % Change */}
          <div
            className="rounded-xl border p-4"
            style={{ background: COLORS.panel, borderColor: COLORS.border }}
          >
            <div
              className="text-sm font-medium mb-1"
              style={{ color: COLORS.text }}
            >
              {symbol} Daily % Change
            </div>
            <div className="text-xs mb-3" style={{ color: COLORS.subtext }}>
              Same range: {tf}
            </div>
            <div style={{ width: "100%", height: 260 }}>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={filteredDeltas}>
                  <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" />
                  <XAxis dataKey="date" hide />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip
                    contentStyle={{
                      background: "#0b0f13",
                      border: `1px solid ${COLORS.border}`,
                      color: COLORS.text,
                      borderRadius: 8,
                    }}
                    formatter={(v) => [`${fmtNumber(v, 2)}%`, "% change"]}
                    labelFormatter={(d) => d}
                  />
                  <Bar dataKey="pct_change" isAnimationActive={false}>
                    {filteredDeltas.map((d, i) => {
                      const v =
                        d && d.pct_change !== null && d.pct_change !== undefined
                          ? Number(d.pct_change)
                          : 0;
                      const fill =
                        v > 0 ? COLORS.green : v < 0 ? COLORS.red : COLORS.barNeutral;
                      return <Cell key={`cell-${i}`} fill={fill} />;
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Volume */}
        <div
          className="rounded-xl border p-4 mb-8"
          style={{ background: COLORS.panel, borderColor: COLORS.border }}
        >
          <div
            className="text-sm font-medium mb-1"
            style={{ color: COLORS.text }}
          >
            {symbol} Daily Volume
          </div>
          <div className="text-xs mb-3" style={{ color: COLORS.subtext }}>
            Shares traded per day
          </div>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={filteredBars}>
                <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" />
                <XAxis dataKey="date" hide />
                <YAxis />
                <Tooltip
                  contentStyle={{
                    background: "#0b0f13",
                    border: `1px solid ${COLORS.border}`,
                    color: COLORS.text,
                    borderRadius: 8,
                  }}
                  formatter={(v) => [Number(v).toLocaleString(), "Volume"]}
                  labelFormatter={(d) => d}
                />
                <Bar dataKey="volume" isAnimationActive={false} fill={COLORS.barNeutral} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Options activity tables */}
        <OptionsTables symbol={symbol} colors={COLORS} />
      </div>

      {/* Footer */}
      <footer className="text-center text-xs pb-6" style={{ color: COLORS.subtext }}>
        Data served from {CORE_API} (core) and {INSIGHTS_API} (insights)
      </footer>

      {/* Error/Loading banners */}
      {loading && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 text-xs px-3 py-2 rounded-lg border"
          style={{ background: COLORS.panel, borderColor: COLORS.border, color: COLORS.text }}>
          Loading {symbol}…
        </div>
      )}
      {err && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 text-xs px-3 py-2 rounded-lg border"
          style={{ background: COLORS.panel, borderColor: COLORS.border, color: COLORS.red }}>
          {err}
        </div>
      )}
    </div>
  );
}

/** Options tables component (Today / 1W / 1M) */
function OptionsTables({ symbol, colors }) {
  const [tab, setTab] = useState("today");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setErr("");
        const res = await fetch(`${CORE_API}/options/top/${symbol}?range=${tab}`);
        if (!res.ok) throw new Error("Failed to load options activity");
        const json = await res.json();
        if (alive) setRows(Array.isArray(json) ? json : []);
      } catch (e) {
        if (alive) {
          setRows([]);
          setErr(e.message || "Options fetch failed");
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [symbol, tab]);

  const tabs = [
    { id: "today", label: "Today" },
    { id: "1w", label: "1W" },
    { id: "1m", label: "1M" },
  ];

  return (
    <div
      className="rounded-xl border p-4"
      style={{ background: colors.panel, borderColor: colors.border }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-medium" style={{ color: colors.text }}>
          {symbol} Options Activity — Top Contracts
        </div>
        <div className="flex items-center gap-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={classNames(
                "px-2.5 py-1.5 text-xs rounded-lg border",
                t.id === tab ? "font-semibold" : ""
              )}
              style={{
                color: t.id === tab ? colors.text : colors.subtext,
                background: t.id === tab ? "#0f141a" : "#0b0f13",
                borderColor: colors.border,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs mb-3" style={{ color: colors.subtext }}>
        Shows the most actively traded calls/puts (any strike & expiry) by volume for the selected range.
        Use it to spot crowding or one-sided flow that might foreshadow momentum.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ color: colors.subtext }}>
              <th className="text-left py-2 pr-4">Type</th>
              <th className="text-left py-2 pr-4">Strike</th>
              <th className="text-left py-2 pr-4">Expiry</th>
              <th className="text-left py-2 pr-4">Volume</th>
              <th className="text-left py-2 pr-4">OI</th>
              <th className="text-left py-2 pr-4">Last</th>
              <th className="text-left py-2 pr-4">Bid</th>
              <th className="text-left py-2 pr-4">Ask</th>
              <th className="text-left py-2 pr-4">Notional ($)</th>
            </tr>
          </thead>
          <tbody style={{ color: colors.text }}>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="py-6 text-center" style={{ color: colors.subtext }}>
                  No data
                </td>
              </tr>
            ) : (
              rows.map((r, i) => (
                <tr key={i} className="border-t" style={{ borderColor: colors.border }}>
                  <td className="py-2 pr-4">{r.opt_type}</td>
                  <td className="py-2 pr-4">{fmtNumber(r.strike, 2)}</td>
                  <td className="py-2 pr-4">{r.expiry}</td>
                  <td className="py-2 pr-4">{r.volume?.toLocaleString?.() ?? r.volume}</td>
                  <td className="py-2 pr-4">{r.open_interest?.toLocaleString?.() ?? r.open_interest}</td>
                  <td className="py-2 pr-4">{fmtNumber(r.last_price, 2)}</td>
                  <td className="py-2 pr-4">{fmtNumber(r.bid, 2)}</td>
                  <td className="py-2 pr-4">{fmtNumber(r.ask, 2)}</td>
                  <td className="py-2 pr-4">{fmtNumber(r.notional_usd, 0)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {err && (
        <div className="mt-3 text-xs" style={{ color: colors.red }}>
          {err}
        </div>
      )}
    </div>
  );
}

