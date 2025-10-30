// src/components/ProfitTargetCard.jsx
import { useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

export default function ProfitTargetCard({ symbol }) {
  // We'll keep some basic form state in here for now
  const [strike, setStrike] = useState("");
  const [optionType, setOptionType] = useState("CALL");
  const [premiumPaid, setPremiumPaid] = useState("");
  const [contracts, setContracts] = useState(1);
  const [targetProfit, setTargetProfit] = useState(1000);
  const [daysToExpiry, setDaysToExpiry] = useState(7);
  const [underlyingPriceNow, setUnderlyingPriceNow] = useState("");

  const [result, setResult] = useState(null);
  const [errMsg, setErrMsg] = useState("");

  async function runCalc() {
    if (
      !symbol ||
      !strike ||
      !premiumPaid ||
      !underlyingPriceNow
    ) {
      setErrMsg("Fill all fields first.");
      setResult(null);
      return;
    }

    setErrMsg("");

    const params = new URLSearchParams({
      symbol,
      strike: String(strike),
      option_type: optionType,
      premium_paid: String(premiumPaid),
      contracts: String(contracts),
      target_profit: String(targetProfit),
      days_to_expiry: String(daysToExpiry),
      underlying_price_now: String(underlyingPriceNow),
    });

    try {
      const res = await fetch(`${API_BASE}/options/profit_target?` + params);
      if (!res.ok) {
        throw new Error("profit_target failed: " + res.status);
      }
      const data = await res.json();
      setResult(data);
    } catch (err) {
      console.error("profit calc failed:", err);
      setErrMsg("Could not calculate. Check values.");
      setResult(null);
    }
  }

  // If symbol changes, clear result (so you don't see stale calc from old ticker)
  useEffect(() => {
    setResult(null);
  }, [symbol]);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-white text-lg font-semibold">
            $1K Profit Target Helper
          </div>
          <div className="text-xs text-zinc-500">
            “How far does the stock need to move for me to make ${targetProfit}?”
          </div>
        </div>

        <div className="text-xs text-zinc-500">
          {symbol ? `for ${symbol}` : "—"}
        </div>
      </div>

      <div className="bg-zinc-900/50 rounded-xl border border-zinc-800 p-4">
        {!symbol ? (
          <div className="text-zinc-500 text-sm">
            Pick a symbol first.
          </div>
        ) : (
          <>
            {/* Form */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm text-zinc-200">
              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  Strike ($)
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={strike}
                  onChange={(e) => setStrike(e.target.value)}
                  placeholder="200"
                />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  Type
                </label>
                <select
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={optionType}
                  onChange={(e) => setOptionType(e.target.value)}
                >
                  <option value="CALL">CALL</option>
                  <option value="PUT">PUT</option>
                </select>
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  Premium Paid ($/share)
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={premiumPaid}
                  onChange={(e) => setPremiumPaid(e.target.value)}
                  placeholder="5.25"
                />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  # Contracts
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={contracts}
                  onChange={(e) => setContracts(e.target.value)}
                  placeholder="1"
                />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  Target Profit ($)
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={targetProfit}
                  onChange={(e) => setTargetProfit(e.target.value)}
                  placeholder="1000"
                />
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-zinc-400 text-xs">
                  Days to Exit
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={daysToExpiry}
                  onChange={(e) => setDaysToExpiry(e.target.value)}
                  placeholder="7"
                />
              </div>

              <div className="flex flex-col gap-1 md:col-span-3">
                <label className="text-zinc-400 text-xs">
                  Stock Price Now ($)
                </label>
                <input
                  className="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2"
                  value={underlyingPriceNow}
                  onChange={(e) => setUnderlyingPriceNow(e.target.value)}
                  placeholder="195.00"
                />
              </div>
            </div>

            <div className="pt-4 flex flex-wrap gap-3 items-start">
              <button
                onClick={runCalc}
                className="bg-emerald-500 hover:bg-emerald-400 text-black font-semibold text-sm px-4 py-2 rounded-lg transition"
              >
                Calculate
              </button>

              {errMsg && (
                <div className="text-red-400 text-xs">{errMsg}</div>
              )}
            </div>

            {/* Result */}
            {result && (
              <div className="mt-6 text-sm text-zinc-200 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-zinc-950/60 border border-zinc-800 rounded-lg p-4">
                  <div className="text-zinc-400 text-xs mb-1">
                    Needed stock move
                  </div>
                  <div className="text-white text-lg font-semibold">
                    {result.needed_move_in_stock_dollars > 0
                      ? `+${result.needed_move_in_stock_dollars.toFixed(2)}`
                      : result.needed_move_in_stock_dollars.toFixed(2)
                    }{" "}
                    $
                  </div>
                  <div className="text-zinc-500 text-xs">
                    ~{result.est_pct_move_in_underlying.toFixed(2)}% move in{" "}
                    {symbol}
                  </div>
                </div>

                <div className="bg-zinc-950/60 border border-zinc-800 rounded-lg p-4">
                  <div className="text-zinc-400 text-xs mb-1">
                    Target stock price
                  </div>
                  <div className="text-white text-lg font-semibold">
                    ${result.est_target_stock_price.toFixed(2)}
                  </div>
                  <div className="text-zinc-500 text-xs">
                    Breakeven at expiry:{" "}
                    <span className="text-zinc-300 font-medium">
                      ${result.breakeven_at_expiry.toFixed(2)}
                    </span>
                  </div>
                </div>

                <div className="md:col-span-2 text-[10px] text-zinc-600 leading-relaxed">
                  Assumed delta: {result.assumed_delta}. We assume linear
                  payoff, ignore theta, IV crush, gamma, and liquidity.
                  This is ballpark guidance, not financial advice.
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

