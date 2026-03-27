"use client";
import { PainSignal } from "@/lib/api";
import { ArrowUpRight, Zap, ExternalLink } from "lucide-react";

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
          <div key={signal.id} className="hover-card" style={{
            padding: "12px 18px",
            borderBottom: i < signals.length - 1 ? "1px solid var(--border)" : "none",
          }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
              <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.4, flex: 1 }}>
                {signal.problem_desc
                  ? signal.problem_desc.slice(0, 100) + (signal.problem_desc.length > 100 ? "…" : "")
                  : signal.content.slice(0, 90) + (signal.content.length > 90 ? "…" : "")}
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                {signal.lead_potential && (
                  <span className={`badge ${signal.lead_potential >= 8 ? "badge-green" : signal.lead_potential >= 6 ? "badge-amber" : "badge-muted"}`}>
                    {signal.lead_potential}/10
                  </span>
                )}
                {signal.source_url && (
                  <a
                    href={signal.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => e.stopPropagation()}
                    title="Open original post"
                    style={{ display: "flex", alignItems: "center", padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--text-3)", textDecoration: "none", fontSize: 11 }}
                  >
                    <ExternalLink size={11} />
                  </a>
                )}
              </div>
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
