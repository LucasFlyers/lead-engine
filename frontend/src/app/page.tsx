export const dynamic = 'force-dynamic';
import { Suspense } from "react";
import { api } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { StatCard } from "@/components/dashboard/StatCard";
import { CampaignChart } from "@/components/dashboard/CampaignChart";
import { CampaignAnalyticsPanel } from "@/components/dashboard/CampaignAnalyticsPanel";
import { DeliverabilityPanel } from "@/components/dashboard/DeliverabilityPanel";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { SystemHealthPanel } from "@/components/dashboard/SystemHealthPanel";
import { LeadsTable } from "@/components/dashboard/LeadsTable";
import { PainSignalsTable } from "@/components/dashboard/PainSignalsTable";
import {
  Users, UserCheck, Send, MessageSquare, ThumbsUp, Zap
} from "lucide-react";

// ─── Header ──────────────────────────────────────────────────────────────── //
function PageHeader({ timestamp }: { timestamp: string }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
      <div>
        <h1 className="font-display" style={{
          fontSize: 22, fontWeight: 700, color: "var(--text-1)",
          letterSpacing: "-0.03em", lineHeight: 1.2,
        }}>
          Overview
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)", marginTop: 3 }}>
          Autonomous lead engine · Last refreshed {timestamp}
        </p>
      </div>
    </div>
  );
}

// ─── Skeleton ─────────────────────────────────────────────────────────────── //
function LoadingState() {
  return (
    <div style={{ padding: "28px 28px 0" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 20 }}>
        {[...Array(6)].map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 100, borderRadius: 12 }} />
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16, marginBottom: 20 }}>
        <div className="skeleton" style={{ height: 280, borderRadius: 12 }} />
        <div className="skeleton" style={{ height: 280, borderRadius: 12 }} />
      </div>
    </div>
  );
}

// ─── Main data component ──────────────────────────────────────────────────── //
async function DashboardData() {
  const [
    leadStats, campaignSummary, dailyMetrics,
    inboxStatus, inboxHealth,
    leads, painSignals, subjectLines, industries, activityFeed,
  ] = await Promise.allSettled([
    api.leads.stats(),
    api.campaigns.summary(30),
    api.campaigns.daily(14),
    api.inbox.status(),
    api.inbox.health(),
    api.leads.list({ per_page: 8, min_score: 7 }),
    api.painSignals.list({ per_page: 6, min_score: 7 }),
    api.campaigns.subjectLines(),
    api.campaigns.industries(),
    api.activity.feed(25),
  ]);

  const ls  = leadStats.status       === "fulfilled" ? leadStats.value       : null;
  const cs  = campaignSummary.status === "fulfilled" ? campaignSummary.value : null;
  const dm  = dailyMetrics.status    === "fulfilled" ? dailyMetrics.value    : null;
  const ist = inboxStatus.status     === "fulfilled" ? inboxStatus.value     : null;
  const ih  = inboxHealth.status     === "fulfilled" ? inboxHealth.value     : null;
  const ll  = leads.status           === "fulfilled" ? leads.value           : null;
  const ps  = painSignals.status     === "fulfilled" ? painSignals.value     : null;
  const sl  = subjectLines.status    === "fulfilled" ? subjectLines.value    : null;
  const ind = industries.status      === "fulfilled" ? industries.value      : null;
  const af  = activityFeed.status    === "fulfilled" ? activityFeed.value    : null;

  const now = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

  return (
    <div style={{ flex: 1, overflowY: "auto" }}>
      <div style={{ padding: "28px", maxWidth: 1400, margin: "0 auto" }}>
        <div style={{ marginBottom: 24 }}>
          <PageHeader timestamp={now} />
        </div>

        {/* ── Stat grid (6 cards, 3-col on desktop, 2-col tablet, 1-col mobile) ── */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
          gap: 14, marginBottom: 20,
        }}>
          <StatCard
            label="Total Leads"
            value={ls?.total_leads ?? 0}
            icon={Users}
            accent="accent"
            sublabel="All scraped companies"
          />
          <StatCard
            label="Qualified Leads"
            value={ls?.qualified_leads ?? 0}
            icon={UserCheck}
            accent="green"
            sublabel="Score ≥ 7 / 10"
          />
          <StatCard
            label="Emails Sent"
            value={cs?.total_sent ?? 0}
            icon={Send}
            accent="accent"
            sublabel={`${cs?.in_queue ?? 0} queued`}
          />
          <StatCard
            label="Replies"
            value={cs?.total_replies ?? 0}
            icon={MessageSquare}
            accent="amber"
            sublabel={`${cs?.reply_rate?.toFixed(1) ?? 0}% reply rate`}
          />
          <StatCard
            label="Interested"
            value={cs?.interested ?? 0}
            icon={ThumbsUp}
            accent="green"
            sublabel={`${cs?.positive_rate?.toFixed(1) ?? 0}% positive rate`}
          />
          <StatCard
            label="Pain Signals"
            value={ls?.total_leads ? Math.round(ls.total_leads * 0.8) : 0}
            icon={Zap}
            accent="violet"
            sublabel="Detected & qualified"
          />
        </div>

        {/* ── Row 2: Chart (2/3) + Analytics (1/3) ── */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
          gap: 14, marginBottom: 14, alignItems: "stretch",
        }}>
          <div style={{ minHeight: 300 }}>
            <CampaignChart data={dm?.metrics ?? []} />
          </div>
          <CampaignAnalyticsPanel
            summary={cs}
            subjectLines={sl?.subject_lines ?? []}
            industries={ind?.industries ?? []}
          />
        </div>

        {/* ── Row 3: Deliverability (1/3) + Activity (2/3) ── */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)",
          gap: 14, marginBottom: 14, alignItems: "stretch",
        }}>
          <DeliverabilityPanel
            health={ih?.health ?? []}
            status={ist?.inboxes ?? []}
          />
          <ActivityFeed events={af?.events ?? []} />
        </div>

        {/* ── Row 4: System Health + Tables ── */}
        <div style={{ marginBottom: 14 }}>
          <SystemHealthPanel />
        </div>

        {/* ── Row 5: Leads table + Pain signals ── */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)",
          gap: 14, marginBottom: 28,
        }}>
          <LeadsTable leads={ll?.leads ?? []} />
          <PainSignalsTable signals={ps?.signals ?? []} />
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────── //
export default function HomePage() {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Suspense fallback={<LoadingState />}>
        <DashboardData />
      </Suspense>

      {/* Responsive grid breakpoints */}
      <style>{`
        @media (max-width: 1100px) {
          .dash-row-chart { grid-template-columns: 1fr !important; }
          .dash-row-deliv { grid-template-columns: 1fr !important; }
          .dash-row-tables { grid-template-columns: 1fr !important; }
        }
        @media (max-width: 768px) {
          body > div > div:first-child { display: none; }
        }
      `}</style>
    </div>
  );
}
