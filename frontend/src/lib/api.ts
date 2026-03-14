// All API calls go through /api/proxy/* which is a server-side Next.js route
// that adds the API key and forwards to the backend.
// This avoids all CORS and API key baking issues.

const API_BASE = "/api/proxy";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
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
