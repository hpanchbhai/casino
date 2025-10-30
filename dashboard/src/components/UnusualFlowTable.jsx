// src/components/UnusualFlowTable.jsx
import { useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

export default function UnusualFlowTable({ symbol }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errMsg, setErrMsg] = useState("");

  useEffect(() => {
    async function load() {
      if (!symbol) {
        setRows([]);
        setErrMsg("");
        return;
      }

      setLoading(true);
      setErrMsg("");

      try {
        const res = await fetch(
          `${API_BASE}/options/activity/unusual?symbol=${encodeURIComponent(
            symbol
          )}`
        );
        if (!res.ok) {
          throw new Error(`unusual flow failed: ${res.status}`);
        }
        const data = await res.json();
        setRows(data.rows || []);
      } catch (err) {
        console.error("Unusual flow fetch failed:", err);
        setErrMsg("Could not load unusual flow");
        setRows([]);
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [symbol]);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-white text-lg font-semibold">
            Unusual Options Activity
          </div>
          <div className="text-xs text-zinc-500">
            Contracts trading far above normal volume
          </div>
        </div>

        <div className="text-xs text-zinc-500">
          {symbol ? `for ${symbol}` : "—"}
        </div>
      </div>

      <div className="bg-zinc-900/50 rounded-xl border border-zinc-800 p-4">
        {loading ? (
          <div className="text-zinc-400 text-sm">Loading…</div>
        ) : errMsg ? (
          <div className="text-red-400 text-sm">{errMsg}</div>
        ) : rows.length === 0 ? (
          <div className="text-zinc-500 text-sm border border-zinc-800 rounded-lg p-4 text-center">
            No unusual flow flagged
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-zinc-300">
              <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
                <tr>
                  <th className="text-left py-2 pr-4">Contract</th>
                  <th className="text-left py-2 pr-4">Expiry</th>
                  <th className="text-left py-2 pr-4">Type</th>
                  <th className="text-left py-2 pr-4">Strike</th>
                  <th className="text-right py-2 pr-4 whitespace-nowrap">
                    Vol (Today)
                  </th>
                  <th className="text-right py-2 pr-4 whitespace-nowrap">
                    Avg Vol (20d)
                  </th>
                  <th className="text-right py-2 pr-4">x Avg</th>
                  <th className="text-right py-2 pr-4">IV</th>
                  <th className="text-right py-2">Notional</th>
                </tr>
              </thead>

              <tbody>
                {rows.map((row, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-zinc-800/40 last:border-none align-top"
                  >
                    <td className="py-2 pr-4 text-white font-medium">
                      {row.contract}
                      {row.note && (
                        <div className="text-[10px] text-zinc-500 font-normal">
                          {row.note}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-zinc-400">{row.expiry}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={
                          row.type === "CALL"
                            ? "text-emerald-400 font-medium"
                            : "text-red-400 font-medium"
                        }
                      >
                        {row.type}
                      </span>
                    </td>
                    <td className="py-2 pr-4">{row.strike}</td>
                    <td className="py-2 pr-4 text-right text-zinc-100">
                      {row.volume_today?.toLocaleString?.() ??
                        row.volume_today}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      {row.avg_volume_20d?.toLocaleString?.() ??
                        row.avg_volume_20d}
                    </td>
                    <td className="py-2 pr-4 text-right font-medium text-amber-400">
                      {row.vol_ratio?.toFixed
                        ? row.vol_ratio.toFixed(1) + "x"
                        : row.vol_ratio + "x"}
                    </td>
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

        <div className="text-[10px] text-zinc-600 text-center mt-4">
          Unusual = (today volume / 20d avg volume). Stubbed data for now.
        </div>
      </div>
    </section>
  );
}

