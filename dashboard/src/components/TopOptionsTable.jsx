// src/components/OptionsTables.jsx
import { useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

// helper to render one table block
function FlowTable({ title, rows }) {
  return (
    <div className="bg-zinc-900/50 rounded-xl border border-zinc-800 p-4 flex-1 min-w-[280px]">
      <div className="text-white font-semibold mb-3">{title}</div>

      {(!rows || rows.length === 0) ? (
        <div className="text-zinc-500 text-sm border border-zinc-800 rounded-lg p-4 text-center">
          No option flow data
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-zinc-300">
            <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
              <tr>
                <th className="text-left py-2 pr-4">Contract</th>
                <th className="text-right py-2 pr-4">Vol</th>
                <th className="text-right py-2 pr-4">OI</th>
                <th className="text-right py-2 pr-4">IV</th>
                <th className="text-right py-2">Notional</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr
                  key={idx}
                  className="border-b border-zinc-800/40 last:border-none"
                >
                  <td className="py-2 pr-4 text-white font-medium">
                    {row.contract}
                    <div className="text-[10px] text-zinc-500 font-normal">
                      {row.expiry} • {row.type} • {row.strike}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-right">{row.volume}</td>
                  <td className="py-2 pr-4 text-right">{row.open_interest}</td>
                  <td className="py-2 pr-4 text-right">{row.iv}</td>
                  <td className="py-2 text-right font-medium text-emerald-400">
                    {row.notional}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function OptionsTables({ symbol }) {
  const [todayRows, setTodayRows] = useState([]);
  const [weekRows, setWeekRows] = useState([]);
  const [monthRows, setMonthRows] = useState([]);

  const [loading, setLoading] = useState(false);

  // In the future these will be real:
  //   GET /options/activity?symbol=TSLA&period=today
  //   GET /options/activity?symbol=TSLA&period=1w
  //   GET /options/activity?symbol=TSLA&period=1m
  //
  // For now we'll just stub them so the UI renders.

  useEffect(() => {
    if (!symbol) {
      setTodayRows([]);
      setWeekRows([]);
      setMonthRows([]);
      return;
    }

    async function load() {
      setLoading(true);

      try {
        // placeholder flow data until backend exists
        // we'll only create dummy rows, not fetch, so dashboard always works
        const sampleRow = [
          {
            contract: `${symbol} 100C`,
            expiry: "2025-01-17",
            type: "CALL",
            strike: "$100",
            volume: 12450,
            open_interest: 88210,
            iv: "41%",
            notional: "$3.2M",
          },
          {
            contract: `${symbol} 90P`,
            expiry: "2025-01-17",
            type: "PUT",
            strike: "$90",
            volume: 9800,
            open_interest: 45210,
            iv: "53%",
            notional: "$1.1M",
          },
        ];

        // Mock logic:
        setTodayRows(sampleRow);
        setWeekRows(sampleRow);
        setMonthRows(sampleRow);
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [symbol]);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex justify-between items-baseline">
        <div className="text-white text-lg font-semibold">
          Options Activity
        </div>
        <div className="text-xs text-zinc-500">
          {loading ? "Loading…" : symbol ? `for ${symbol}` : "—"}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <FlowTable title="Today" rows={todayRows} />
        <FlowTable title="Past Week" rows={weekRows} />
        <FlowTable title="Past Month" rows={monthRows} />
      </div>
    </section>
  );
}

