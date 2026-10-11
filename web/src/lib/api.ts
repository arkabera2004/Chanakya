export type Lead = {
  id: string;
  title: string;
  organisation: string | null;
  department: string | null;
  state: string | null;
  platform: string;
  tender_id: string | null;
  ref_no: string | null;
  category: string | null;
  opportunity_type: string;
  status: string;
  priority: string | null;
  score: number | null;
  decision: string | null;
  eligibility: string | null;
  review_status: string;
  published_at: string | null;
  closing_at: string | null;
  closing_label: string | null;
  value_inr: number | null;
  emd_inr: number | null;
  summary: string | null;
  why: string | null;
  next_action: string | null;
  solution: string | null;
  capabilities: { id: string; score: number; offering?: string | null }[];
  reasons: string[];
  flags: string[];
  confidence: number | null;
  confidence_basis: string | null;
  analysis_state: string | null;
  official_url: string | null;
  evidence_line: string | null;
};

export type Finding = {
  criterion: string;
  text: string;
  assessment: string;
  explicit: boolean;
  source: string;
  value: number | null;
  unit: string | null;
};

export type LeadDetail = Lead & {
  description: string | null;
  score_breakdown: {
    total?: number;
    priority?: string;
    components?: Record<string, { score: number; reason: string }>;
    flags?: string[];
  };
  rule_score: number | null;
  semantic_score: number | null;
  data_completeness: number | null;
  explicit_requirements: string[];
  inferred_requirements: string[];
  eligibility_blockers: string[];
  technical_risks: string[];
  missing_information: string[];
  deliverables: string[];
  technologies: string[];
  evidence: string[];
  analysis_notes: string[];
  findings: Finding[];
  changes: { type: string; field: string | null; old: string | null; new: string | null; at: string | null }[];
};

export type RunRow = {
  id: string;
  status: string;
  trigger: string;
  dry_run: boolean;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  new: number;
  updated: number;
  assessed: number;
  rejected: number;
  candidates: number;
  strong: number;
  low: number;
  priorities: { p1: number; p2: number; p3: number; p4: number } | null;
  llm_skipped_budget: number;
  deep_deferred: number;
  budget_blocked_reasons: string[];
  triage_calls: number;
  deep_calls: number;
  llm_cost_usd: number;
  llm_mode: string | null;
  coverage_notes: string[];
  sources: Record<string, { status?: string; seen?: number; new?: number; error?: string }>;
};

export type SourceRow = {
  id: string;
  name: string;
  type: string;
  health: string;
  enabled: boolean;
  verification_status: string;
  last_error: string | null;
  consecutive_failures: number;
  high_water_mark?: string | null;
  pages?: number | null;
  caption?: string | null;
  restricted?: boolean;
};

export type ConfiguredSource = {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  verification_status: string;
  caption?: string | null;
};

export type RestrictedPortal = {
  id: string;
  name: string;
  reason: string;
};

export type Brief = {
  /** True when the desk runs on the synthetic inbox and the simulated model. */
  demo: boolean;
  email: { recipients: number; digest: "daily" | "weekly"; alert_on_new_p1: boolean; provider: string };
  llm_mode: string;
  company: string;
  counts: {
    notices: number;
    active: number;
    p1: number;
    p2: number;
    p3: number;
    rejected: number;
    shortlisted: number;
  };
  last_run: RunRow | null;
  leads: Lead[];
  sources: SourceRow[];
  configured_sources: ConfiguredSource[];
  restricted_portals?: RestrictedPortal[];
  vira: { mood: Mood; greeting: string; suggestions: string[] };
};

export type Mood = "neutral" | "happy" | "curious" | "focused";

export type TraceEvent = {
  at: string;
  kind: string;
  message: string;
  opportunity_id?: string;
  priority?: string | null;
  decision?: string | null;
  title?: string;
};

export type Job = {
  id: string;
  kind?: string;
  status: string;
  stage: string | null;
  events: TraceEvent[];
  error: string | null;
  run_id: string | null;
  stats: RunRow | null;
};

export type WatchStatus = {
  running: boolean;
  mode: string | null;
  run_id: string | null;
  source_id: string | null;
  source_name: string | null;
  pages: number | null;
  notices_stored: number | null;
  started_at: string | null;
  pause_s: number | null;
  accepted?: boolean;
};

export type ViraReply = {
  reply: string;
  mood: Mood;
  suggestions: string[];
  leads: Lead[];
};

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  brief: () => request<Brief>("/api/brief"),
  leads: (
    opts?: {
      q?: string;
      priority?: string;
      includeRejected?: boolean;
      closingWithinDays?: number;
      capability?: string;
      state?: string;
      reviewStatus?: string;
      limit?: number;
      offset?: number;
    },
  ) => {
    const params = new URLSearchParams();
    if (opts?.q) params.set("q", opts.q);
    if (opts?.priority) params.set("priority", opts.priority);
    if (opts?.includeRejected) params.set("include_rejected", "true");
    if (opts?.closingWithinDays) params.set("closing_within_days", String(opts.closingWithinDays));
    if (opts?.capability) params.set("capability", opts.capability);
    if (opts?.state) params.set("state", opts.state);
    if (opts?.reviewStatus) params.set("review_status", opts.reviewStatus);
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.offset) params.set("offset", String(opts.offset));
    return request<{ total: number; opportunities: Lead[] }>(`/api/opportunities?${params}`);
  },
  lead: (id: string) => request<LeadDetail>(`/api/opportunities/${id}`),
  review: (id: string, body: { status?: string; label?: string; note?: string }) =>
    request<Lead>(`/api/opportunities/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  runs: () => request<{ runs: RunRow[] }>("/api/runs"),
  job: () => request<{ job: Job | null }>("/api/job"),
  start: () => request<{ job: Job }>("/api/runs", { method: "POST" }),
  watch: () => request<WatchStatus>("/api/watch"),
  watchAction: (action: "start" | "stop") =>
    request<WatchStatus>("/api/watch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }),
  analyze: (scope: "changed" | "all" = "changed") =>
    request<{ job: Job }>("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope }),
    }),
  events: (after?: string) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    return request<{ run_id: string | null; events: TraceEvent[] }>(`/api/events?${params}`);
  },
  vira: (message: string, opportunityId?: string) =>
    request<ViraReply>("/api/vira", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, opportunity_id: opportunityId ?? null }),
    }),
};
