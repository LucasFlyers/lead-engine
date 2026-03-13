import { Suspense } from "react";
import { api } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { DeliverabilityPanel } from "@/components/dashboard/DeliverabilityPanel";
import { Inbox } from "lucide-react";

async function InboxData() {
  const [statusResult, healthResult] = await Promise.allSettled([
    api.inbox.status(),
    api.inbox.health(),
  ]);
  const status = statusResult.status === "fulfilled" ? statusResult.value.inboxes : [];
  const health = healthResult.status === "fulfilled" ? healthResult.value.health : [];

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
      <div style={{ maxWidth: 900, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
          <Inbox size={18} style={{ color: "var(--accent)" }} />
          <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>
            Deliverability
          </h1>
        </div>
        <DeliverabilityPanel health={health} status={status} />
      </div>
    </div>
  );
}

export default function InboxPage() {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Suspense fallback={<div style={{ flex: 1, padding: 28 }}><div className="skeleton" style={{ height: 400, borderRadius: 12 }} /></div>}>
        <InboxData />
      </Suspense>
    </div>
  );
}
