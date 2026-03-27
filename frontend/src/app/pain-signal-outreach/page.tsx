"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  MessageSquarePlus, ExternalLink, Copy, Check, RefreshCw,
  ChevronDown, Search, X, ArrowUpRight, User, Mail, Phone,
  Linkedin, Globe, FileText, Loader2, AlertCircle,
} from "lucide-react";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { api } from "@/lib/api";
import type {
  OutreachQueueItem, OutreachQueueItemDetail, OutreachQueueStats,
  ReviewStatus, OutreachStatus, OutreachChannel, OutreachQueueUpdate,
} from "@/lib/api";

// ---- Status maps ----
const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  unreviewed:        "Unreviewed",
  reviewing:         "Reviewing",
  contact_found:     "Contact Found",
  contact_not_found: "No Contact",
  ready_to_send:     "Ready",
  sent:              "Sent",
  archived:          "Archived",
};
const REVIEW_STATUS_BADGE: Record<ReviewStatus, string> = {
  unreviewed:        "badge-muted",
  reviewing:         "badge-amber",
  contact_found:     "badge-accent",
  contact_not_found: "badge-muted",
  ready_to_send:     "badge-green",
  sent:              "badge-green",
  archived:          "badge-muted",
};
const OUTREACH_STATUS_LABELS: Record<OutreachStatus, string> = {
  not_started: "Not Started",
  draft_ready: "Draft Ready",
  sent:        "Sent",
  replied:     "Replied",
  closed:      "Closed",
  abandoned:   "Abandoned",
};
const OUTREACH_STATUS_BADGE: Record<OutreachStatus, string> = {
  not_started: "badge-muted",
  draft_ready: "badge-accent",
  sent:        "badge-green",
  replied:     "badge-violet",
  closed:      "badge-green",
  abandoned:   "badge-muted",
};
const SCORE_BADGE = (s?: number | null) =>
  !s ? "badge-muted" : s >= 8 ? "badge-green" : s >= 6 ? "badge-amber" : "badge-muted";
const SOURCE_BADGE: Record<string, string> = {
  reddit:     "badge-violet",
  hackernews: "badge-amber",
  g2:         "badge-accent",
  forum:      "badge-muted",
};

// ---- CopyButton ----
function CopyButton({ text, label = "Copy" }: { text?: string | null; label?: string }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }).catch(() => {/* clipboard permission denied */});
  };
  return (
    <button
      onClick={copy}
      className="btn btn-secondary"
      style={{ fontSize: 12, padding: "4px 10px", display: "flex", alignItems: "center", gap: 5 }}
    >
      {copied
        ? <Check size={12} style={{ color: "var(--green)" }} />
        : <Copy size={12} />}
      {copied ? "Copied!" : label}
    </button>
  );
}

// ---- Section wrapper ----
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <p style={{
        fontSize: 10.5, fontWeight: 700, color: "var(--text-4)",
        textTransform: "uppercase", letterSpacing: "0.07em",
        marginBottom: 12, paddingBottom: 6, borderBottom: "1px solid var(--border)",
      }}>
        {title}
      </p>
      {children}
    </div>
  );
}

// ---- Info row (read-only) ----
function InfoRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", display: "block", marginBottom: 2 }}>
        {label}
      </span>
      <p style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.55, margin: 0, wordBreak: "break-word" }}>
        {value}
      </p>
    </div>
  );
}

// ---- Select field ----
function SelectField({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          background: "var(--surface-2)", border: "1px solid var(--border)",
          borderRadius: 6, padding: "7px 10px", fontSize: 13, color: "var(--text-1)",
          outline: "none", cursor: "pointer",
        }}
      >
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

// ---- Text input field builder ----
function FieldInput({
  label, value, onChange, icon, type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  icon?: React.ReactNode;
  type?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{
        fontSize: 11, fontWeight: 600, color: "var(--text-4)",
        textTransform: "uppercase", letterSpacing: "0.06em",
        display: "flex", alignItems: "center", gap: 5,
      }}>
        {icon}{label}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          background: "var(--surface-2)", border: "1px solid var(--border)",
          borderRadius: 6, padding: "7px 10px", fontSize: 13,
          color: "var(--text-1)", outline: "none", width: "100%", boxSizing: "border-box",
        }}
      />
    </label>
  );
}

// ---- Detail Drawer ----
// `savedItem` tracks the last successfully-saved state so the diff detection
// always compares against what's actually in the DB, not the initial prop.
function DetailDrawer({
  item,
  onClose,
  onUpdated,
}: {
  item: OutreachQueueItemDetail;
  onClose: () => void;
  onUpdated: (updated: OutreachQueueItemDetail) => void;
}) {
  // `current` holds the live detail data (updated by regenerate / save responses)
  const [current, setCurrent] = useState<OutreachQueueItemDetail>(item);

  // `form` holds editable manual fields — initialised from item prop once
  const [form, setForm] = useState({
    manual_company_name:     item.manual_company_name     ?? "",
    manual_contact_name:     item.manual_contact_name     ?? "",
    manual_contact_role:     item.manual_contact_role     ?? "",
    manual_contact_email:    item.manual_contact_email    ?? "",
    manual_contact_phone:    item.manual_contact_phone    ?? "",
    manual_contact_linkedin: item.manual_contact_linkedin ?? "",
    manual_website:          item.manual_website          ?? "",
    manual_notes:            item.manual_notes            ?? "",
    review_status:           item.review_status   as string,
    outreach_channel:        item.outreach_channel ?? "",
    outreach_status:         item.outreach_status  as string,
  });

  // Track the last-saved values to build accurate change diffs
  const savedRef = useRef({ ...form });

  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [regenMsg, setRegenMsg] = useState("");

  const save = async () => {
    setSaving(true);
    setSaveMsg("");
    try {
      // Diff against last-saved state (not original prop) to avoid re-sending unchanged data
      const changed: Partial<OutreachQueueUpdate> = {};
      (Object.keys(form) as (keyof typeof form)[]).forEach((k) => {
        const formVal = form[k] || null;         // empty string → null (clear field)
        const savedVal = savedRef.current[k as keyof typeof savedRef.current] || null;
        if (formVal !== savedVal) {
          (changed as Record<string, unknown>)[k] = formVal;
        }
      });

      if (Object.keys(changed).length === 0) {
        setSaveMsg("No changes");
        setSaving(false);
        setTimeout(() => setSaveMsg(""), 1500);
        return;
      }

      const updated = await api.painSignalOutreach.update(current.id, changed as Partial<OutreachQueueUpdate>);
      setCurrent(updated);
      onUpdated(updated);
      // Sync saved baseline to the new state
      savedRef.current = { ...form };
      setSaveMsg("Saved");
    } catch {
      setSaveMsg("Save failed — try again");
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(""), 2500);
    }
  };

  const regenerate = async () => {
    setRegenerating(true);
    setRegenMsg("");
    try {
      const updated = await api.painSignalOutreach.regenerate(current.id);
      setCurrent(updated);
      onUpdated(updated);
      setRegenMsg("Done");
    } catch {
      setRegenMsg("Failed — try again");
    } finally {
      setRegenerating(false);
      setTimeout(() => setRegenMsg(""), 2500);
    }
  };

  const setField = (key: keyof typeof form) => (v: string) =>
    setForm((f) => ({ ...f, [key]: v }));

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 100, display: "flex" }}>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.45)" }}
      />
      {/* Panel */}
      <div style={{
        position: "absolute", right: 0, top: 0, bottom: 0,
        width: "min(700px, 100vw)",
        background: "var(--surface)", borderLeft: "1px solid var(--border)",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{
          padding: "16px 20px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <MessageSquarePlus size={16} style={{ color: "var(--accent)" }} />
            <span className="font-display" style={{ fontWeight: 700, fontSize: 15, color: "var(--text-1)" }}>
              Outreach Detail
            </span>
            <span className={`badge ${REVIEW_STATUS_BADGE[current.review_status]}`}>
              {REVIEW_STATUS_LABELS[current.review_status]}
            </span>
            <span className={`badge ${OUTREACH_STATUS_BADGE[current.outreach_status]}`}>
              {OUTREACH_STATUS_LABELS[current.outreach_status]}
            </span>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-3)", padding: 4 }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>

          {/* 1 — Source Signal */}
          <Section title="Source Signal">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
              <span className={`badge ${SOURCE_BADGE[current.source] ?? "badge-muted"}`}>
                {current.source}
              </span>
              {current.industry && (
                <span className="badge badge-muted">{current.industry}</span>
              )}
              {current.lead_potential != null && (
                <span className={`badge ${SCORE_BADGE(current.lead_potential)}`}>
                  {current.lead_potential}/10
                </span>
              )}
              {current.author && (
                <span className="badge badge-muted">@{current.author}</span>
              )}
            </div>

            {current.source_url ? (
              <a
                href={current.source_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  fontSize: 12.5, color: "var(--accent)", textDecoration: "none",
                  marginBottom: 12,
                }}
              >
                <ExternalLink size={12} /> Open original post
              </a>
            ) : (
              <p style={{ fontSize: 12, color: "var(--text-4)", marginBottom: 10, fontStyle: "italic" }}>
                No source URL available
              </p>
            )}

            {current.pain_signal?.content && (
              <p style={{
                fontSize: 13, color: "var(--text-3)", lineHeight: 1.6,
                background: "var(--surface-2)", borderRadius: 6, padding: "10px 12px",
                margin: "0 0 10px", whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>
                {current.pain_signal.content.slice(0, 600)}
                {current.pain_signal.content.length > 600 ? "…" : ""}
              </p>
            )}

            <InfoRow label="Problem" value={current.problem_desc} />
            <InfoRow label="Automation Opportunity" value={current.automation_opp} />
          </Section>

          {/* 2 — AI Outreach Suggestions */}
          <Section title="AI Outreach Suggestions">
            {!current.suggested_email_message && !current.suggested_subject && (
              <div style={{
                display: "flex", alignItems: "center", gap: 8,
                background: "var(--surface-2)", borderRadius: 6, padding: "10px 12px",
                marginBottom: 14,
              }}>
                <AlertCircle size={14} style={{ color: "var(--text-4)", flexShrink: 0 }} />
                <p style={{ fontSize: 13, color: "var(--text-4)", margin: 0 }}>
                  No AI suggestions yet — click Regenerate to generate outreach content.
                </p>
              </div>
            )}

            <InfoRow label="Target Contact" value={current.target_contact_type} />
            <InfoRow label="Personalisation Hook" value={current.personalization_hook} />
            <InfoRow label="Recommended CTA" value={current.recommended_cta} />

            {current.suggested_subject && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Email Subject
                  </span>
                  <CopyButton text={current.suggested_subject} label="Copy" />
                </div>
                <p style={{
                  fontSize: 13, color: "var(--text-1)", background: "var(--surface-2)",
                  borderRadius: 6, padding: "8px 10px", margin: 0, fontStyle: "italic",
                  wordBreak: "break-word",
                }}>
                  {current.suggested_subject}
                </p>
              </div>
            )}

            {current.suggested_email_message && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Email Body
                  </span>
                  <CopyButton text={current.suggested_email_message} label="Copy email" />
                </div>
                <pre style={{
                  fontSize: 12.5, color: "var(--text-2)", background: "var(--surface-2)",
                  borderRadius: 6, padding: "10px 12px", margin: 0,
                  whiteSpace: "pre-wrap", fontFamily: "inherit", lineHeight: 1.65,
                  wordBreak: "break-word",
                }}>
                  {current.suggested_email_message}
                </pre>
              </div>
            )}

            {current.suggested_dm_message && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    DM Message
                  </span>
                  <CopyButton text={current.suggested_dm_message} label="Copy DM" />
                </div>
                <pre style={{
                  fontSize: 12.5, color: "var(--text-2)", background: "var(--surface-2)",
                  borderRadius: 6, padding: "10px 12px", margin: 0,
                  whiteSpace: "pre-wrap", fontFamily: "inherit", lineHeight: 1.65,
                  wordBreak: "break-word",
                }}>
                  {current.suggested_dm_message}
                </pre>
                <p style={{ fontSize: 11, color: "var(--text-4)", marginTop: 4 }}>
                  {current.suggested_dm_message.length}/280 chars
                </p>
              </div>
            )}

            {current.ai_reasoning && (
              <p style={{ fontSize: 12, color: "var(--text-4)", fontStyle: "italic", margin: "4px 0 0" }}>
                {current.ai_reasoning}
              </p>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
              <button
                onClick={regenerate}
                disabled={regenerating}
                className="btn btn-secondary"
                style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}
              >
                {regenerating
                  ? <Loader2 size={12} className="spin" />
                  : <RefreshCw size={12} />}
                {regenerating ? "Regenerating…" : "Regenerate suggestions"}
              </button>
              {regenMsg && (
                <span style={{
                  fontSize: 12,
                  color: regenMsg.includes("Failed") ? "var(--red)" : "var(--green)",
                }}>
                  {regenMsg}
                </span>
              )}
            </div>
          </Section>

          {/* 3 — Manual Research */}
          <Section title="Manual Research / Contact">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <FieldInput label="Company Name"  value={form.manual_company_name}     onChange={setField("manual_company_name")} />
              <FieldInput label="Contact Name"  value={form.manual_contact_name}     onChange={setField("manual_contact_name")}  icon={<User size={11} />} />
              <FieldInput label="Role / Title"  value={form.manual_contact_role}     onChange={setField("manual_contact_role")} />
              <FieldInput label="Email"         value={form.manual_contact_email}    onChange={setField("manual_contact_email")}    icon={<Mail size={11} />} type="email" />
              <FieldInput label="Phone"         value={form.manual_contact_phone}    onChange={setField("manual_contact_phone")}    icon={<Phone size={11} />} type="tel" />
              <FieldInput label="LinkedIn URL"  value={form.manual_contact_linkedin} onChange={setField("manual_contact_linkedin")} icon={<Linkedin size={11} />} />
              <FieldInput label="Website"       value={form.manual_website}          onChange={setField("manual_website")}          icon={<Globe size={11} />} />
            </div>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 12 }}>
              <span style={{
                fontSize: 11, fontWeight: 600, color: "var(--text-4)",
                textTransform: "uppercase", letterSpacing: "0.06em",
                display: "flex", alignItems: "center", gap: 5,
              }}>
                <FileText size={11} />Notes
              </span>
              <textarea
                value={form.manual_notes}
                onChange={(e) => setField("manual_notes")(e.target.value)}
                rows={3}
                placeholder="Research notes, context, next steps…"
                style={{
                  background: "var(--surface-2)", border: "1px solid var(--border)",
                  borderRadius: 6, padding: "7px 10px", fontSize: 13, color: "var(--text-1)",
                  outline: "none", resize: "vertical", fontFamily: "inherit",
                  width: "100%", boxSizing: "border-box",
                }}
              />
            </label>
          </Section>

          {/* 4 — Workflow */}
          <Section title="Workflow Status">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
              <SelectField
                label="Review Status"
                value={form.review_status}
                onChange={setField("review_status")}
                options={Object.entries(REVIEW_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l }))}
              />
              <SelectField
                label="Outreach Channel"
                value={form.outreach_channel}
                onChange={setField("outreach_channel")}
                options={[
                  { value: "", label: "—" },
                  { value: "email",        label: "Email" },
                  { value: "linkedin",     label: "LinkedIn" },
                  { value: "contact_form", label: "Contact Form" },
                  { value: "twitter",      label: "Twitter/X" },
                  { value: "phone",        label: "Phone" },
                  { value: "other",        label: "Other" },
                ]}
              />
              <SelectField
                label="Outreach Status"
                value={form.outreach_status}
                onChange={setField("outreach_status")}
                options={Object.entries(OUTREACH_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l }))}
              />
            </div>
          </Section>

          <div style={{ height: 24 }} />
        </div>

        {/* Footer */}
        <div style={{
          padding: "14px 20px", borderTop: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10,
          flexShrink: 0,
        }}>
          {saveMsg && (
            <span style={{
              fontSize: 12.5,
              color: saveMsg === "Saved" ? "var(--green)"
                   : saveMsg === "No changes" ? "var(--text-4)"
                   : "var(--red)",
            }}>
              {saveMsg}
            </span>
          )}
          <button onClick={onClose} className="btn btn-secondary" style={{ fontSize: 13 }}>
            Close
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="btn btn-primary"
            style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}
          >
            {saving && <Loader2 size={13} className="spin" />}
            Save changes
          </button>
        </div>
      </div>

      <style>{`
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

// ---- Filter select ----
function FilterSelect({ value, onChange, placeholder, options }: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  options: { value: string; label: string }[];
}) {
  return (
    <div style={{ position: "relative" }}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          appearance: "none", background: "var(--surface-2)",
          border: "1px solid var(--border)", borderRadius: 6,
          padding: "7px 28px 7px 10px", fontSize: 13,
          color: value ? "var(--text-1)" : "var(--text-4)",
          outline: "none", cursor: "pointer",
        }}
      >
        <option value="">{placeholder}</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={12} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
    </div>
  );
}

// ---- Stat group ----
function StatGroup({ title, items }: {
  title: string;
  items: { label: string; count: number; badge: string }[];
}) {
  const visible = items.filter((i) => i.count > 0);
  if (visible.length === 0) return null;
  return (
    <div>
      <p style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--text-4)", marginBottom: 8 }}>
        {title}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {visible.map((i) => (
          <span key={i.label} className={`badge ${i.badge}`} style={{ fontSize: 11.5 }}>
            {i.label} · {i.count}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---- Queue row ----
function QueueRow({
  item,
  last,
  onOpen,
  onQuickUpdate,
}: {
  item: OutreachQueueItem;
  last: boolean;
  onOpen: () => void;
  onQuickUpdate: (patch: Partial<OutreachQueueUpdate>) => void;
}) {
  const [copyDone, setCopyDone] = useState(false);

  const copyEmail = (e: React.MouseEvent) => {
    e.stopPropagation();
    const text = item.email_preview;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopyDone(true);
      setTimeout(() => setCopyDone(false), 1500);
    }).catch(() => {});
  };

  const problemText = item.problem_desc
    ? item.problem_desc.length > 130
      ? item.problem_desc.slice(0, 128) + "…"
      : item.problem_desc
    : null;

  return (
    <div
      onClick={onOpen}
      className="hover-card"
      style={{
        padding: "13px 18px",
        borderBottom: last ? "none" : "1px solid var(--border)",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
        {/* Left: signal info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Badge row */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 7 }}>
            <span className={`badge ${SOURCE_BADGE[item.source] ?? "badge-muted"}`}>
              {item.source}
            </span>
            <span className={`badge ${REVIEW_STATUS_BADGE[item.review_status]}`}>
              {REVIEW_STATUS_LABELS[item.review_status]}
            </span>
            <span className={`badge ${OUTREACH_STATUS_BADGE[item.outreach_status]}`}>
              {OUTREACH_STATUS_LABELS[item.outreach_status]}
            </span>
            {item.lead_potential != null && (
              <span className={`badge ${SCORE_BADGE(item.lead_potential)}`}>
                {item.lead_potential}/10
              </span>
            )}
            {item.industry && (
              <span className="badge badge-muted">{item.industry}</span>
            )}
            {item.has_contact && (
              <span className="badge badge-green">Contact saved</span>
            )}
          </div>

          {/* Problem description */}
          {problemText && (
            <p style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.45, margin: "0 0 6px" }}>
              {problemText}
            </p>
          )}

          {/* Sub-info */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            {item.target_contact_type && (
              <span style={{ fontSize: 11.5, color: "var(--text-4)" }}>
                → {item.target_contact_type}
              </span>
            )}
            {item.manual_contact_name && (
              <span style={{ fontSize: 11.5, color: "var(--accent)" }}>
                <User size={10} style={{ display: "inline", marginRight: 3 }} />
                {item.manual_contact_name}
                {item.manual_contact_email && ` · ${item.manual_contact_email}`}
              </span>
            )}
          </div>
        </div>

        {/* Right: actions — stop click propagation to avoid opening drawer */}
        <div
          onClick={(e) => e.stopPropagation()}
          style={{ display: "flex", flexDirection: "column", gap: 6, flexShrink: 0, alignItems: "flex-end" }}
        >
          {item.source_url && (
            <a
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-secondary"
              style={{ fontSize: 11.5, padding: "4px 9px", display: "flex", alignItems: "center", gap: 4, textDecoration: "none" }}
            >
              <ArrowUpRight size={11} /> Open post
            </a>
          )}
          {item.email_preview && (
            <button
              onClick={copyEmail}
              className="btn btn-secondary"
              style={{ fontSize: 11.5, padding: "4px 9px", display: "flex", alignItems: "center", gap: 4 }}
            >
              {copyDone
                ? <Check size={11} style={{ color: "var(--green)" }} />
                : <Copy size={11} />}
              {copyDone ? "Copied" : "Copy email"}
            </button>
          )}
          {item.review_status === "unreviewed" && (
            <button
              onClick={() => onQuickUpdate({ review_status: "reviewing" })}
              className="btn btn-secondary"
              style={{ fontSize: 11.5, padding: "4px 9px" }}
            >
              Start review
            </button>
          )}
          {item.review_status === "ready_to_send" && item.outreach_status !== "sent" && (
            <button
              onClick={() => onQuickUpdate({ outreach_status: "sent", review_status: "sent" })}
              className="btn btn-primary"
              style={{ fontSize: 11.5, padding: "4px 9px" }}
            >
              Mark sent
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Main page ----
export default function PainSignalOutreachPage() {
  const [items, setItems] = useState<OutreachQueueItem[]>([]);
  const [stats, setStats] = useState<OutreachQueueStats | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [reviewFilter, setReviewFilter] = useState("");
  const [outreachFilter, setOutreachFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [hasContactFilter, setHasContactFilter] = useState("");
  const [minScore, setMinScore] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Detail drawer
  const [detailItem, setDetailItem] = useState<OutreachQueueItemDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Debounce search: wait 350ms after last keystroke before querying
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string | number | boolean> = { per_page: 100 };
      if (reviewFilter)     params.review_status  = reviewFilter;
      if (outreachFilter)   params.outreach_status = outreachFilter;
      if (sourceFilter)     params.source = sourceFilter;
      if (debouncedSearch)  params.search = debouncedSearch;
      if (hasContactFilter) params.has_contact = hasContactFilter === "yes";
      if (minScore)         params.min_score = Number(minScore);

      const [res, statsRes] = await Promise.all([
        api.painSignalOutreach.list(params),
        api.painSignalOutreach.stats(),
      ]);
      setItems(res.items);
      setTotal(res.total);
      setStats(statsRes);
    } catch {
      setError("Failed to load outreach queue — check your connection and try again.");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [reviewFilter, outreachFilter, sourceFilter, debouncedSearch, hasContactFilter, minScore]);

  useEffect(() => { loadItems(); }, [loadItems]);

  const openDetail = async (id: string) => {
    setDetailError(null);
    setDetailLoading(true);
    setDetailItem(null);
    try {
      const d = await api.painSignalOutreach.get(id);
      setDetailItem(d);
    } catch {
      setDetailError("Failed to load detail — please try again.");
      setDetailLoading(false);
      return;
    }
    setDetailLoading(false);
  };

  const closeDetail = () => {
    setDetailItem(null);
    setDetailError(null);
  };

  const onUpdated = (updated: OutreachQueueItemDetail) => {
    setDetailItem(updated);
    setItems((prev) => prev.map((i) => (i.id === updated.id ? { ...i, ...updated } : i)));
  };

  const quickUpdate = async (id: string, patch: Partial<OutreachQueueUpdate>) => {
    try {
      const updated = await api.painSignalOutreach.update(id, patch);
      setItems((prev) => prev.map((i) => (i.id === id ? { ...i, ...updated } : i)));
      if (detailItem?.id === id) setDetailItem(updated);
    } catch {
      /* Quick-update failures are silent — row retains old state */
    }
  };

  const hasFilters = !!(reviewFilter || outreachFilter || sourceFilter || search || hasContactFilter || minScore);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>

          {/* Header */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
            <MessageSquarePlus size={18} style={{ color: "var(--accent)" }} />
            <h1
              className="font-display"
              style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.02em" }}
            >
              Manual Outreach Queue
            </h1>
            {stats != null && (
              <>
                <span className="badge badge-accent">{stats.total} items</span>
                {stats.contacts_found > 0 && (
                  <span className="badge badge-green">{stats.contacts_found} contacts found</span>
                )}
              </>
            )}
          </div>

          {/* Error banner */}
          {error && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
              background: "var(--red-bg, #fef2f2)", border: "1px solid var(--red-ring, #fecaca)",
              borderRadius: 8, marginBottom: 16, color: "var(--red, #dc2626)",
            }}>
              <AlertCircle size={15} />
              <span style={{ fontSize: 13 }}>{error}</span>
              <button onClick={loadItems} style={{ marginLeft: "auto", fontSize: 12, color: "inherit", background: "none", border: "none", cursor: "pointer", textDecoration: "underline" }}>
                Retry
              </button>
            </div>
          )}

          {/* Stats bar */}
          {stats && (
            <div className="card" style={{ padding: "14px 18px", marginBottom: 16, display: "flex", flexWrap: "wrap", gap: 20 }}>
              <StatGroup
                title="By Review"
                items={stats.by_review_status.map((s) => ({
                  label: REVIEW_STATUS_LABELS[s.status as ReviewStatus] ?? s.status,
                  count: s.count,
                  badge: REVIEW_STATUS_BADGE[s.status as ReviewStatus] ?? "badge-muted",
                }))}
              />
              <div style={{ width: 1, background: "var(--border)", flexShrink: 0 }} />
              <StatGroup
                title="By Outreach"
                items={stats.by_outreach_status.map((s) => ({
                  label: OUTREACH_STATUS_LABELS[s.status as OutreachStatus] ?? s.status,
                  count: s.count,
                  badge: OUTREACH_STATUS_BADGE[s.status as OutreachStatus] ?? "badge-muted",
                }))}
              />
            </div>
          )}

          {/* Filters */}
          <div
            className="card"
            style={{ padding: "12px 14px", marginBottom: 16, display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}
          >
            {/* Search */}
            <div style={{ position: "relative", flex: "1 1 200px", minWidth: 160 }}>
              <Search size={13} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)" }} />
              <input
                placeholder="Search problem, industry, company…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  width: "100%", paddingLeft: 30, paddingRight: 10,
                  paddingTop: 7, paddingBottom: 7,
                  background: "var(--surface-2)", border: "1px solid var(--border)",
                  borderRadius: 6, fontSize: 13, color: "var(--text-1)", outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            <FilterSelect
              value={reviewFilter}
              onChange={setReviewFilter}
              placeholder="Review status"
              options={Object.entries(REVIEW_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l }))}
            />
            <FilterSelect
              value={outreachFilter}
              onChange={setOutreachFilter}
              placeholder="Outreach status"
              options={Object.entries(OUTREACH_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l }))}
            />
            <FilterSelect
              value={sourceFilter}
              onChange={setSourceFilter}
              placeholder="Source"
              options={["reddit", "g2", "forum", "hackernews", "review"].map((v) => ({ value: v, label: v }))}
            />
            <FilterSelect
              value={hasContactFilter}
              onChange={setHasContactFilter}
              placeholder="Contact"
              options={[
                { value: "yes", label: "Has contact" },
                { value: "no",  label: "No contact"  },
              ]}
            />
            <input
              type="number"
              min={0}
              max={10}
              placeholder="Min score"
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              style={{
                width: 90, padding: "7px 10px",
                background: "var(--surface-2)", border: "1px solid var(--border)",
                borderRadius: 6, fontSize: 13, color: "var(--text-1)", outline: "none",
              }}
            />
            {hasFilters && (
              <button
                onClick={() => { setReviewFilter(""); setOutreachFilter(""); setSourceFilter(""); setSearch(""); setHasContactFilter(""); setMinScore(""); }}
                className="btn btn-secondary"
                style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}
              >
                <X size={11} /> Clear filters
              </button>
            )}
          </div>

          {/* Table */}
          <div className="card" style={{ overflow: "hidden" }}>
            <div style={{
              padding: "13px 18px 11px", borderBottom: "1px solid var(--border)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <span className="font-display" style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-1)" }}>
                {loading ? "Loading…" : `${total} item${total !== 1 ? "s" : ""}`}
              </span>
            </div>

            {loading ? (
              <div style={{ padding: 48, display: "flex", justifyContent: "center" }}>
                <Loader2 size={22} className="spin" style={{ color: "var(--text-4)" }} />
              </div>
            ) : items.length === 0 ? (
              <div style={{ padding: "48px 24px", textAlign: "center" }}>
                <MessageSquarePlus size={28} style={{ color: "var(--text-4)", margin: "0 auto 12px" }} />
                <p style={{ fontSize: 14, fontWeight: 600, color: "var(--text-2)", marginBottom: 6 }}>
                  {hasFilters ? "No items match these filters" : "Queue is empty"}
                </p>
                <p style={{ fontSize: 13, color: "var(--text-4)", maxWidth: 380, margin: "0 auto" }}>
                  {hasFilters
                    ? "Try removing some filters or broadening the search."
                    : "Qualified pain signals will automatically appear here after the next scraper run. Each item comes with AI-generated outreach suggestions ready to use."}
                </p>
                {hasFilters && (
                  <button
                    onClick={() => { setReviewFilter(""); setOutreachFilter(""); setSourceFilter(""); setSearch(""); setHasContactFilter(""); setMinScore(""); }}
                    className="btn btn-secondary"
                    style={{ marginTop: 16, fontSize: 13 }}
                  >
                    Clear filters
                  </button>
                )}
              </div>
            ) : (
              <div style={{ padding: "4px 0" }}>
                {items.map((item, i) => (
                  <QueueRow
                    key={item.id}
                    item={item}
                    last={i === items.length - 1}
                    onOpen={() => openDetail(item.id)}
                    onQuickUpdate={(patch) => quickUpdate(item.id, patch)}
                  />
                ))}
              </div>
            )}
          </div>

        </div>
      </div>

      {/* Detail loading overlay */}
      {detailLoading && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 100,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "rgba(0,0,0,0.35)",
        }}>
          <Loader2 size={28} className="spin" style={{ color: "var(--accent)" }} />
        </div>
      )}

      {/* Detail error toast */}
      {detailError && !detailLoading && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 101,
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: 10, padding: "14px 18px",
          display: "flex", alignItems: "center", gap: 10, boxShadow: "0 4px 20px rgba(0,0,0,0.15)",
        }}>
          <AlertCircle size={15} style={{ color: "var(--red, #dc2626)", flexShrink: 0 }} />
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>{detailError}</span>
          <button
            onClick={() => setDetailError(null)}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-4)", padding: 2 }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Detail drawer */}
      {detailItem && !detailLoading && (
        <DetailDrawer
          item={detailItem}
          onClose={closeDetail}
          onUpdated={onUpdated}
        />
      )}

      <style>{`
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
