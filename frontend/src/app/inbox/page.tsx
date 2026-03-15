"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { InboxStatus, InboxHealth } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { DeliverabilityPanel } from "@/components/dashboard/DeliverabilityPanel";
import { Inbox } from "lucide-react";

export default function InboxPage() {
  const [status, setStatus] = useState<InboxStatus[]>([]);
  const [health, setHealth] = useState<InboxHealth[]>([]);

  useEffect(() => {
    api.inbox.status().then(r => setStatus(r.inboxes)).catch(() => {});
    api.inbox.health().then(r => setHealth(r.health)).catch(() => {});
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 900, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
            <Inbox size={18} style={{ color: "var(--accent)" }} />
            <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Deliverability</h1>
          </div>
          <DeliverabilityPanel health={health} status={status} />
        </div>
      </div>
    </div>
  );
}
