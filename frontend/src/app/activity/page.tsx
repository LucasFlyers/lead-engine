"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { ActivityEvent } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { Activity } from "lucide-react";

export default function ActivityPage() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);

  useEffect(() => {
    api.activity.feed(100).then(r => setEvents(r.events)).catch(() => {});
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
            <Activity size={18} style={{ color: "var(--accent)" }} />
            <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Activity Log</h1>
          </div>
          <ActivityFeed events={events} />
        </div>
      </div>
    </div>
  );
}
