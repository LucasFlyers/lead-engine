"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { Lead, LeadStats } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { LeadsTable } from "@/components/dashboard/LeadsTable";
import { Users } from "lucide-react";

export default function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [stats, setStats] = useState<LeadStats | null>(null);

  useEffect(() => {
    api.leads.list({ per_page: 50, min_score: 1 }).then(r => setLeads(r.leads)).catch(() => {});
    api.leads.stats().then(setStats).catch(() => {});
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
            <Users size={18} style={{ color: "var(--accent)" }} />
            <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Leads</h1>
            <span className="badge badge-accent">{stats?.total_leads ?? leads.length} total</span>
          </div>
          <div style={{ display: "flex", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
            {[
              { label: "Total Leads", value: stats?.total_leads ?? 0, color: "var(--accent)" },
              { label: "Qualified", value: stats?.qualified_leads ?? 0, color: "var(--green)" },
              { label: "In Queue", value: stats?.in_queue ?? 0, color: "var(--amber)" },
            ].map(m => (
              <div key={m.label} className="card" style={{ padding: "14px 18px", minWidth: 140 }}>
                <p style={{ fontSize: 11.5, color: "var(--text-4)", marginBottom: 4 }}>{m.label}</p>
                <p className="font-display" style={{ fontSize: 22, fontWeight: 700, color: m.color }}>{m.value.toLocaleString()}</p>
              </div>
            ))}
          </div>
          <LeadsTable leads={leads} />
          {stats?.by_source && stats.by_source.length > 0 && (
            <div className="card" style={{ marginTop: 14, padding: "18px" }}>
              <p className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)", marginBottom: 14 }}>Source Breakdown</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {stats.by_source.map(src => {
                  const pct = (src.count / Math.max(stats.total_leads, 1)) * 100;
                  return (
                    <div key={src.source} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ fontSize: 12.5, color: "var(--text-3)", width: 110, textTransform: "capitalize" }}>{src.source}</span>
                      <div className="progress-track" style={{ flex: 1 }}>
                        <div className="progress-fill" style={{ width: `${pct}%`, background: "var(--accent)" }} />
                      </div>
                      <span className="font-mono" style={{ fontSize: 12, color: "var(--text-2)", width: 36, textAlign: "right" }}>{src.count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
