export const dynamic = 'force-dynamic';
import { Suspense } from "react";
import { api } from "@/lib/api";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { PainSignalsTable } from "@/components/dashboard/PainSignalsTable";
import { Zap } from "lucide-react";

async function PainSignalsData() {
  const [signals, stats] = await Promise.allSettled([
    api.painSignals.list({ per_page: 50 }),
    api.painSignals.stats(),
  ]);
  const sl = signals.status === "fulfilled" ? signals.value.signals : [];
  const st = stats.status   === "fulfilled" ? stats.value           : null;
  const maxCount = st ? Math.max(...(st.by_source.map(s => s.count)), 1) : 1;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
          <Zap size={18} style={{ color: "var(--violet)" }} />
          <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}>
            Pain Signals
          </h1>
          {st && <span className="badge badge-violet">{st.total} detected</span>}
        </div>

        {/* Source breakdown */}
        {st && st.by_source.length > 0 && (
          <div className="card" style={{ padding: "18px", marginBottom: 16 }}>
            <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)", marginBottom: 14 }}>
              By Source
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {st.by_source.map(src => (
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

        <PainSignalsTable signals={sl} />
      </div>
    </div>
  );
}

export default function PainSignalsPage() {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Suspense fallback={<div style={{ flex: 1, padding: 28 }}><div className="skeleton" style={{ height: 400, borderRadius: 12 }} /></div>}>
        <PainSignalsData />
      </Suspense>
    </div>
  );
}
