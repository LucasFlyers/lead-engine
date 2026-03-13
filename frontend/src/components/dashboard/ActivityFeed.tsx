"use client";
import { ActivityEvent } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { UserPlus, Zap, Send, MessageCircle, AlertTriangle, PlayCircle, CheckCircle2 } from "lucide-react";

const EVENT_META: Record<string, { icon: React.ElementType; color: string; label: string }> = {
  lead_scraped:      { icon: UserPlus,     color: "var(--accent)",  label: "Lead scraped" },
  pain_detected:     { icon: Zap,          color: "var(--violet)",  label: "Pain signal" },
  email_sent:        { icon: Send,          color: "var(--green)",   label: "Email sent" },
  reply_received:    { icon: MessageCircle, color: "var(--amber)",   label: "Reply received" },
  inbox_paused:      { icon: AlertTriangle, color: "var(--red)",     label: "Inbox paused" },
  pipeline_start:    { icon: PlayCircle,    color: "var(--text-3)",  label: "Pipeline started" },
  pipeline_complete: { icon: CheckCircle2,  color: "var(--green)",   label: "Pipeline complete" },
};

function timeAgo(ts: string) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }); }
  catch { return "—"; }
}

export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "16px 18px 14px", borderBottom: "1px solid var(--border)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="status-dot green" />
          <span className="font-display" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>
            Live Activity
          </span>
        </div>
        <span style={{ fontSize: 11, color: "var(--text-4)", background: "var(--surface-3)", padding: "2px 8px", borderRadius: 99, border: "1px solid var(--border)" }}>
          {events.length} events
        </span>
      </div>

      {/* Feed */}
      <div style={{ flex: 1, overflowY: "auto", padding: "10px 0" }}>
        {events.length === 0 && (
          <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--text-4)" }}>
            <Send size={24} style={{ margin: "0 auto 8px", opacity: 0.4 }} />
            <p style={{ fontSize: 13 }}>No activity yet</p>
          </div>
        )}
        {events.map((event, i) => {
          const meta = EVENT_META[event.event_type] ?? {
            icon: CheckCircle2, color: "var(--text-3)", label: event.event_type,
          };
          const IconComp = meta.icon;

          return (
            <div
              key={event.id}
              className="fade-up"
              style={{
                display: "flex", gap: 12, padding: "9px 18px",
                animationDelay: `${i * 0.04}s`,
                borderBottom: i < events.length - 1 ? "1px solid var(--border)" : "none",
              }}
            >
              {/* Icon */}
              <div style={{
                width: 30, height: 30, borderRadius: 8, flexShrink: 0,
                background: `color-mix(in srgb, ${meta.color} 12%, transparent)`,
                display: "flex", alignItems: "center", justifyContent: "center",
                marginTop: 1,
              }}>
                <IconComp size={13} style={{ color: meta.color }} />
              </div>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.4, marginBottom: 2 }}>
                  {event.message}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="badge badge-muted" style={{ fontSize: 10.5 }}>{meta.label}</span>
                  <span style={{ fontSize: 10.5, color: "var(--text-4)" }}>{timeAgo(event.created_at)}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
