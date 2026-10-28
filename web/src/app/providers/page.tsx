"use client";

import { CircleAlert, CircleCheck, CircleDashed, RotateCcw, Square, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, EmptyState, Metric, PageBody, PageHeader, Panel, StatusChip, type Tone } from "@/components/ui";
import { acquisitionApi, type AcquisitionAnalytics, type AcquisitionJob, type AcquisitionJobDetail, type ProviderStatus } from "@/lib/api-acquisition";
import { count, when } from "@/lib/format";

const JOB_STATUS: Record<string, { tone: Tone; label: string }> = {
  queued: { tone: "neutral", label: "Queued" },
  running: { tone: "info", label: "Running" },
  succeeded: { tone: "success", label: "Succeeded" },
  partial: { tone: "warning", label: "Partial" },
  failed: { tone: "error", label: "Failed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

function ProviderHealth({ provider }: { provider: ProviderStatus }) {
  let icon: LucideIcon = CircleDashed;
  let tone: Tone = "neutral";
  let label = "Disabled";
  if (provider.enabled && provider.configured) {
    icon = CircleCheck;
    tone = "success";
    label = "Ready";
  } else if (provider.enabled && !provider.configured) {
    icon = CircleAlert;
    tone = provider.requires_credentials ? "warning" : "error";
    label = provider.requires_credentials ? "Needs credentials" : "Not installed";
  }
  const Icon = icon;
  return (
    <StatusChip tone={tone}>
      <Icon size={14} strokeWidth={1.75} />
      {label}
    </StatusChip>
  );
}

function ProviderCard({ provider }: { provider: ProviderStatus }) {
  return (
    <Panel bodyClassName="p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-heading-sm text-primary capitalize">{provider.provider_id}</h3>
        <ProviderHealth provider={provider} />
      </div>
      <p className="mt-2 text-body-sm text-tertiary">{provider.capabilities.join(", ") || "no capabilities declared"}</p>
      {provider.problems.length > 0 ? (
        <ul className="mt-2 space-y-1 text-body-sm text-quaternary">
          {provider.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      ) : null}
    </Panel>
  );
}

function JobRow({ job, onSelect, onCancel, onRetry }: {
  job: AcquisitionJob;
  onSelect: (id: string) => void;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  const status = JOB_STATUS[job.status] ?? { tone: "neutral" as Tone, label: job.status };
  const cancellable = job.status === "queued" || job.status === "running";
  const retryable = job.status === "failed" && job.retryable;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
      <button type="button" onClick={() => onSelect(job.id)} className="min-w-0 flex-1 basis-56 text-left text-label-md text-primary hover:underline">
        {job.provider_id} &middot; {job.url}
      </button>
      <span className="text-body-sm text-tertiary">{when(job.created_at)}</span>
      {job.record_count > 0 ? <span className="text-body-sm text-tertiary tabular">{count(job.record_count)} records</span> : null}
      <StatusChip tone={status.tone}>{status.label}</StatusChip>
      {cancellable ? (
        <Button variant="ghost" onClick={() => onCancel(job.id)} aria-label="Cancel job">
          <Square size={14} strokeWidth={1.75} />
        </Button>
      ) : null}
      {retryable ? (
        <Button variant="ghost" onClick={() => onRetry(job.id)} aria-label="Retry job">
          <RotateCcw size={14} strokeWidth={1.75} />
        </Button>
      ) : null}
    </li>
  );
}

function JobDetailPanel({ job, onClose }: { job: AcquisitionJobDetail; onClose: () => void }) {
  return (
    <Panel title={`Job ${job.id}`} action={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      <div className="px-4 py-3">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <dt className="text-caption uppercase tracking-wide text-quaternary">Provider</dt>
            <dd className="mt-0.5 text-body-md text-primary">{job.provider_id}</dd>
          </div>
          <div>
            <dt className="text-caption uppercase tracking-wide text-quaternary">Attempt</dt>
            <dd className="mt-0.5 text-body-md text-primary tabular">{job.attempt} / {job.max_attempts}</dd>
          </div>
          <div>
            <dt className="text-caption uppercase tracking-wide text-quaternary">Records</dt>
            <dd className="mt-0.5 text-body-md text-primary tabular">{count(job.record_count)}</dd>
          </div>
          <div>
            <dt className="text-caption uppercase tracking-wide text-quaternary">Retryable</dt>
            <dd className="mt-0.5 text-body-md text-primary">{job.retryable ? "Yes" : "No"}</dd>
          </div>
        </dl>
        {job.error ? <p className="mt-3 text-body-sm text-error">{job.error}</p> : null}
        <h4 className="mt-4 text-label-md text-primary">Event history</h4>
        <ul className="mt-2 space-y-2">
          {job.events.map((event, i) => (
            <li key={i} className="text-body-sm">
              <span className="text-tertiary">{when(event.at)}</span> &middot; <span className="text-primary">{event.kind}</span>
              {event.message ? <span className="text-tertiary"> — {event.message}</span> : null}
            </li>
          ))}
        </ul>
        {Object.keys(job.diagnostics ?? {}).length > 0 ? (
          <>
            <h4 className="mt-4 text-label-md text-primary">Diagnostics</h4>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-surface-secondary p-3 text-body-sm text-tertiary">
              {JSON.stringify(job.diagnostics, null, 2)}
            </pre>
          </>
        ) : null}
      </div>
    </Panel>
  );
}

export default function ProvidersPage() {
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [jobs, setJobs] = useState<AcquisitionJob[] | null>(null);
  const [analytics, setAnalytics] = useState<AcquisitionAnalytics | null>(null);
  const [selected, setSelected] = useState<AcquisitionJobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    function load() {
      Promise.all([acquisitionApi.providers(), acquisitionApi.jobs({ limit: 50 }), acquisitionApi.analytics()])
        .then(([p, j, a]) => {
          if (cancelled) return;
          setProviders(p.providers);
          setJobs(j.jobs);
          setAnalytics(a);
          setError(null);
          const open = j.jobs.some((job) => job.status === "queued" || job.status === "running");
          if (open) timer = window.setTimeout(load, 3000);
        })
        .catch((err: Error) => {
          if (!cancelled) setError(err.message);
        });
    }

    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  function openJob(id: string) {
    acquisitionApi.job(id).then(setSelected).catch((err: Error) => setError(err.message));
  }

  function cancelJob(id: string) {
    acquisitionApi.cancelJob(id).catch((err: Error) => setError(err.message));
  }

  function retryJob(id: string) {
    acquisitionApi.retryJob(id).catch((err: Error) => setError(err.message));
  }

  const ready = (providers ?? []).filter((p) => p.enabled && p.configured).length;

  return (
    <>
      <PageHeader
        title="Providers"
        description="Nine acquisition strategies behind one job manager. A provider with no credentials shows what it needs, not a crash."
      />
      <PageBody>
        {error ? (
          <Panel bodyClassName="p-4">
            <p className="text-body-sm text-error">The acquisition API is not reachable: {error}</p>
          </Panel>
        ) : null}

        {providers ? (
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <Metric label="Ready" value={count(ready)} hint={`${count(providers.length)} total`} />
            <Metric label="Jobs" value={count(analytics?.jobs_total ?? jobs?.length ?? 0)} hint="all time" />
            <Metric
              label="Succeeded / Failed"
              value={`${count(analytics?.by_status.succeeded ?? 0)} / ${count((analytics?.by_status.failed ?? 0) + (analytics?.by_status.partial ?? 0))}`}
              hint="partial counted with failed"
            />
            <Metric
              label="Avg duration"
              value={analytics?.avg_duration_s != null ? `${analytics.avg_duration_s.toFixed(1)}s` : "—"}
              hint={analytics?.avg_duration_s != null ? `p95 ${analytics.p95_duration_s?.toFixed(1)}s` : "no finished job yet"}
            />
          </div>
        ) : null}

        {providers && providers.length > 0 ? (
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {providers.map((p) => <ProviderCard key={p.provider_id} provider={p} />)}
          </div>
        ) : null}

        {selected ? (
          <div className="mt-4">
            <JobDetailPanel job={selected} onClose={() => setSelected(null)} />
          </div>
        ) : null}

        <div className="mt-4">
          <Panel title="Recent jobs">
            {jobs && jobs.length === 0 ? (
              <EmptyState title="No acquisition jobs yet">
                Jobs appear here once a source or a manual run uses one of the nine providers above.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-muted">
                {(jobs ?? []).map((job) => (
                  <JobRow key={job.id} job={job} onSelect={openJob} onCancel={cancelJob} onRetry={retryJob} />
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
