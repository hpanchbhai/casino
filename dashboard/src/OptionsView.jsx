// Copy code
// src/OptionsView.jsx
//
// This is the "Pro / Options" dashboard.
// It pulls data from the new backend endpoints you now have:
//   GET /pl/weekly
//   GET /positions/open
//   GET /options/screener
//
// It shows:
//  - Weekly $1K goal progress
//  - Open positions with unrealized P/L and theta risk
//  - Screener: which calls could (theoretically) get you $1K
//
// Styling matches the Robinhood-style theme you already use.

import { useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

// shared palette from your current design
const bgPage = "#0a0d10";
const bgPanel = "#12161c";
const textPrimary = "#ffffff";
const textSecondary = "#6b6f76";
const borderSubtle = "#1e242c";
const linePositive = "#00c805";
const lineNegative = "#ff3b5b";

export default function OptionsView() {
  const [weekly, setWeekly] = useState(null);
  const [positions, setPositions] = useState([]);
  const [screener, setScreener] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  // Basic screener params for now
  const [symbolFilter, setSymbolFilter] = useState("TSLA");
  const [assumedMovePct, setAssumedMovePct] = useState(0.03); // 3%
  const [holdDays, setHoldDays] = useState(5);

  useEffect(() => {
    async function loadAll() {
      try {
        setLoading(true);
        setErr("");

        // 1. weekly PL
        const plRes = await fetch(`${API_BASE}/pl/weekly`);
        if (!plRes.ok) throw new Error("Failed /pl/weekly");
        const plJson = await plRes.json();

        // 2. open positions
        const posRes = await fetch(`${API_BASE}/positions/open`);
        if (!posRes.ok) throw new Error("Failed /positions/open");
        const posJson = await posRes.json();

        // 3. screener
        // we'll call /options/screener with some defaults
        const qs = new URLSearchParams({
          symbol: symbolFilter || "",
          max_days_to_expiry: "14",
          min_delta: "0.4",
          max_delta: "0.65",
          min_volume: "1000",
          min_oi: "1000",
          assumed_move_pct: String(assumedMovePct), // e.g. 0.03
          hold_days: String(holdDays),               // e.g. 5
        });
        const scRes = await fetch(`${API_BASE}/options/screener?` + qs.toString());
        if (!scRes.ok) throw new Error("Failed /options/screener");
        const scJson = await scRes.json();

        setWeekly(plJson);
        setPositions(posJson);
        setScreener(scJson);
      } catch (e) {
        console.error(e);
        setErr(e.message || "Error loading data");
      } finally {
        setLoading(false);
      }
    }

    loadAll();
  }, [symbolFilter, assumedMovePct, holdDays]);

  return (
    <div
      className="min-h-screen p-6 flex flex-col gap-6"
      style={{
        backgroundColor: bgPage,
        color: textPrimary,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Inter", "Roboto", "Segoe UI", system-ui, sans-serif',
      }}
    >
      {/* HEADER / CONTROLS */}
      <header className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div
            className="text-xl font-semibold leading-none"
            style={{ color: textPrimary }}
          >
            Options Profit Target
          </div>
          <div
            className="text-sm"
            style={{ color: textSecondary }}
          >
            Goal: $1000 profit / week using call options
          </div>
        </div>

        {/* Screener parameters */}
        <div
          className="flex flex-col sm:flex-row gap-4 rounded-xl p-4"
          style={{
            backgroundColor: bgPanel,
            border: `1px solid ${borderSubtle}`,
          }}
        >
          <div className="flex flex-col text-sm">
            <label
              className="mb-1 font-medium"
              style={{ color: textSecondary }}
            >
              Symbol
            </label>
            <input
              className="rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{
                backgroundColor: bgPage,
                border: `1px solid ${borderSubtle}`,
                color: textPrimary,
              }}
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
            />
          </div>

          <div className="flex flex-col text-sm">
            <label
              className="mb-1 font-medium"
              style={{ color: textSecondary }}
            >
              Assumed Move %
            </label>
            <input
              type="number"
              step="0.01"
              min="0"
              className="rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{
                backgroundColor: bgPage,
                border: `1px solid ${borderSubtle}`,
                color: textPrimary,
              }}
              value={assumedMovePct}
              onChange={(e) => setAssumedMovePct(parseFloat(e.target.value))}
            />
            <span
              className="text-[11px] mt-1"
              style={{ color: textSecondary }}
            >
              e.g. 0.03 = +3%
            </span>
          </div>

          <div className="flex flex-col text-sm">
            <label
              className="mb-1 font-medium"
              style={{ color: textSecondary }}
            >
              Hold Days
            </label>
            <input
              type="number"
              min="1"
              className="rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{
                backgroundColor: bgPage,
                border: `1px solid ${borderSubtle}`,
                color: textPrimary,
              }}
              value={holdDays}
              onChange={(e) => setHoldDays(parseInt(e.target.value || "1", 10))}
            />
          </div>
        </div>
      </header>

      {/* STATUS / LOADING / ERROR */}
      {loading && (
        <div className="text-sm" style={{ color: textSecondary }}>
          Loading...
        </div>
      )}
      {err && (
        <div className="text-sm" style={{ color: lineNegative }}>
          {err}
        </div>
      )}

      {/* WEEKLY GOAL CARD */}
      <section
        className="rounded-xl p-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
        style={{
          backgroundColor: bgPanel,
          border: `1px solid ${borderSubtle}`,
        }}
      >
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium" style={{ color: textSecondary }}>
            Weekly Progress
          </div>

          <div className="text-3xl font-semibold leading-tight" style={{ color: textPrimary }}>
            {weekly
              ? `$${(weekly.realized_profit + weekly.unrealized_profit).toFixed(
                  2
                )} / $${weekly.target_profit.toFixed(0)}`
              : "--"}
          </div>

          <div className="text-[11px]" style={{ color: textSecondary }}>
            Includes unrealized P/L from open positions
          </div>

          <div
            className="text-[11px]"
            style={{ color: textSecondary }}
          >
            Week starting:{" "}
            {weekly ? weekly.week_start : "--"}
          </div>
        </div>

        <div className="w-full sm:w-48 mt-4 sm:mt-0">
          {/* simple progress bar */}
          <div
            className="w-full h-2 rounded-full overflow-hidden"
            style={{
              backgroundColor: "#1a1f27",
              border: `1px solid ${borderSubtle}`,
            }}
          >
            <div
              className="h-full"
              style={{
                width: weekly
                  ? `${Math.min(100, weekly.progress_pct).toFixed(2)}%`
                  : "0%",
                backgroundColor: linePositive,
              }}
            />
          </div>
          <div
            className="text-right text-[11px] mt-1 font-medium"
            style={{ color: textPrimary }}
          >
            {weekly ? `${weekly.progress_pct.toFixed(1)}%` : "--"}
          </div>
        </div>
      </section>

      {/* OPEN POSITIONS TABLE */}
      <section
        className="rounded-xl p-4"
        style={{
          backgroundColor: bgPanel,
          border: `1px solid ${borderSubtle}`,
        }}
      >
        <div className="text-base font-semibold mb-3" style={{ color: textPrimary }}>
          Open Positions
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr style={{ color: textSecondary, textAlign: "left" }}>
                <th className="pb-2 pr-4 font-medium">Contract</th>
                <th className="pb-2 pr-4 font-medium">Contracts</th>
                <th className="pb-2 pr-4 font-medium">Avg Cost</th>
                <th className="pb-2 pr-4 font-medium">Last Price</th>
                <th className="pb-2 pr-4 font-medium">Unrealized P/L</th>
                <th className="pb-2 pr-4 font-medium">Theta/day</th>
                <th className="pb-2 pr-4 font-medium">Gamma</th>
                <th className="pb-2 pr-4 font-medium">IV now</th>
              </tr>
            </thead>

            <tbody style={{ color: textPrimary }}>
              {positions.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="py-4 text-center text-[12px]"
                    style={{ color: textSecondary }}
                  >
                    No open positions
                  </td>
                </tr>
              ) : (
                positions.map((p, idx) => {
                  const unrealColor =
                    p.unrealized_pl > 0
                      ? linePositive
                      : p.unrealized_pl < 0
                      ? lineNegative
                      : textSecondary;

                  return (
                    <tr
                      key={idx}
                      className="align-top border-t"
                      style={{ borderColor: borderSubtle }}
                    >
                      <td className="py-2 pr-4 whitespace-nowrap">
                        <div className="font-semibold text-[13px]" style={{ color: textPrimary }}>
                          {p.symbol} {Number(p.strike).toFixed(2)} {p.option_type}
                        </div>
                        <div className="text-[11px]" style={{ color: textSecondary }}>
                          exp {p.expiration}
                        </div>
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {p.contracts}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        ${Number(p.avg_cost_per_contract).toFixed(2)}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {p.current_price_per_contract != null
                          ? `$${Number(p.current_price_per_contract).toFixed(2)}`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px] font-medium" style={{ color: unrealColor }}>
                        {p.unrealized_pl != null
                          ? `$${Number(p.unrealized_pl).toFixed(2)}`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {p.theta_per_day != null
                          ? `${Number(p.theta_per_day).toFixed(3)}`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {p.gamma_exposure != null
                          ? `${Number(p.gamma_exposure).toFixed(3)}`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {p.iv_now != null
                          ? `${(Number(p.iv_now) * 100).toFixed(1)}%`
                          : "--"}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* SCREENER TABLE */}
      <section
        className="rounded-xl p-4"
        style={{
          backgroundColor: bgPanel,
          border: `1px solid ${borderSubtle}`,
        }}
      >
        <div className="text-base font-semibold mb-3" style={{ color: textPrimary }}>
          Screener (Calls)
        </div>
        <div className="text-[11px] mb-4" style={{ color: textSecondary }}>
          Filtering: near-the-money calls with volume / OI and estimating
          profit per 1 contract if the stock moves in your favor.
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr style={{ color: textSecondary, textAlign: "left" }}>
                <th className="pb-2 pr-4 font-medium">Contract</th>
                <th className="pb-2 pr-4 font-medium">Prem ($)</th>
                <th className="pb-2 pr-4 font-medium">Δ</th>
                <th className="pb-2 pr-4 font-medium">θ</th>
                <th className="pb-2 pr-4 font-medium">IV</th>
                <th className="pb-2 pr-4 font-medium">Vol/OI</th>
                <th className="pb-2 pr-4 font-medium">Days</th>
                <th className="pb-2 pr-4 font-medium">Est $/1C</th>
                <th className="pb-2 pr-4 font-medium">Cts for $1K</th>
              </tr>
            </thead>

            <tbody style={{ color: textPrimary }}>
              {screener.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="py-4 text-center text-[12px]"
                    style={{ color: textSecondary }}
                  >
                    No qualifying contracts (try adjusting the filters above)
                  </td>
                </tr>
              ) : (
                screener.map((c, idx) => {
                  // choose color for est profit
                  const profitColor =
                    c.est_profit_1_contract > 0
                      ? linePositive
                      : c.est_profit_1_contract < 0
                      ? lineNegative
                      : textSecondary;

                  return (
                    <tr
                      key={idx}
                      className="align-top border-t"
                      style={{ borderColor: borderSubtle }}
                    >
                      <td className="py-2 pr-4 whitespace-nowrap text-[13px]">
                        <div className="font-semibold" style={{ color: textPrimary }}>
                          {c.symbol} {Number(c.strike).toFixed(2)}C
                        </div>
                        <div className="text-[11px]" style={{ color: textSecondary }}>
                          exp {c.expiration}
                        </div>
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        ${Number(c.last_price).toFixed(2)}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.delta != null ? Number(c.delta).toFixed(2) : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.theta != null ? Number(c.theta).toFixed(3) : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.iv != null
                          ? `${(Number(c.iv) * 100).toFixed(1)}%`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.volume} / {c.open_interest}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.days_to_expiry}
                      </td>

                      <td
                        className="py-2 pr-4 text-[13px] font-medium"
                        style={{ color: profitColor }}
                      >
                        {c.est_profit_1_contract != null
                          ? `$${Number(c.est_profit_1_contract).toFixed(0)}`
                          : "--"}
                      </td>

                      <td className="py-2 pr-4 text-[13px]">
                        {c.contracts_for_1k != null
                          ? Number(c.contracts_for_1k).toFixed(1)
                          : "--"}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div
          className="text-[11px] mt-4"
          style={{ color: textSecondary }}
        >
          "Est $/1C" = estimated dollars profit for 1 contract if the stock
          makes the assumed move in the given hold days (delta gain - theta
          decay). "Cts for $1K" = ~how many contracts you'd need to *theoretically*
          approach $1000 from that move.
        </div>
      </section>

      <footer
        className="text-center pb-8 text-[11px]"
        style={{ color: textSecondary }}
      >
        Data served locally from {API_BASE}
      </footer>
    </div>
  );
}

