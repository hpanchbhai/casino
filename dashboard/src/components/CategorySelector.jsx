// src/components/CategorySelector.jsx
import { useState, useEffect } from "react";
import customList from "../data/customList.json";

const API_BASE = "http://127.0.0.1:8000";

export default function CategorySelector({ onSymbolSelect }) {
  const [category, setCategory] = useState("");
  const [symbols, setSymbols] = useState([]);

  // sectorsPayload: { fetched_at: "...", data: { "AI Stocks": [...], ... } }
  const [sectorsPayload, setSectorsPayload] = useState(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // fetch /sectors once (dynamic sector -> ticker map)
  useEffect(() => {
    async function loadSectors() {
      try {
        const res = await fetch(`${API_BASE}/sectors`);
        if (!res.ok) throw new Error("Failed to fetch sectors");
        const data = await res.json();
        setSectorsPayload(data);
      } catch (err) {
        console.error("sectors fetch failed:", err);
        // we don't hard fail; sectors just won't populate
      }
    }
    loadSectors();
  }, []);

  // whenever category changes, update symbols list
  useEffect(() => {
    async function fetchTopVolume() {
      try {
        setLoading(true);
        setError("");

        // backend: /pricebars?limit=10
        const res = await fetch(`${API_BASE}/pricebars?limit=10`);
        if (!res.ok) {
          throw new Error("Failed to fetch top traded stocks");
        }

        const data = await res.json();
        const tickers = data.map((row) => row.symbol);
        setSymbols(tickers);
      } catch (err) {
        console.error("top volume fetch failed:", err);
        setError("Could not load top traded stocks");
        setSymbols([]);
      } finally {
        setLoading(false);
      }
    }

    if (category === "customList") {
      const listName = Object.keys(customList)[0]; // "My Custom List"
      setSymbols(customList[listName] || []);
      return;
    }

    if (category === "topVolume") {
      fetchTopVolume();
      return;
    }

    if (category.startsWith("sector:")) {
      const sectorLabel = category.replace("sector:", "");
      const fromSectors =
        sectorsPayload &&
        sectorsPayload.data &&
        sectorsPayload.data[sectorLabel];

      if (fromSectors && Array.isArray(fromSectors)) {
        setSymbols(fromSectors);
      } else {
        setSymbols([]);
      }
      return;
    }

    // default reset
    setSymbols([]);
  }, [category, sectorsPayload]);

  const sectorNames = sectorsPayload?.data
    ? Object.keys(sectorsPayload.data)
    : [];

  return (
    <div className="flex flex-col md:flex-row gap-4 bg-zinc-900/50 rounded-xl p-4 border border-zinc-800">
      {/* Category dropdown */}
      <div className="flex flex-col gap-2 w-full md:w-1/2">
        <label className="text-zinc-300 text-base font-medium">Category</label>
        <select
          value={category}
          onChange={(e) => {
            setCategory(e.target.value);
            // also clear previously selected symbol in parent
            onSymbolSelect("");
          }}
          className="bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-3 py-3 text-base focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
        >
          <option value="">Select category</option>

          <option value="topVolume">Top 10 Traded by Volume</option>
          <option value="customList">My Custom List</option>

          <optgroup label="Sectors">
            {sectorNames.length === 0 ? (
              <option disabled>Loading sectors…</option>
            ) : (
              sectorNames.map((sector) => (
                <option key={sector} value={`sector:${sector}`}>
                  {sector}
                </option>
              ))
            )}
          </optgroup>
        </select>
      </div>

      {/* Symbol dropdown */}
      <div className="flex flex-col gap-2 w-full md:w-1/2">
        <label className="text-zinc-300 text-base font-medium">Symbol</label>
        <select
          onChange={(e) => onSymbolSelect(e.target.value)}
          disabled={!symbols.length}
          className="bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-3 py-3 text-base disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
        >
          <option value="">Select symbol</option>
          {symbols.map((sym) => (
            <option key={sym} value={sym}>
              {sym}
            </option>
          ))}
        </select>
      </div>

      {/* status text */}
      <div className="flex flex-col justify-end text-xs min-h-[2rem] text-left md:text-right md:ml-auto">
        {loading && <div className="text-zinc-400">Loading…</div>}
        {error && <div className="text-red-400">{error}</div>}
      </div>
    </div>
  );
}

