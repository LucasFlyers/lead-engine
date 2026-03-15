"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { CampaignSummary, DayMetric, SubjectLine, IndustryMetric } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { CampaignChart } from "@/components/dashboard/CampaignChart";
import { CampaignAnalyticsPanel } from "@/components/dashboard/CampaignAnalyticsPanel";
import { BarChart3 } from "lucide-react";

export default function CampaignsPage() {
  const [cs, setCs] = useState<CampaignSummary | null>(null);
  const [dm, setDm] = useState<{ metrics: DayMetric[] } | null>(null);
  const [sl, setSl] = useState<{ subject_lines: SubjectLine[] } | null>(null);
  const [ind, setInd] = useState<{ industries: IndustryMetric[] } | null>(null);

  useEffect(() => {
    api.campaigns.summary(30).then(setCs).catch(() => {});
    api.campaigns.daily(30).then(setDm).catch(() => {});
    api.campaigns.subjectLines().then(setSl).catch(() => {});
    api.campaigns.industries().then(setInd).catch(() => {});
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
            <BarChart3 size={18} style={{ color: "var(--accent)" }} />
            <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Campaigns</h1>
            {cs && <span className="badge badge-accent">30-day window</span>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 14 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ height: 320 }}>
                <CampaignChart data={dm?.metrics ?? []} />
              </div>
              {cs && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
                  {[
                    { label: "Total Sent", value: cs.total_sent.toLocaleString() },
                    { label: "Total Replies", value: cs.total_replies.toLocaleString() },
                    { label: "Interested", value: cs.interested.toLocaleString() },
                    { label: "In Queue", value: cs.in_queue.toLocaleString() },
                  ].map(m => (
                    <div key={m.label} className="card" style={{ padding: "14px 16px" }}>
                      <p style={{ fontSize: 11, color: "var(--text-4)", marginBottom: 4 }}>{m.label}</p>
                      <p className="font-display font-mono" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)" }}>{m.value}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <CampaignAnalyticsPanel summary={cs} subjectLines={sl?.subject_lines ?? []} industries={ind?.industries ?? []} />
          </div>
        </div>
      </div>
    </div>
  );
}
