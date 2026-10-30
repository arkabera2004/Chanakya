import { request } from "@/lib/api";

export type Website = {
  id: string;
  name: string;
  base_url: string;
  category: string;
  description: string | null;
  enabled: boolean;
  crawl_config: Record<string, unknown>;
  created_at: string | null;
  last_crawl_at: string | null;
  last_crawl_status: string | null;
};

export type WebsiteCrawlJob = {
  id: string;
  website_id: string;
  status: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  pages_discovered: number;
  pages_processed: number;
  pages_failed: number;
  documents_downloaded: number;
  tenders_extracted: number;
  error: string | null;
  cancel_requested: boolean;
  pause_requested: boolean;
};

export type CreateWebsiteInput = {
  name: string;
  base_url: string;
  category: string;
  description?: string;
  enabled?: boolean;
  crawl_config?: Record<string, unknown>;
};

export type UpdateWebsiteInput = Partial<CreateWebsiteInput>;

export type WebsitePage = {
  id: string;
  url: string;
  depth: number;
  status: string;
  http_status: number | null;
  content_type: string | null;
  error: string | null;
  discovered_at: string | null;
};

export type WebsiteDocument = {
  id: string;
  source_page_url: string;
  document_url: string;
  local_path: string | null;
  sha256: string | null;
  content_type: string | null;
  status: string;
  extraction_error: string | null;
  extracted_text: string | null;
  created_at: string | null;
};

export const websitesApi = {
  list: () => request<{ websites: Website[] }>("/api/websites"),
  create: (body: CreateWebsiteInput) =>
    request<Website>("/api/websites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  get: (id: string) => request<Website>(`/api/websites/${id}`),
  update: (id: string, patch: UpdateWebsiteInput) =>
    request<Website>(`/api/websites/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  remove: (id: string) => request<{ deleted: boolean }>(`/api/websites/${id}`, { method: "DELETE" }),

  createCrawlJob: (id: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs`, { method: "POST" }),
  listCrawlJobs: (id: string) =>
    request<{ crawl_jobs: WebsiteCrawlJob[] }>(`/api/websites/${id}/crawl-jobs`),
  getCrawlJob: (id: string, jobId: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs/${jobId}`),
  cancelCrawlJob: (id: string, jobId: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs/${jobId}/cancel`, { method: "POST" }),
  pauseCrawlJob: (id: string, jobId: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs/${jobId}/pause`, { method: "POST" }),
  resumeCrawlJob: (id: string, jobId: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs/${jobId}/resume`, { method: "POST" }),
  startCrawlJob: (id: string, jobId: string) =>
    request<WebsiteCrawlJob>(`/api/websites/${id}/crawl-jobs/${jobId}/start`, { method: "POST" }),
  listCrawlJobPages: (id: string, jobId: string) =>
    request<{ pages: WebsitePage[] }>(`/api/websites/${id}/crawl-jobs/${jobId}/pages`),
  listCrawlJobDocuments: (id: string, jobId: string) =>
    request<{ documents: WebsiteDocument[] }>(`/api/websites/${id}/crawl-jobs/${jobId}/documents`),
};
