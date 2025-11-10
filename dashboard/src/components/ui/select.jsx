import React from "react";

export function Select({ value, onChange, children, className = "", ...props }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
      className={
        "w-full rounded-xl bg-zinc-900/60 text-zinc-100 border border-zinc-800 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-emerald-500 " +
        className
      }
      {...props}
    >
      {children}
    </select>
  );
}

export function SelectItem({ value, children, ...props }) {
  return (
    <option value={value} {...props}>
      {children}
    </option>
  );
}

