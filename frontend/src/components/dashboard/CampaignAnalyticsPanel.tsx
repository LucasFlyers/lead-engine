import { CampaignSummary, SubjectLine, IndustryMetric } from "@/lib/api";
import { TrendingUp, Star, BarChart2 } from "lucide-react";

function RateBar({ label, value, max = 100, color }: { label: string; value: number; max?: number; color: string }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 12.5, color: "var(--text-3)", width: 130, flexShrink: 0 }}>{label}</span>
      <div className="progress-track" style={{ flex: 1 }}>
        <div className="progress-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="font-mono" style={{ fontSize: 12, color: "var(--text-2)", width: 40, textAlign: "right" }}>
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

export function CampaignAnalyticsPanel({
  summary,
  subjectLines,
  industries,
}: {
  summary: CampaignSummary | null;
  subjectLines: SubjectLine[];
  industries: IndustryMetric[];
}) {
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "16px 18px 14px", borderBottom: "1px solid var(--border)" }}>
        <p className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
          Campaign Analytics
        </p>
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {/* Rate bars */}
        <div style={{ padding: "16px 18px", borderBottom: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
            <TrendingUp size={13} style={{ color: "var(--text-4)" }} />
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)" }}>
              Performance Rates
            </span>
          </div>
          <RateBar label="Reply Rate"         value={summary?.reply_rate ?? 0}    color="var(--accent)" />
          <RateBar label="Positive Rate"      value={summary?.positive_rate ?? 0} color="var(--green)" />
        </div>

        {/* Best subject lines */}
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <Star size={13} style={{ color: "var(--text-4)" }} />
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)" }}>
              Top Subject Lines
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {subjectLines.slice(0, 4).map((sl, i) => (
              <div key={i} style={{
                padding: "8px 10px",
                background: "var(--surface-2)", border: "1px solid var(--border)",
                borderRadius: 8,
              }}>
                <p style={{ fontSize: 12.5, color: "var(--text-2)", marginBottom: 4, lineHeight: 1.3 }}>
                  {sl.subject || sl.variant}
                </p>
                <div style={{ display: "flex", gap: 8 }}>
                  <span style={{ fontSize: 11, color: "var(--text-4)" }}>{sl.sent} sent</span>
                  <span className="badge badge-green" style={{ fontSize: 10 }}>{sl.reply_rate?.toFixed(1)}% reply</span>
                </div>
              </div>
            ))}
            {subjectLines.length === 0 && (
              <p style={{ fontSize: 12.5, color: "var(--text-4)" }}>No data yet</p>
            )}
          </div>
        </div>

        {/* Best industries */}
        <div style={{ padding: "14px 18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <BarChart2 size={13} style={{ color: "var(--text-4)" }} />
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)" }}>
              Best Industries
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {industries.slice(0, 5).map((ind, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 11, color: "var(--text-4)", width: 16, fontFamily: "monospace" }}>{i + 1}</span>
                <span style={{ flex: 1, fontSize: 12.5, color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ind.industry}</span>
                <span className="badge badge-accent" style={{ fontSize: 10.5 }}>{ind.reply_rate?.toFixed(1)}%</span>
                <span style={{ fontSize: 11, color: "var(--text-4)" }}>{ind.sent} sent</span>
              </div>
            ))}
            {industries.length === 0 && (
              <p style={{ fontSize: 12.5, color: "var(--text-4)" }}>No industry data yet</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
