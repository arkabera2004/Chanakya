import { request, type Job } from "@/lib/api";

/** One document a pass would find for a notice. */
export type NoticeDocument = {
  name: string;
  /** The inbox folder it sits in. Null for a file a connector downloaded. */
  folder: string | null;
  origin: "inbox" | "portal";
  removable: boolean;
  /** Taken from the bytes of the file, not from its name. */
  type: string;
  size_bytes: number;
  modified_at: string;
  /** A pass opens only the first few documents of a notice. */
  within_limit: boolean;
  /** A pass has opened this exact file. */
  read: boolean;
  pages: number | null;
  needs_manual_review: boolean | null;
  error: string | null;
};

export type NoticeDocuments = {
  opportunity_id: string;
  documents: NoticeDocument[];
  max_documents: number;
  max_mb: number;
  accepted: string[];
  analysis_state: string | null;
  decision: string | null;
};

export type StoredDocument = {
  name: string;
  type: string;
  size_bytes: number;
  replaced: boolean;
  archive_members: string[];
};

/** The job the desk runs for one notice carries the notice it is for. */
export type NoticeJob = Job & { opportunity_id?: string };

const base = (id: string) => `/api/opportunities/${encodeURIComponent(id)}`;

export const documentsApi = {
  list: (id: string) => request<NoticeDocuments>(`${base(id)}/documents`),
  /** One file per request. The file is the request body, and its name travels in the query. */
  upload: (id: string, file: File) =>
    request<NoticeDocuments & { stored: StoredDocument }>(
      `${base(id)}/documents?filename=${encodeURIComponent(file.name)}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file },
    ),
  remove: (id: string, doc: Pick<NoticeDocument, "name" | "folder">) =>
    request<NoticeDocuments>(
      `${base(id)}/documents/${encodeURIComponent(doc.name)}${doc.folder ? `?folder=${encodeURIComponent(doc.folder)}` : ""}`,
      { method: "DELETE" },
    ),
  analyse: (id: string) => request<{ job: NoticeJob }>(`${base(id)}/analyze`, { method: "POST" }),
};

/** "412 KB", "3.4 MB". */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
