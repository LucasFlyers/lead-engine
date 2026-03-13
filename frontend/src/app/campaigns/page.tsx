import { Suspense } from "react";
import { api } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { CampaignChart } from "@/components/dashboard/CampaignChart";
import { CampaignAnalyticsPanel } from "@/components/dashboard/CampaignAnalyticsPanel";
import { BarChart3 } from "lucide-react";

async function CampaignsData() {
  const [summary, daily, subjectLines, industries] = await Promise.allSettled([
    api.campaigns.summary(30),
    api.campaigns.daily(30),
    api.campaigns.subjectLines(),
    api.campaigns.industries(),
  ]);
  const cs  = summary.status      === "fulfilled" ? summary.value        : null;
  const dm  = daily.status        === "fulfilled" ? daily.value          : null;
  const sl  = subjectLines.status === "fulfilled" ? subjectLines.value   : null;
  const ind = industries.status   === "fulfilled" ? industries.value     : null;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
          <BarChart3 size={18} style={{ color: "var(--accent)" }} />
          <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>
            Campaigns
          </h1>
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
  );
}

export default function CampaignsPage() {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Suspense fallback={<div style={{ flex: 1, padding: 28 }}><div className="skeleton" style={{ height: 400, borderRadius: 12 }} /></div>}>
        <CampaignsData />
      </Suspense>
    </div>
  );
}
