// v2 - https fixed
const API_BASE = "https://backend-api-production-a8fb.up.railway.app/api/v1";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
      ...options?.headers,
    },
    next: { revalidate: 30 },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export const api = {
  leads: {
    list: (params?: Record<string, string | number>) => {
      const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
      return apiFetch<{ leads: Lead[]; total: number }>(`/leads${qs}`);
    },
    stats: () => apiFetch<LeadStats>("/leads/stats/summary"),
  },
  campaigns: {
    summary: (days = 30) => apiFetch<CampaignSummary>(`/campaigns/summary?days=${days}`),
    daily: (days = 14) => apiFetch<{ metrics: DayMetric[] }>(`/campaigns/metrics/daily?days=${days}`),
    subjectLines: () => apiFetch<{ subject_lines: SubjectLine[] }>("/campaigns/subject-lines/best"),
    industries: () => apiFetch<{ industries: IndustryMetric[] }>("/campaigns/industries/best"),
    sources: () => apiFetch<{ sources: SourceMetric[] }>("/campaigns/sources/best"),
  },
  inbox: {
    status: () => apiFetch<{ inboxes: InboxStatus[] }>("/inbox/status"),
    health: () => apiFetch<{ health: InboxHealth[] }>("/inbox/health"),
    pause: (email: string) => apiFetch(`/inbox/${encodeURIComponent(email)}/pause`, { method: "POST" }),
    resume: (email: string) => apiFetch(`/inbox/${encodeURIComponent(email)}/resume`, { method: "POST" }),
  },
  painSignals: {
    list: (params?: Record<string, string | number>) => {
      const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
      return apiFetch<{ signals: PainSignal[]; total: number }>(`/pain-signals${qs}`);
    },
    stats: () => apiFetch<PainSignalStats>("/pain-signals/stats"),
  },
  painSignalOutreach: {
    list: (params?: Record<string, string | number | boolean>) => {
      const qs = params ? "?" + new URLSearchParams(
        Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))
      ).toString() : "";
      return apiFetch<{ items: OutreachQueueItem[]; total: number; page: number; per_page: number }>(
        `/pain-signal-outreach${qs}`
      );
    },
    stats: () => apiFetch<OutreachQueueStats>("/pain-signal-outreach/stats"),
    get: (id: string) => apiFetch<OutreachQueueItemDetail>(`/pain-signal-outreach/${id}`),
    update: (id: string, payload: Partial<OutreachQueueUpdate>) =>
      apiFetch<OutreachQueueItemDetail>(`/pain-signal-outreach/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    regenerate: (id: string) =>
      apiFetch<OutreachQueueItemDetail>(`/pain-signal-outreach/${id}/regenerate-message`, {
        method: "POST",
      }),
    copyReady: (id: string) => apiFetch<OutreachCopyReady>(`/pain-signal-outreach/${id}/copy-ready`),
  },
  activity: {
    feed: (limit = 50) => apiFetch<{ events: ActivityEvent[] }>(`/activity/feed?limit=${limit}`),
  },
};

// Types
export interface Lead {
  id: string; company_name: string; website?: string; industry?: string;
  location?: string; source: string; score?: number;
  automation_maturity?: string; scraped_at?: string;
}
export interface LeadStats {
  total_leads: number; qualified_leads: number; in_queue: number;
  top_industries: { industry: string; count: number }[];
  by_source: { source: string; count: number }[];
}
export interface CampaignSummary {
  period_days: number; total_sent: number; total_replies: number;
  interested: number; in_queue: number; reply_rate: number; positive_rate: number;
}
export interface DayMetric {
  date: string; emails_sent: number; replies: number; interested: number;
  reply_rate: number; positive_rate: number; bounces: number;
}
export interface SubjectLine {
  subject: string; variant: string; sent: number; replies: number; reply_rate: number;
}
export interface IndustryMetric {
  industry: string; sent: number; replies: number; interested: number; reply_rate: number;
}
export interface SourceMetric {
  source: string; sent: number; replies: number; reply_rate: number;
}
export interface InboxStatus {
  email: string; daily_limit: number; sent_today: number;
  remaining: number; warmup_week: number; is_paused: boolean;
  pause_reason?: string; can_send: boolean;
}
export interface InboxHealth {
  inbox: string; warmup_week: number; daily_limit: number; sent_today: number;
  bounce_rate: string; spam_rate: string; reply_rate: string;
  is_paused: boolean; pause_reason?: string; last_sent_at?: string;
}
export interface PainSignal {
  id: string; source: string; source_url?: string; content: string;
  keywords_matched?: string[]; industry?: string; problem_desc?: string;
  automation_opp?: string; lead_potential?: number; processed: boolean; scraped_at: string;
}
export interface PainSignalStats {
  total: number; qualified: number;
  by_source: { source: string; count: number }[];
}
export interface ActivityEvent {
  id: string; event_type: string; entity_type?: string;
  entity_id?: string; message: string; metadata?: Record<string, unknown>; created_at: string;
}

// ---- Pain Signal Manual Outreach Queue ----
export type ReviewStatus =
  | "unreviewed" | "reviewing" | "contact_found" | "contact_not_found"
  | "ready_to_send" | "sent" | "archived";
export type OutreachStatus = "not_started" | "draft_ready" | "sent" | "replied" | "closed" | "abandoned";
export type OutreachChannel = "email" | "linkedin" | "contact_form" | "twitter" | "phone" | "other";

export interface OutreachQueueItem {
  id: string;
  pain_signal_id: string;
  source: string;
  source_url?: string;
  author?: string;
  industry?: string;
  problem_desc?: string;
  automation_opp?: string;
  lead_potential?: number;
  target_contact_type?: string;
  personalization_hook?: string;
  suggested_subject?: string;
  email_preview?: string;
  dm_preview?: string;
  recommended_cta?: string;
  manual_company_name?: string;
  manual_contact_name?: string;
  manual_contact_role?: string;
  manual_contact_email?: string;
  has_contact: boolean;
  review_status: ReviewStatus;
  outreach_channel?: OutreachChannel;
  outreach_status: OutreachStatus;
  created_at: string;
  updated_at: string;
  reviewed_at?: string;
  contact_found_at?: string;
  outreach_marked_at?: string;
}

export interface OutreachQueueItemDetail extends OutreachQueueItem {
  suggested_email_message?: string;
  suggested_dm_message?: string;
  ai_reasoning?: string;
  message_model_used?: string;
  manual_contact_phone?: string;
  manual_contact_linkedin?: string;
  manual_website?: string;
  manual_notes?: string;
  pain_signal?: {
    id: string; source: string; source_url?: string; author?: string;
    content: string; keywords_matched?: string[]; industry?: string;
    problem_desc?: string; automation_opp?: string; lead_potential?: number;
    scraped_at: string;
  };
}

export interface OutreachQueueUpdate {
  manual_company_name?: string;
  manual_contact_name?: string;
  manual_contact_role?: string;
  manual_contact_email?: string;
  manual_contact_phone?: string;
  manual_contact_linkedin?: string;
  manual_website?: string;
  manual_notes?: string;
  review_status?: ReviewStatus;
  outreach_channel?: OutreachChannel;
  outreach_status?: OutreachStatus;
}

export interface OutreachQueueStats {
  total: number;
  contacts_found: number;
  by_review_status: { status: string; count: number }[];
  by_outreach_status: { status: string; count: number }[];
}

export interface OutreachCopyReady {
  subject?: string;
  email_message?: string;
  dm_message?: string;
  personalization_hook?: string;
  cta?: string;
  source_url?: string;
}
