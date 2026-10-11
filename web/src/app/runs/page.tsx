"use client";

import { Gauge, Mail } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState, PageBody, PageHeader, Panel, StatusChip, type Tone } from "@/components/ui";
import { EmptyPasses } from "@/components/vira/art";
import { useDesk } from "@/context/desk";
import { api, type RunRow } from "@/lib/api";
import { budgetHit, budgetReason, count, money, triggerLabel, when } from "@/lib/format";

const STATUS: Record<string, { tone: Tone; label: string }> = {
  ok: { tone: "success", label: "Finished" },
  partial: { tone: "warning", label: "Partial" },
  failed: { tone: "error", label: "Failed" },
  running: { tone: "info", label: "Running" },
  stopped: { tone: "neutral", label: "Stopped" },
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-caption uppercase tracking-wide text-quaternary">{label}</dt>
      <dd className="mt-0.5 text-heading-sm text-primary tabular">{value}</dd>
    </div>
  );
}

function RunCard({ run }: { run: RunRow }) {
  const status = STATUS[run.status] ?? { tone: "neutral" as Tone, label: run.status };
  // A pass someone stopped on purpose is a fact about the run, not a fault.
  const calm = run.status === "partial" || run.status === "stopped";
  return (
    <Panel bodyClassName="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-heading-sm text-primary">{triggerLabel(run.trigger)}</h3>
          <p className="mt-0.5 text-body-sm text-tertiary">
            {when(run.started_at)} to {run.finished_at ? when(run.finished_at) : run.status === "stopped" ? "never closed" : "still open"}
            <span className="text-quaternary"> · {run.id}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {budgetHit(run) ? (
            <StatusChip tone="warning">
              <Gauge size={14} strokeWidth={1.75} />
              Budget hit
            </StatusChip>
          ) : null}
          <StatusChip tone={status.tone}>{status.label}</StatusChip>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-5">
        <Stat label="New" value={count(run.new)} />
        <Stat label="Assessed" value={count(run.assessed)} />
        <Stat label="Strong / review" value={`${count(run.strong)} / ${count(run.candidates)}`} />
        <Stat label="Set aside" value={count(run.rejected)} />
        <Stat label="Model cost" value={money(run.llm_cost_usd)} />
      </dl>

      {run.priorities ? (
        <p className="mt-3 text-body-sm text-secondary tabular">
          P1 {count(run.priorities.p1)} · P2 {count(run.priorities.p2)} · P3 {count(run.priorities.p3)} · P4 {count(run.priorities.p4)}
        </p>
      ) : null}
      <p className="mt-3 text-body-sm text-tertiary">
        {run.llm_mode ? `Model ${run.llm_mode}, ` : ""}
        {count(run.triage_calls)} triage and {count(run.deep_calls)} deep calls
        {run.llm_skipped_budget > 0 ? `. ${count(run.llm_skipped_budget)} notices wait for the model budget` : ""}
        {run.deep_deferred > 0 ? `. ${count(run.deep_deferred)} wait for documents` : ""}.
      </p>
      {run.budget_blocked_reasons.length > 0 ? (
        <p className="mt-1 text-body-sm text-warning">{run.budget_blocked_reasons.map(budgetReason).join(" ")}</p>
      ) : null}
      {run.error ? <p className={`mt-2 text-body-sm ${calm ? "text-secondary" : "text-error"}`}>{run.error}</p> : null}
      {run.coverage_notes.length > 0 ? (
        <ul className="mt-2 space-y-1 text-body-sm text-tertiary">
          {run.coverage_notes.filter((note) => note !== run.error).map((note) => <li key={note}>{note}</li>)}
        </ul>
      ) : null}
    </Panel>
  );
}

export default function RunsPage() {
  const { brief, job, writerBusy } = useDesk();
  const mail = brief?.email;
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    function load() {
      api.runs()
        .then((data) => {
          if (cancelled) return;
          setRows(data.runs);
          setError(null);
          const open = data.runs.some((run) => run.status === "running") || job?.status === "running" || writerBusy;
          if (open) timer = window.setTimeout(load, 15000);
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
  }, [job?.status, writerBusy]);

  return (
    <>
      <PageHeader
        title="Passes"
        description="Every time Vira opens the sources, the result is a run: what she saw, what she set aside, and what the model cost."
      />
      <PageBody>
        {mail ? (
          <p className="mb-3 flex items-start gap-2 text-body-sm text-tertiary">
            <Mail size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
            <span>
              {mail.recipients === 0
                ? "No email recipients are configured, so passes send nothing."
                : `Emailed passes send a ${mail.digest} digest to ${mail.recipients} ${mail.recipients === 1 ? "recipient" : "recipients"}${mail.provider === "file" ? " (written to the outbox folder, not sent)" : ""}. New P1 alerts are ${mail.alert_on_new_p1 ? "on" : "off"}.`}
            </span>
          </p>
        ) : null}
        {error ? <p className="mb-3 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">{error}</p> : null}
        {rows === null && !error ? <p className="mb-3 text-body-md text-tertiary">Loading passes…</p> : null}
        <div className="space-y-3">
          {(rows ?? []).map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </div>
        {rows && rows.length === 0 ? (
          <EmptyState art={<EmptyPasses />} title="No passes yet">
            Start one from the desk. Each pass is kept here with its counts and its cost.
          </EmptyState>
        ) : null}
      </PageBody>
    </>
  );
}
