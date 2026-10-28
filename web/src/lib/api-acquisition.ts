import { request } from "@/lib/api";

export type ProviderStatus = {
  provider_id: string;
  enabled: boolean;
  configured: boolean;
  capabilities: string[];
  requires_credentials: boolean;
  problems: string[];
};

export type AcquisitionJobEvent = {
  at: string | null;
  kind: string;
  message: string;
  data: Record<string, unknown>;
};

export type AcquisitionJob = {
  id: string;
  provider_id: string;
  source_id: string | null;
  url: string;
  capability: string;
  status: string;
  scan_run_id: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  attempt: number;
  max_attempts: number;
  retryable: boolean;
  error: string | null;
  record_count: number;
  diagnostics: Record<string, unknown>;
  cancel_requested: boolean;
};

export type AcquisitionJobDetail = AcquisitionJob & { events: AcquisitionJobEvent[] };

export type AcquisitionAnalytics = {
  jobs_total: number;
  by_status: Record<string, number>;
  by_provider: Record<string, Record<string, number>>;
  records_produced: number;
  jobs_retried: number;
  avg_duration_s: number | null;
  p95_duration_s: number | null;
};

export const acquisitionApi = {
  providers: () => request<{ providers: ProviderStatus[] }>("/api/acquisition/providers"),
  analytics: () => request<AcquisitionAnalytics>("/api/acquisition/analytics"),
  jobs: (opts?: { status?: string; providerId?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts?.status) params.set("status", opts.status);
    if (opts?.providerId) params.set("provider_id", opts.providerId);
    if (opts?.limit) params.set("limit", String(opts.limit));
    return request<{ jobs: AcquisitionJob[] }>(`/api/acquisition/jobs?${params}`);
  },
  job: (id: string) => request<AcquisitionJobDetail>(`/api/acquisition/jobs/${id}`),
  createJob: (body: { provider_id: string; url: string; source_id?: string; capability?: string; extra?: Record<string, unknown> }) =>
    request<AcquisitionJob>("/api/acquisition/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  cancelJob: (id: string) => request<{ cancelled: boolean }>(`/api/acquisition/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<AcquisitionJob>(`/api/acquisition/jobs/${id}/retry`, { method: "POST" }),
};
