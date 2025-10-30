// src/components/TimeRangeSelector.jsx
export default function TimeRangeSelector({ activeRange, onSelect }) {
  const ranges = ["1D", "1W", "1M", "3M", "YTD", "1Y", "2Y", "3Y", "5Y", "Max"];

  return (
    <div className="flex flex-wrap gap-2">
      {ranges.map((range) => (
        <button
          key={range}
          onClick={() => onSelect(range)}
          className={`px-3 py-1 rounded-lg border text-sm font-medium transition-all duration-200 ${
            activeRange === range
              ? "bg-emerald-500 text-white border-emerald-400"
              : "bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700"
          }`}
        >
          {range}
        </button>
      ))}
    </div>
  );
}

