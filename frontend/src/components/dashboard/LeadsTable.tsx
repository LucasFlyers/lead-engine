import { Lead } from "@/lib/api";
import { ArrowUpRight } from "lucide-react";

function ScoreBadge({ score }: { score?: number }) {
  if (!score) return <span style={{ fontSize: 12, color: "var(--text-4)" }}>—</span>;
  const cls = score >= 8 ? "badge-green" : score >= 6 ? "badge-amber" : "badge-muted";
  return <span className={`badge ${cls}`}>{score}/10</span>;
}

export function LeadsTable({ leads }: { leads: Lead[] }) {
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div style={{ padding: "16px 18px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
          Top Leads
        </span>
        <a href="/leads" style={{ fontSize: 12, color: "var(--accent)", textDecoration: "none", display: "flex", alignItems: "center", gap: 3 }}>
          View all <ArrowUpRight size={11} />
        </a>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["Company", "Industry", "Source", "Score"].map(h => (
                <th key={h} style={{
                  textAlign: "left", padding: "8px 18px 8px",
                  fontSize: 11, fontWeight: 600, letterSpacing: "0.06em",
                  textTransform: "uppercase", color: "var(--text-4)",
                  borderBottom: "1px solid var(--border)",
                  whiteSpace: "nowrap",
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {leads.length === 0 && (
              <tr>
                <td colSpan={4} style={{ textAlign: "center", padding: "32px", color: "var(--text-4)", fontSize: 13 }}>
                  No leads yet — pipeline will populate this
                </td>
              </tr>
            )}
            {leads.map((lead, i) => (
              <tr key={lead.id} style={{ borderBottom: i < leads.length - 1 ? "1px solid var(--border)" : "none" }}
                onMouseEnter={e => (e.currentTarget as HTMLTableRowElement).style.background = "var(--bg-subtle)"}
                onMouseLeave={e => (e.currentTarget as HTMLTableRowElement).style.background = ""}
              >
                <td style={{ padding: "10px 18px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{
                      width: 26, height: 26, borderRadius: 6, flexShrink: 0,
                      background: "var(--accent-muted)", border: "1px solid var(--accent-ring)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 11, fontWeight: 700, color: "var(--accent)", fontFamily: "Outfit, sans-serif",
                    }}>
                      {lead.company_name[0]?.toUpperCase()}
                    </div>
                    <div>
                      <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-1)", lineHeight: 1.2 }}>
                        {lead.company_name}
                      </p>
                      {lead.website && (
                        <p style={{ fontSize: 11, color: "var(--text-4)", lineHeight: 1 }}>{lead.website.replace(/^https?:\/\/(www\.)?/, "")}</p>
                      )}
                    </div>
                  </div>
                </td>
                <td style={{ padding: "10px 18px" }}>
                  <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>{lead.industry ?? "—"}</span>
                </td>
                <td style={{ padding: "10px 18px" }}>
                  <span className="badge badge-muted" style={{ fontSize: 11 }}>{lead.source}</span>
                </td>
                <td style={{ padding: "10px 18px" }}>
                  <ScoreBadge score={lead.score} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
