"use client";

import { ExternalLink, Pause, Play, Plus, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, EmptyState, PageBody, PageHeader, Panel, StatusChip, type Tone } from "@/components/ui";
import {
  websitesApi, type Website, type WebsiteCrawlJob, type WebsiteDocument, type WebsitePage, type WebsiteTender,
} from "@/lib/api-websites";
import { when } from "@/lib/format";

const JOB_STATUS: Record<string, Tone> = {
  queued: "neutral", running: "info", paused: "warning",
  succeeded: "success", partial: "warning", failed: "error", cancelled: "neutral",
};

function JobRow({ job, onOpen, onStart, onCancel, onPause, onResume }: {
  job: WebsiteCrawlJob;
  onOpen: (id: string) => void;
  onStart: (id: string) => void;
  onCancel: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
      <button type="button" onClick={() => onOpen(job.id)} className="min-w-0 flex-1 basis-40 text-left text-label-md text-primary hover:underline">
        {job.id}
      </button>
      <span className="text-body-sm text-tertiary">{when(job.created_at)}</span>
      <span className="text-body-sm text-tertiary tabular">
        {job.pages_processed}/{job.pages_discovered} pages &middot; {job.documents_downloaded} docs &middot; {job.tenders_extracted} tenders
      </span>
      <StatusChip tone={JOB_STATUS[job.status] ?? "neutral"}>{job.status}</StatusChip>
      {job.status === "queued" ? (
        <Button variant="ghost" onClick={() => onStart(job.id)} aria-label="Start crawl"><Play size={14} strokeWidth={1.75} /></Button>
      ) : null}
      {job.status === "running" ? (
        <Button variant="ghost" onClick={() => onPause(job.id)} aria-label="Pause crawl"><Pause size={14} strokeWidth={1.75} /></Button>
      ) : null}
      {job.status === "paused" ? (
        <Button variant="ghost" onClick={() => onResume(job.id)} aria-label="Resume crawl"><Play size={14} strokeWidth={1.75} /></Button>
      ) : null}
      {["queued", "running", "paused"].includes(job.status) ? (
        <Button variant="ghost" onClick={() => onCancel(job.id)} aria-label="Cancel crawl"><Square size={14} strokeWidth={1.75} /></Button>
      ) : null}
    </li>
  );
}

function TenderCard({ tender }: { tender: WebsiteTender }) {
  const entries = Object.entries(tender.fields).filter(([, f]) => f.value !== null);
  return (
    <Panel bodyClassName="p-4">
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-label-md text-primary break-all">{tender.source_url}</h4>
        <a href={tender.source_url} target="_blank" rel="noreferrer" className="shrink-0 text-tertiary hover:text-primary">
          <ExternalLink size={14} strokeWidth={1.75} />
        </a>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {entries.length === 0 ? (
          <p className="text-body-sm text-tertiary">Not found in source</p>
        ) : entries.map(([name, f]) => (
          <div key={name}>
            <dt className="text-caption uppercase tracking-wide text-quaternary">{name.replace(/_/g, " ")}</dt>
            <dd className="mt-0.5 text-body-sm text-primary">{String(f.value)}</dd>
            {f.excerpt ? <dd className="mt-0.5 text-body-sm text-quaternary italic">&ldquo;{f.excerpt}&rdquo;</dd> : null}
          </div>
        ))}
      </dl>
      {tender.matches.length > 0 ? (
        <>
          <h5 className="mt-3 text-label-sm text-primary">Matched Wayam capabilities</h5>
          <ul className="mt-1 space-y-1">
            {tender.matches.map((m) => (
              <li key={m.capability_id} className="text-body-sm text-tertiary">
                {m.capability_id} &middot; score {m.score.toFixed(2)}
                {m.strong.length > 0 ? ` — ${m.strong.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

export default function WebsiteDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;

  const [website, setWebsite] = useState<Website | null>(null);
  const [jobs, setJobs] = useState<WebsiteCrawlJob[] | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [pages, setPages] = useState<WebsitePage[] | null>(null);
  const [documents, setDocuments] = useState<WebsiteDocument[] | null>(null);
  const [tenders, setTenders] = useState<WebsiteTender[] | null>(null);
  const [tab, setTab] = useState<"tenders" | "pages" | "documents">("tenders");
  const [error, setError] = useState<string | null>(null);

  function loadWebsite() {
    websitesApi.get(id).then(setWebsite).catch((err: Error) => setError(err.message));
    websitesApi.listCrawlJobs(id).then((r) => setJobs(r.crawl_jobs)).catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    loadWebsite();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function openJob(jobId: string) {
    setSelectedJobId(jobId);
    websitesApi.listCrawlJobPages(id, jobId).then((r) => setPages(r.pages)).catch((err: Error) => setError(err.message));
    websitesApi.listCrawlJobDocuments(id, jobId).then((r) => setDocuments(r.documents)).catch((err: Error) => setError(err.message));
    websitesApi.listCrawlJobTenders(id, jobId).then((r) => setTenders(r.tenders)).catch((err: Error) => setError(err.message));
  }

  function createJob() {
    websitesApi.createCrawlJob(id).then((job) => {
      setJobs((prev) => [job, ...(prev ?? [])]);
    }).catch((err: Error) => setError(err.message));
  }

  function startJob(jobId: string) {
    websitesApi.startCrawlJob(id, jobId).then(() => { loadWebsite(); openJob(jobId); }).catch((err: Error) => setError(err.message));
  }

  function cancelJob(jobId: string) {
    websitesApi.cancelCrawlJob(id, jobId).then(loadWebsite).catch((err: Error) => setError(err.message));
  }

  function pauseJob(jobId: string) {
    websitesApi.pauseCrawlJob(id, jobId).then(loadWebsite).catch((err: Error) => setError(err.message));
  }

  function resumeJob(jobId: string) {
    websitesApi.resumeCrawlJob(id, jobId).then(loadWebsite).catch((err: Error) => setError(err.message));
  }

  function removeWebsite() {
    if (!window.confirm(`Remove ${website?.name}? This also removes its crawl history.`)) return;
    websitesApi.remove(id).then(() => router.push("/websites")).catch((err: Error) => setError(err.message));
  }

  return (
    <>
      <PageHeader
        title={website?.name ?? "Website"}
        description={website?.base_url}
        actions={
          <Button variant="ghost" onClick={removeWebsite} aria-label="Remove website">
            <Trash2 size={14} strokeWidth={1.75} /> Remove
          </Button>
        }
      />
      <PageBody>
        {error ? (
          <Panel bodyClassName="p-4"><p className="text-body-sm text-error">{error}</p></Panel>
        ) : null}

        <Panel title="Crawl jobs" action={<Button onClick={createJob}><Plus size={14} strokeWidth={1.75} /> New crawl</Button>}>
          {jobs && jobs.length === 0 ? (
            <EmptyState title="No crawls yet">Start a crawl to discover pages, documents and tenders.</EmptyState>
          ) : (
            <ul className="divide-y divide-muted">
              {(jobs ?? []).map((job) => (
                <JobRow key={job.id} job={job} onOpen={openJob} onStart={startJob} onCancel={cancelJob}
                        onPause={pauseJob} onResume={resumeJob} />
              ))}
            </ul>
          )}
        </Panel>

        {selectedJobId ? (
          <div className="mt-4">
            <Panel title={`Crawl ${selectedJobId}`}>
              <div className="flex gap-1 border-b border-muted px-4 pt-2">
                {(["tenders", "pages", "documents"] as const).map((t) => (
                  <button key={t} type="button" onClick={() => setTab(t)}
                          className={`rounded-t-lg px-3 py-1.5 text-label-sm capitalize ${tab === t ? "border-b-2 border-primary text-primary" : "text-tertiary"}`}>
                    {t} ({t === "tenders" ? tenders?.length ?? 0 : t === "pages" ? pages?.length ?? 0 : documents?.length ?? 0})
                  </button>
                ))}
              </div>
              <div className="p-4">
                {tab === "tenders" ? (
                  tenders && tenders.length === 0 ? (
                    <EmptyState title="No tenders extracted">
                      No tender-related fields were found in the crawled pages or documents.
                    </EmptyState>
                  ) : (
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                      {tenders && tenders.length > 0 && selectedJobId ? (
                        <div className="col-span-full flex justify-end gap-2">
                          <a href={websitesApi.exportTendersUrl(id, selectedJobId, "csv")} className="text-body-sm text-tertiary hover:underline">
                            Export CSV
                          </a>
                          <a href={websitesApi.exportTendersUrl(id, selectedJobId, "xlsx")} className="text-body-sm text-tertiary hover:underline">
                            Export XLSX
                          </a>
                        </div>
                      ) : null}
                      {(tenders ?? []).map((t) => <TenderCard key={t.id} tender={t} />)}
                    </div>
                  )
                ) : null}
                {tab === "pages" ? (
                  <ul className="divide-y divide-muted">
                    {(pages ?? []).map((p) => (
                      <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                        <a href={p.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 basis-56 truncate text-body-sm text-primary hover:underline">
                          {p.url}
                        </a>
                        <span className="text-body-sm text-tertiary">{p.http_status ?? "—"}</span>
                        <StatusChip tone={p.status === "fetched" ? "success" : p.status === "failed" ? "error" : "neutral"}>
                          {p.status}
                        </StatusChip>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {tab === "documents" ? (
                  <ul className="divide-y divide-muted">
                    {(documents ?? []).map((d) => (
                      <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                        <a href={d.document_url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 basis-56 truncate text-body-sm text-primary hover:underline">
                          {d.document_url}
                        </a>
                        <Link href={d.source_page_url} className="text-body-sm text-tertiary hover:underline">source page</Link>
                        <StatusChip tone={d.status === "extracted" ? "success" : "error"}>{d.status}</StatusChip>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </Panel>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}
