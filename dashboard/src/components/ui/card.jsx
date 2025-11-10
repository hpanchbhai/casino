// lightweight Card components so App.jsx can render without shadcn/ui
import React from "react";

export function Card({ className = "", children, ...props }) {
  return (
    <div
      className={
        "rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4 " + className
      }
      {...props}
    >
      {children}
    </div>
  );
}

export function CardContent({ className = "", children, ...props }) {
  return (
    <div className={"text-zinc-100 " + className} {...props}>
      {children}
    </div>
  );
}

