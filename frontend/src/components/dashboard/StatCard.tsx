import { LucideIcon, TrendingUp, TrendingDown, Minus } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  delta?: number;
  icon: LucideIcon;
  accent?: "accent" | "green" | "amber" | "red" | "violet";
}

const accentVars: Record<string, { color: string; bg: string; ring: string }> = {
  accent: { color: "var(--accent)",  bg: "var(--accent-muted)", ring: "var(--accent-ring)" },
  green:  { color: "var(--green)",   bg: "var(--green-bg)",     ring: "var(--green-ring)" },
  amber:  { color: "var(--amber)",   bg: "var(--amber-bg)",     ring: "var(--amber-ring)" },
  red:    { color: "var(--red)",     bg: "var(--red-bg)",       ring: "var(--red-ring)" },
  violet: { color: "var(--violet)",  bg: "var(--violet-bg)",    ring: "rgba(124,58,237,0.24)" },
};

export function StatCard({ label, value, sublabel, delta, icon: Icon, accent = "accent" }: StatCardProps) {
  const a = accentVars[accent];
  const displayValue = typeof value === "number" ? value.toLocaleString() : value;
  const hasDelta = delta !== undefined && delta !== null;
  const isUp = hasDelta && delta > 0;
  const isDown = hasDelta && delta < 0;

  return (
    <div className="card card-hover" style={{ padding: "18px 20px" }}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
        <span style={{ fontSize: 12.5, fontWeight: 500, color: "var(--text-3)" }}>{label}</span>
        <div style={{
          width: 32, height: 32, borderRadius: 8,
          background: a.bg, border: `1px solid ${a.ring}`,
          display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
        }}>
          <Icon size={15} style={{ color: a.color }} />
        </div>
      </div>

      {/* Value */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
        <span className="font-display" style={{
          fontSize: 26, fontWeight: 700, lineHeight: 1,
          color: "var(--text-1)", letterSpacing: "-0.02em",
          fontVariantNumeric: "tabular-nums",
        }}>
          {displayValue}
        </span>
        {hasDelta && (
          <span style={{
            display: "flex", alignItems: "center", gap: 3,
            fontSize: 11.5, fontWeight: 600, marginBottom: 2,
            color: isUp ? "var(--green)" : isDown ? "var(--red)" : "var(--text-3)",
          }}>
            {isUp ? <TrendingUp size={11} /> : isDown ? <TrendingDown size={11} /> : <Minus size={11} />}
            {Math.abs(delta)}%
          </span>
        )}
      </div>

      {/* Sublabel */}
      {sublabel && (
        <p style={{ fontSize: 11.5, color: "var(--text-4)", marginTop: 5 }}>{sublabel}</p>
      )}
    </div>
  );
}
