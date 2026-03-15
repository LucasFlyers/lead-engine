"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { PainSignal, PainSignalStats } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { PainSignalsTable } from "@/components/dashboard/PainSignalsTable";
import { Zap } from "lucide-react";

export default function PainSignalsPage() {
  const [signals, setSignals] = useState<PainSignal[]>([]);
  const [stats, setStats] = useState<PainSignalStats | null>(null);

  useEffect(() => {
    api.painSignals.list({ per_page: 50 }).then(r => setSignals(r.signals)).catch(() => {});
    api.painSignals.stats().then(setStats).catch(() => {});
  }, []);

  const maxCount = stats ? Math.max(...stats.by_source.map(s => s.count), 1) : 1;

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 1100, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
            <Zap size={18} style={{ color: "var(--violet)" }} />
            <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>Pain Signals</h1>
            {stats && <span className="badge badge-violet">{stats.total} detected</span>}
          </div>
          {stats && stats.by_source.length > 0 && (
            <div className="card" style={{ padding: "18px", marginBottom: 16 }}>
              <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)", marginBottom: 14 }}>By Source</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {stats.by_source.map(src => (
                  <div key={src.source} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span style={{ fontSize: 12.5, color: "var(--text-3)", width: 100, textTransform: "capitalize" }}>{src.source}</span>
                    <div className="progress-track" style={{ flex: 1 }}>
                      <div className="progress-fill" style={{ width: `${(src.count / maxCount) * 100}%`, background: "var(--violet)" }} />
                    </div>
                    <span className="font-mono" style={{ fontSize: 12, color: "var(--text-2)", width: 32, textAlign: "right" }}>{src.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <PainSignalsTable signals={signals} />
        </div>
      </div>
    </div>
  );
}
