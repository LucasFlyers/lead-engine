"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { LeadStats, CampaignSummary, DayMetric, InboxStatus, InboxHealth, Lead, PainSignal, SubjectLine, IndustryMetric, ActivityEvent } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { StatCard } from "@/components/dashboard/StatCard";
import { CampaignChart } from "@/components/dashboard/CampaignChart";
import { CampaignAnalyticsPanel } from "@/components/dashboard/CampaignAnalyticsPanel";
import { DeliverabilityPanel } from "@/components/dashboard/DeliverabilityPanel";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { SystemHealthPanel } from "@/components/dashboard/SystemHealthPanel";
import { LeadsTable } from "@/components/dashboard/LeadsTable";
import { PainSignalsTable } from "@/components/dashboard/PainSignalsTable";
import { Users, UserCheck, Send, MessageSquare, ThumbsUp, Zap } from "lucide-react";

export default function HomePage() {
  const [ls, setLs] = useState<LeadStats | null>(null);
  const [cs, setCs] = useState<CampaignSummary | null>(null);
  const [dm, setDm] = useState<{ metrics: DayMetric[] } | null>(null);
  const [ist, setIst] = useState<{ inboxes: InboxStatus[] } | null>(null);
  const [ih, setIh] = useState<{ health: InboxHealth[] } | null>(null);
  const [ll, setLl] = useState<{ leads: Lead[] } | null>(null);
  const [ps, setPs] = useState<{ signals: PainSignal[] } | null>(null);
  const [sl, setSl] = useState<{ subject_lines: SubjectLine[] } | null>(null);
  const [ind, setInd] = useState<{ industries: IndustryMetric[] } | null>(null);
  const [af, setAf] = useState<{ events: ActivityEvent[] } | null>(null);
  const [now, setNow] = useState("");

  useEffect(() => {
    setNow(new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }));
    api.leads.stats().then(setLs).catch(() => {});
    api.campaigns.summary(30).then(setCs).catch(() => {});
    api.campaigns.daily(14).then(setDm).catch(() => {});
    api.inbox.status().then(setIst).catch(() => {});
    api.inbox.health().then(setIh).catch(() => {});
    api.leads.list({ per_page: 8, min_score: 7 }).then(setLl).catch(() => {});
    api.painSignals.list({ per_page: 6, min_score: 7 }).then(setPs).catch(() => {});
    api.campaigns.subjectLines().then(setSl).catch(() => {});
    api.campaigns.industries().then(setInd).catch(() => {});
    api.activity.feed(25).then(setAf).catch(() => {});
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto" }}>
        <div style={{ padding: "28px", maxWidth: 1400, margin: "0 auto" }}>
          <div style={{ marginBottom: 24 }}>
            <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.03em" }}>Overview</h1>
            <p style={{ fontSize: 13, color: "var(--text-3)", marginTop: 3 }}>Autonomous lead engine · Last refreshed {now}</p>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 14, marginBottom: 20 }}>
            <StatCard label="Total Leads" value={ls?.total_leads ?? 0} icon={Users} accent="accent" sublabel="All scraped companies" />
            <StatCard label="Qualified Leads" value={ls?.qualified_leads ?? 0} icon={UserCheck} accent="green" sublabel="Score ≥ 7 / 10" />
            <StatCard label="Emails Sent" value={cs?.total_sent ?? 0} icon={Send} accent="accent" sublabel={`${cs?.in_queue ?? 0} queued`} />
            <StatCard label="Replies" value={cs?.total_replies ?? 0} icon={MessageSquare} accent="amber" sublabel={`${cs?.reply_rate?.toFixed(1) ?? 0}% reply rate`} />
            <StatCard label="Interested" value={cs?.interested ?? 0} icon={ThumbsUp} accent="green" sublabel={`${cs?.positive_rate?.toFixed(1) ?? 0}% positive rate`} />
            <StatCard label="Pain Signals" value={ps?.signals?.length ?? 0} icon={Zap} accent="violet" sublabel="Detected & qualified" />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 14, marginBottom: 14 }}>
            <CampaignChart data={dm?.metrics ?? []} />
            <CampaignAnalyticsPanel summary={cs} subjectLines={sl?.subject_lines ?? []} industries={ind?.industries ?? []} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)", gap: 14, marginBottom: 14 }}>
            <DeliverabilityPanel health={ih?.health ?? []} status={ist?.inboxes ?? []} />
            <ActivityFeed events={af?.events ?? []} />
          </div>

          <div style={{ marginBottom: 14 }}>
            <SystemHealthPanel />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gap: 14, marginBottom: 28 }}>
            <LeadsTable leads={ll?.leads ?? []} />
            <PainSignalsTable signals={ps?.signals ?? []} />
          </div>
        </div>
      </div>
    </div>
  );
}
