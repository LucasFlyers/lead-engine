"use client";
import { useEffect, useState } from "react";
import { Database, Search, Send, MessageSquare, BarChart3, RefreshCw, CheckCircle2, XCircle, AlertCircle, Clock } from "lucide-react";

type ServiceStatus = "healthy" | "degraded" | "down" | "unknown";

interface ServiceState {
  name: string;
  key: string;
  icon: React.ElementType;
  status: ServiceStatus;
  detail?: string;
  latencyMs?: number;
}

const STATUS_META: Record<ServiceStatus, { color: string; bg: string; ring: string; icon: React.ElementType; label: string }> = {
  healthy:  { color: "var(--green)",  bg: "var(--green-bg)",  ring: "var(--green-ring)",  icon: CheckCircle2, label: "Healthy" },
  degraded: { color: "var(--amber)",  bg: "var(--amber-bg)",  ring: "var(--amber-ring)",  icon: AlertCircle,  label: "Degraded" },
  down:     { color: "var(--red)",    bg: "var(--red-bg)",    ring: "var(--red-ring)",    icon: XCircle,      label: "Down" },
  unknown:  { color: "var(--text-4)", bg: "var(--surface-3)", ring: "var(--border)",      icon: Clock,        label: "Unknown" },
};

const DEFAULT_SERVICES: ServiceState[] = [
  { name: "Database",      key: "database",      icon: Database,      status: "unknown" },
  { name: "Scrapers",      key: "scrapers",      icon: Search,        status: "unknown" },
  { name: "Email Sender",  key: "email_sender",  icon: Send,          status: "unknown" },
  { name: "Reply Monitor", key: "reply_monitor", icon: MessageSquare, status: "unknown" },
  { name: "Analytics",     key: "analytics",     icon: BarChart3,     status: "unknown" },
];

async function fetchHealth(apiBase: string): Promise<ServiceState[]> {
  try {
    const t0 = performance.now();
    const res = await fetch(`${apiBase}/health`, { cache: "no-store" });
    const latency = Math.round(performance.now() - t0);
    const data = await res.json();

    return DEFAULT_SERVICES.map(svc => {
      if (svc.key === "database") {
        return {
          ...svc,
          status: data.database === "connected" ? "healthy" : "down",
          detail: data.database === "connected" ? "Connected" : "Unreachable",
          latencyMs: latency,
        };
      }
      // If API responded, other services are reachable
      return { ...svc, status: "healthy" as ServiceStatus, detail: "Reachable", latencyMs: latency };
    });
  } catch {
    return DEFAULT_SERVICES.map(s => ({ ...s, status: "down" as ServiceStatus, detail: "API unreachable" }));
  }
}

export function SystemHealthPanel() {
  const [services, setServices] = useState<ServiceState[]>(DEFAULT_SERVICES);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);
  const [checking, setChecking] = useState(false);

  // Get API base URL — strip /api/v1 suffix if present
  const getApiBase = () => {
    const url = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
    return url.replace(/\/api\/v1\/?$/, "");
  };

  const check = async () => {
    setChecking(true);
    const result = await fetchHealth(getApiBase());
    setServices(result);
    setLastCheck(new Date());
    setChecking(false);
  };

  useEffect(() => { check(); }, []);

  const overallOk = services.every(s => s.status === "healthy");
  const anyDown   = services.some(s => s.status === "down");
  const overall   = anyDown ? "down" : overallOk ? "healthy" : "degraded";

  return (
    <div className="card">
      <div style={{ padding: "16px 18px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className={`status-dot ${overall === "healthy" ? "green" : overall === "degraded" ? "amber" : "red"}`} />
          <span className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
            System Health
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {lastCheck && (
            <span style={{ fontSize: 11, color: "var(--text-4)" }}>
              {lastCheck.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={check}
            disabled={checking}
            title="Refresh health checks"
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: 28, height: 28, borderRadius: 7,
              background: "var(--surface-3)", border: "1px solid var(--border)",
              cursor: "pointer", color: "var(--text-3)",
              opacity: checking ? 0.5 : 1,
            }}
          >
            <RefreshCw size={12} style={{ animation: checking ? "spin-slow 1s linear infinite" : "none" }} />
          </button>
        </div>
      </div>

      <div style={{ padding: "10px 18px 14px", display: "flex", flexDirection: "column", gap: 7, marginTop: 4 }}>
        {services.map(svc => {
          const meta = STATUS_META[svc.status];
          const StatusIcon = meta.icon;
          const SvcIcon = svc.icon;

          return (
            <div key={svc.key} style={{
              display: "flex", alignItems: "center", gap: 11,
              padding: "9px 12px", borderRadius: 9,
              background: "var(--surface-2)", border: "1px solid var(--border)",
            }}>
              <div style={{
                width: 30, height: 30, borderRadius: 7,
                background: meta.bg, border: `1px solid ${meta.ring}`,
                display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
              }}>
                <SvcIcon size={14} style={{ color: meta.color }} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-1)" }}>{svc.name}</p>
                {svc.detail && (
                  <p style={{ fontSize: 11, color: "var(--text-4)", marginTop: 1 }}>{svc.detail}</p>
                )}
              </div>
              {svc.latencyMs !== undefined && (
                <span className="font-mono" style={{ fontSize: 11, color: "var(--text-4)", flexShrink: 0 }}>
                  {svc.latencyMs}ms
                </span>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
                <StatusIcon size={13} style={{ color: meta.color }} />
                <span style={{ fontSize: 12, fontWeight: 500, color: meta.color }}>{meta.label}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
