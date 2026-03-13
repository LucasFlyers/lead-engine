"use client";
import { InboxHealth, InboxStatus } from "@/lib/api";
import { ShieldCheck, TrendingDown, AlertCircle, Thermometer, SendHorizonal, Pause, Play } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";

const WARMUP_WEEKS = [
  { week: 1, limit: 8 },
  { week: 2, limit: 15 },
  { week: 3, limit: 25 },
  { week: 4, limit: 35 },
  { week: 5, limit: 45 },
  { week: 6, limit: 55 },
];

function WarmupProgress({ week }: { week: number }) {
  const w = Math.min(week, 6);
  const pct = (w / 6) * 100;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>Week {week} of 6+</span>
        <span className="font-mono" style={{ fontSize: 12, color: "var(--text-2)" }}>
          {WARMUP_WEEKS.find(x => x.week === w)?.limit ?? "60+"}/day
        </span>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{
          width: `${pct}%`,
          background: pct < 50 ? "var(--amber)" : pct < 85 ? "var(--accent)" : "var(--green)",
        }} />
      </div>
      <div style={{ display: "flex", marginTop: 4 }}>
        {WARMUP_WEEKS.map(wk => (
          <div
            key={wk.week}
            title={`Week ${wk.week}: ${wk.limit}/day`}
            style={{
              flex: 1, height: 14, cursor: "help",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <div style={{
              width: 5, height: 5, borderRadius: 3,
              background: wk.week <= w ? "var(--accent)" : "var(--border)",
              transition: "background 0.3s",
            }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function HealthScore({ bounce, spam }: { bounce: number; spam: number }) {
  const score = Math.max(0, Math.round(100 - (bounce * 10) - (spam * 500)));
  const color = score >= 80 ? "var(--green)" : score >= 60 ? "var(--amber)" : "var(--red)";
  const label = score >= 80 ? "Good" : score >= 60 ? "Fair" : "Poor";

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "12px 14px", background: "var(--surface-2)", border: "1px solid var(--border)",
      borderRadius: 10, marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 8, background: `color-mix(in srgb, ${color} 12%, transparent)`, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <ShieldCheck size={16} style={{ color }} />
        </div>
        <div>
          <p style={{ fontSize: 12, color: "var(--text-3)" }}>Inbox Health Score</p>
          <p className="font-display" style={{ fontSize: 18, fontWeight: 700, color: "var(--text-1)", lineHeight: 1.2 }}>
            {score}<span style={{ fontSize: 12, fontWeight: 400, color: "var(--text-3)" }}>/100</span>
          </p>
        </div>
      </div>
      <span className={`badge badge-${score >= 80 ? "green" : score >= 60 ? "amber" : "red"}`}>{label}</span>
    </div>
  );
}

export function DeliverabilityPanel({
  health,
  status,
}: {
  health: InboxHealth[];
  status: InboxStatus[];
}) {
  const [toggling, setToggling] = useState<string | null>(null);
  const statusMap = Object.fromEntries(status.map(s => [s.email, s]));

  const allInboxes = health.length > 0 ? health : status.map(s => ({
    inbox: s.email, warmup_week: s.warmup_week, daily_limit: s.daily_limit,
    sent_today: s.sent_today, bounce_rate: "0%", spam_rate: "0%",
    reply_rate: "0%", is_paused: s.is_paused, pause_reason: s.pause_reason,
  } as InboxHealth));

  const totalBounce = allInboxes.reduce((a, h) => a + parseFloat(h.bounce_rate), 0) / Math.max(allInboxes.length, 1);
  const totalSpam   = allInboxes.reduce((a, h) => a + parseFloat(h.spam_rate), 0) / Math.max(allInboxes.length, 1);
  const totalSent   = status.reduce((a, s) => a + s.sent_today, 0);
  const totalLimit  = status.reduce((a, s) => a + s.daily_limit, 0);
  const avgWarmup   = Math.round(allInboxes.reduce((a, h) => a + h.warmup_week, 0) / Math.max(allInboxes.length, 1));

  const handleToggle = async (email: string, isPaused: boolean) => {
    setToggling(email);
    try {
      if (isPaused) await api.inbox.resume(email);
      else await api.inbox.pause(email);
    } catch {}
    setToggling(null);
  };

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "16px 18px 14px", borderBottom: "1px solid var(--border)" }}>
        <p className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
          Deliverability
        </p>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "14px 18px" }}>
        {/* Health score */}
        <HealthScore bounce={totalBounce} spam={totalSpam} />

        {/* Metrics grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
          {[
            { icon: TrendingDown, label: "Bounce Rate", value: `${totalBounce.toFixed(2)}%`, ok: totalBounce <= 5, varName: "red" },
            { icon: AlertCircle,  label: "Spam Rate",   value: `${totalSpam.toFixed(3)}%`,   ok: totalSpam <= 0.1, varName: "red" },
            { icon: SendHorizonal, label: "Sent Today", value: `${totalSent}/${totalLimit}`,  ok: true, varName: "accent" },
            { icon: Thermometer,  label: "Avg Warmup",  value: `Week ${avgWarmup}`,           ok: true, varName: "green" },
          ].map(m => (
            <div key={m.label} style={{
              padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 9,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
                <m.icon size={12} style={{ color: m.ok ? "var(--text-4)" : "var(--red)" }} />
                <span style={{ fontSize: 11, color: "var(--text-4)" }}>{m.label}</span>
              </div>
              <span className="font-mono" style={{ fontSize: 14.5, fontWeight: 600, color: m.ok ? "var(--text-1)" : "var(--red)" }}>
                {m.value}
              </span>
            </div>
          ))}
        </div>

        {/* Warmup progress for first inbox */}
        {avgWarmup > 0 && (
          <div style={{ marginBottom: 14, padding: "12px", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 9 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
              <Thermometer size={12} style={{ color: "var(--text-4)" }} />
              <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)" }}>
                Warmup Progress
              </span>
            </div>
            <WarmupProgress week={avgWarmup} />
          </div>
        )}

        {/* Per-inbox */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {allInboxes.map(h => {
            const st = statusMap[h.inbox];
            const paused = h.is_paused;
            const bounce = parseFloat(h.bounce_rate);
            const isUnhealthy = bounce > 5;

            return (
              <div key={h.inbox} style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "9px 12px", borderRadius: 9,
                background: paused ? "var(--red-bg)" : "var(--surface-2)",
                border: `1px solid ${paused ? "var(--red-ring)" : "var(--border)"}`,
              }}>
                <span className={`status-dot ${paused ? "red" : isUnhealthy ? "amber" : "green"}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p className="font-mono" style={{ fontSize: 11.5, color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {h.inbox}
                  </p>
                  {paused && h.pause_reason && (
                    <p style={{ fontSize: 10.5, color: "var(--red)" }}>{h.pause_reason}</p>
                  )}
                </div>
                {st && (
                  <span className="font-mono" style={{ fontSize: 11, color: "var(--text-4)", flexShrink: 0 }}>
                    {st.sent_today}/{st.daily_limit}
                  </span>
                )}
                <button
                  onClick={() => handleToggle(h.inbox, paused)}
                  disabled={toggling === h.inbox}
                  title={paused ? "Resume inbox" : "Pause inbox"}
                  style={{
                    padding: "4px 8px", borderRadius: 6, fontSize: 11, cursor: "pointer",
                    background: paused ? "var(--green-bg)" : "var(--surface-3)",
                    color: paused ? "var(--green)" : "var(--text-3)",
                    border: `1px solid ${paused ? "var(--green-ring)" : "var(--border)"}`,
                    display: "flex", alignItems: "center", gap: 4,
                    opacity: toggling === h.inbox ? 0.5 : 1,
                  }}
                >
                  {paused ? <Play size={10} /> : <Pause size={10} />}
                  {paused ? "Resume" : "Pause"}
                </button>
              </div>
            );
          })}
          {allInboxes.length === 0 && (
            <p style={{ fontSize: 12.5, color: "var(--text-4)", textAlign: "center", padding: "20px 0" }}>
              No inboxes configured
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
