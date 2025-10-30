// src/components/PriceChart.jsx
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

export default function PriceChart({ data, symbol }) {
  if (!data || data.length === 0) {
    return (
      <div className="text-zinc-500 text-sm p-6 border border-zinc-800 rounded-xl">
        No data to display. Select a stock to view its price trend.
      </div>
    );
  }

  return (
    <div className="bg-zinc-900/50 rounded-xl border border-zinc-800 p-4">
      <div className="flex justify-between items-center mb-3">
        <div className="text-white font-semibold">{symbol} Price Trend</div>
        <div className="text-xs text-zinc-500">{data.length} data points</div>
      </div>

      <div className="w-full h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2f2f2f" />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} hide />
            <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#18181b",
                border: "1px solid #3f3f46",
                color: "#fff",
              }}
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="#16c784" // green for growth
              strokeWidth={2}
              dot={false}
              animationDuration={500}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

