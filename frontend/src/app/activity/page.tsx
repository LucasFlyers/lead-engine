export const dynamic = 'force-dynamic';
import { Suspense } from "react";
import { api } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { Activity } from "lucide-react";

async function ActivityData() {
  const result = await api.activity.feed(100).catch(() => ({ events: [] }));
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
          <Activity size={18} style={{ color: "var(--accent)" }} />
          <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Activity Log</h1>
        </div>
        <ActivityFeed events={result.events} />
      </div>
    </div>
  );
}

export default function ActivityPage() {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Suspense fallback={<div style={{ flex: 1, padding: 28 }}><div className="skeleton" style={{ height: 400, borderRadius: 12 }} /></div>}>
        <ActivityData />
      </Suspense>
    </div>
  );
}
