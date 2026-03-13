"use client";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { DayMetric } from "@/lib/api";
import { format, parseISO } from "date-fns";
import { useState } from "react";

type Metric = "emails_sent" | "replies" | "interested";

const METRICS: { key: Metric; label: string; var: string }[] = [
  { key: "emails_sent", label: "Sent", var: "--accent" },
  { key: "replies",     label: "Replies", var: "--green" },
  { key: "interested",  label: "Interested", var: "--amber" },
];

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: 10, padding: "10px 14px", boxShadow: "var(--shadow-md)",
    }}>
      <p style={{ fontSize: 11, color: "var(--text-4)", marginBottom: 6 }}>
        {label}
      </p>
      {payload.map((p: any) => (
        <div key={p.name} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
          <span style={{ width: 6, height: 6, borderRadius: 3, background: p.color }} />
          <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>{p.name}</span>
          <span className="font-mono" style={{ fontSize: 12.5, color: "var(--text-1)", fontWeight: 600, marginLeft: "auto" }}>
            {p.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export function CampaignChart({ data }: { data: DayMetric[] }) {
  const [active, setActive] = useState<Metric>("emails_sent");
  const meta = METRICS.find(m => m.key === active)!;

  const formatted = data.map(d => ({
    ...d,
    date: (() => { try { return format(parseISO(d.date), "MMM d"); } catch { return d.date; } })(),
  }));

  return (
    <div className="card" style={{ height: "100%" }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "16px 18px 14px", borderBottom: "1px solid var(--border)", flexWrap: "wrap", gap: 10,
      }}>
        <div>
          <p className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
            Campaign Performance
          </p>
          <p style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 2 }}>14-day rolling window</p>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {METRICS.map(m => (
            <button
              key={m.key}
              onClick={() => setActive(m.key)}
              style={{
                padding: "4px 11px", borderRadius: 6, fontSize: 12, fontWeight: 500,
                cursor: "pointer", transition: "all 0.12s",
                background: active === m.key ? "var(--accent-muted)" : "var(--surface-3)",
                color: active === m.key ? "var(--accent)" : "var(--text-3)",
                border: `1px solid ${active === m.key ? "var(--accent-ring)" : "var(--border)"}`,
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div style={{ padding: "18px 18px 10px", height: 220 }}>
        {data.length === 0 ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-4)", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 30 }}>📊</div>
            <p style={{ fontSize: 13 }}>No data yet — start sending</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={formatted} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={`var(${meta.var})`} stopOpacity={0.18} />
                  <stop offset="95%" stopColor={`var(${meta.var})`} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "var(--text-4)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "var(--text-4)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Area
                type="monotone"
                dataKey={active}
                name={meta.label}
                stroke={`var(${meta.var})`}
                strokeWidth={2}
                fill="url(#areaGrad)"
                dot={false}
                activeDot={{ r: 4, fill: `var(${meta.var})`, strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
