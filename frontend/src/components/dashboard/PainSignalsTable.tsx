import { PainSignal } from "@/lib/api";
import { ArrowUpRight, Zap } from "lucide-react";

const SOURCE_COLORS: Record<string, string> = {
  reddit:    "badge-violet",
  hackernews:"badge-amber",
  g2:        "badge-accent",
  capterra:  "badge-green",
  forum:     "badge-muted",
};

export function PainSignalsTable({ signals }: { signals: PainSignal[] }) {
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div style={{ padding: "16px 18px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <Zap size={14} style={{ color: "var(--violet)" }} />
          <span className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
            Pain Signals
          </span>
        </div>
        <a href="/pain-signals" style={{ fontSize: 12, color: "var(--accent)", textDecoration: "none", display: "flex", alignItems: "center", gap: 3 }}>
          View all <ArrowUpRight size={11} />
        </a>
      </div>
      <div style={{ padding: "8px 0" }}>
        {signals.length === 0 && (
          <div style={{ textAlign: "center", padding: "32px", color: "var(--text-4)", fontSize: 13 }}>
            No pain signals detected yet
          </div>
        )}
        {signals.map((signal, i) => (
          <div key={signal.id} style={{
            padding: "12px 18px",
            borderBottom: i < signals.length - 1 ? "1px solid var(--border)" : "none",
          }}
            onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.background = "var(--bg-subtle)"}
            onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.background = ""}
          >
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
              <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.4, flex: 1 }}>
                {signal.content.slice(0, 90)}{signal.content.length > 90 ? "…" : ""}
              </p>
              {signal.lead_potential && (
                <span className={`badge ${signal.lead_potential >= 8 ? "badge-green" : signal.lead_potential >= 6 ? "badge-amber" : "badge-muted"}`} style={{ flexShrink: 0 }}>
                  {signal.lead_potential}/10
                </span>
              )}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
              <span className={`badge ${SOURCE_COLORS[signal.source] ?? "badge-muted"}`}>
                {signal.source}
              </span>
              {signal.industry && (
                <span className="badge badge-muted">{signal.industry}</span>
              )}
              {(signal.keywords_matched ?? []).slice(0, 2).map(kw => (
                <span key={kw} className="badge badge-muted" style={{ fontSize: 10.5 }}>{kw}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
