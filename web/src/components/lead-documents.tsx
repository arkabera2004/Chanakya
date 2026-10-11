"use client";

import { Spot } from "@/components/vira/art";
import { FileText, ListChecks, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { Button, Panel, StatusChip, type Tone } from "@/components/ui";
import { useDesk } from "@/context/desk";
import type { LeadDetail } from "@/lib/api";
import { documentsApi, fileSize, type NoticeDocument, type NoticeDocuments, type NoticeJob } from "@/lib/api-documents";
import { budgetReason, count, money, when } from "@/lib/format";

const TYPES: Record<string, string> = { pdf: "PDF", docx: "DOCX", xlsx: "XLSX", zip: "ZIP" };

function reading(doc: NoticeDocument): { tone: Tone; label: string } {
  if (!doc.within_limit) return { tone: "neutral", label: "Over the limit, not read" };
  if (doc.read && doc.needs_manual_review) return { tone: "warning", label: "Needs a manual read" };
  if (doc.read) return { tone: "success", label: doc.pages ? `Read, ${count(doc.pages)} ${doc.pages === 1 ? "page" : "pages"}` : "Read" };
  return { tone: "neutral", label: "Not read yet" };
}

/** What the finished job did, in a sentence. The figures come from the run it recorded. */
function outcome(job: NoticeJob): { tone: "success" | "warning" | "error"; text: string } {
  if (job.status === "failed") return { tone: "error", text: job.error || "The analysis did not finish." };
  const stats = job.stats;
  if (!stats) return { tone: "success", text: "Analysed." };
  if (stats.deep_calls > 0) {
    return { tone: "success", text: `Analysed. The deep analysis ran on the model, at a cost of ${money(stats.llm_cost_usd)}.` };
  }
  if (stats.llm_skipped_budget > 0) {
    const reason = stats.budget_blocked_reasons.map(budgetReason).join(" ");
    return { tone: "warning", text: `The model budget stopped the deep analysis. ${reason} The clauses found by rule are on this page, and the next pass tries the model again.` };
  }
  if (stats.deep_deferred > 0) {
    return { tone: "warning", text: "There was too little readable text for a deep analysis. A scanned document has no text to read: check what was added." };
  }
  if (!stats.llm_mode || stats.llm_mode === "disabled") {
    return { tone: "success", text: "Analysed by rule only. The model is switched off in the config." };
  }
  return { tone: "success", text: "Analysed. No new model call was needed." };
}

const OUTCOME_TEXT = { success: "text-success", warning: "text-warning", error: "text-error" } as const;

export function LeadDocuments({ lead, onAnalysed }: { lead: LeadDetail; onAnalysed: () => void }) {
  const { job, busy, writerBusy, followJob } = useDesk();
  const [docs, setDocs] = useState<NoticeDocuments | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [working, setWorking] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [startedJob, setStartedJob] = useState<string | null>(null);
  const handled = useRef<string | null>(null);
  const id = lead.id;

  const load = useCallback(() => {
    documentsApi.list(id)
      .then((next) => {
        setDocs(next);
        setLoadError(null);
      })
      .catch((err: Error) => setLoadError(err.message));
  }, [id]);

  useEffect(() => {
    setDocs(null);
    setProblems([]);
    setStartedJob(null);
    load();
  }, [load]);

  // The job for this notice, followed by the desk. When it ends, the page shows the new record.
  // One that is still running when the page is opened again is picked up, not lost.
  const current: NoticeJob | null = job;
  const runningHere = current?.status === "running" && current.opportunity_id === id ? current.id : null;
  useEffect(() => {
    if (runningHere) setStartedJob(runningHere);
  }, [runningHere]);
  const mine: NoticeJob | null = current && current.id === startedJob ? current : null;
  const analysing = mine?.status === "running";
  useEffect(() => {
    if (!mine || mine.status === "running" || handled.current === mine.id) return;
    handled.current = mine.id;
    onAnalysed();
    load();
  }, [mine, onAnalysed, load]);

  const locked = busy || writerBusy || working !== null;
  const list = docs?.documents ?? [];
  const full = docs ? list.length >= docs.max_documents : false;
  const unread = list.filter((doc) => doc.within_limit && !doc.read).length;
  const setAside = lead.decision === "rejected" || lead.decision === "low";

  async function add(files: File[]) {
    if (!docs || files.length === 0) return;
    const failed: string[] = [];
    for (const file of files) {
      if (file.size > docs.max_mb * 1024 * 1024) {
        failed.push(`${file.name}: larger than the ${docs.max_mb} MB limit for one document.`);
        continue;
      }
      setWorking(`Adding ${file.name}`);
      try {
        setDocs(await documentsApi.upload(id, file));
      } catch (err) {
        const reason = err instanceof TypeError ? "The desk API is not reachable." : err instanceof Error ? err.message : "It was not stored.";
        failed.push(`${file.name}: ${reason}`);
      }
    }
    setWorking(null);
    setProblems(failed);
  }

  async function remove(doc: NoticeDocument) {
    setWorking(`Removing ${doc.name}`);
    try {
      setDocs(await documentsApi.remove(id, doc));
      setProblems([]);
    } catch (err) {
      setProblems([`${doc.name}: ${err instanceof Error ? err.message : "It was not removed."}`]);
    } finally {
      setWorking(null);
    }
  }

  async function analyse() {
    setProblems([]);
    try {
      const { job: next } = await documentsApi.analyse(id);
      setStartedJob(next.id);
      followJob(next);
    } catch (err) {
      setProblems([err instanceof Error ? err.message : "The analysis did not start."]);
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    if (!locked && !full) add(Array.from(event.dataTransfer.files));
  }

  const result = mine && !analysing ? outcome(mine) : null;

  return (
    <Panel
      title="Documents"
      action={docs ? <span className="text-body-sm text-tertiary tabular">{count(list.length)} of {count(docs.max_documents)}</span> : null}
    >
      <div className="space-y-2 px-4 pt-4">
        <p className="max-w-[72ch] text-body-md text-secondary">
          The portal keeps tender documents behind a CAPTCHA, and Vira does not solve it. Download them from the official listing and add them here.
          She reads them when you analyse this notice.
        </p>
        {setAside ? (
          <p className="text-body-sm text-warning">Vira set this notice aside on the rules, so she does not open documents for it.</p>
        ) : lead.analysis_state === "deferred_no_documents" ? (
          <p className="text-body-sm text-warning">The deep analysis of this notice is waiting for its documents.</p>
        ) : null}
      </div>

      {loadError ? <p className="mx-4 mt-3 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-sm text-error">{loadError}</p> : null}
      {!docs && !loadError ? <p className="px-4 pt-3 text-body-sm text-tertiary">Looking for documents…</p> : null}

      {docs && list.length === 0 ? (
        <div className="flex items-center gap-3 px-4 pt-3">
          <Spot name="documents" size={72} />
          <p className="text-body-sm text-tertiary">No document is attached to this notice yet.</p>
        </div>
      ) : null}
      {list.length > 0 ? (
        <ul className="mt-3 divide-y divide-muted border-y border-muted">
          {list.map((doc) => {
            const state = reading(doc);
            return (
              <li key={`${doc.folder ?? "portal"}/${doc.name}`} className="flex flex-wrap items-start gap-x-3 gap-y-2 px-4 py-3">
                <FileText size={14} strokeWidth={1.75} className="mt-1 shrink-0 text-tertiary" />
                <div className="min-w-0 flex-1 basis-40">
                  <div className="break-all text-label-md text-primary">{doc.name}</div>
                  <p className="mt-0.5 text-body-sm text-tertiary">
                    {TYPES[doc.type] ?? doc.type} · {fileSize(doc.size_bytes)} · {doc.origin === "portal" ? "fetched from the portal" : `added ${when(doc.modified_at)}`}
                  </p>
                  {doc.error ? <p className="mt-0.5 text-body-sm text-warning">{doc.error}</p> : null}
                </div>
                <StatusChip tone={state.tone}>{state.label}</StatusChip>
                {doc.removable ? (
                  <button
                    type="button"
                    aria-label={`Remove ${doc.name}`}
                    title="Remove"
                    disabled={locked}
                    onClick={() => remove(doc)}
                    className="-my-1 -mr-2 rounded-lg p-2 text-secondary hover:bg-action-tertiary-hover disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Trash2 size={14} strokeWidth={1.75} />
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {docs ? (
        <div className="space-y-3 p-4">
          {full ? (
            <p className="text-body-sm text-tertiary">
              A pass reads {count(docs.max_documents)} documents for one notice, and this one has them. Remove one to add another.
            </p>
          ) : (
            <label
              onDragOver={(event) => {
                event.preventDefault();
                if (!locked) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={[
                "flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-5 text-center focus-within:border-vira",
                locked ? "cursor-not-allowed border-muted opacity-60" : "cursor-pointer hover:border-default hover:bg-raised",
                dragging ? "border-vira bg-raised" : "border-muted",
              ].join(" ")}
            >
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.xlsx,.zip"
                className="sr-only"
                disabled={locked}
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  add(files);
                }}
              />
              <Upload size={20} strokeWidth={1.5} className="text-tertiary" />
              <span className="text-label-md text-primary">{working ?? "Drop files here, or choose them"}</span>
              <span className="text-body-sm text-tertiary">
                PDF, DOCX, XLSX or ZIP. Up to {docs.max_mb} MB each, {count(docs.max_documents)} documents for one notice.
              </span>
            </label>
          )}

          {writerBusy ? (
            <p className="text-body-sm text-tertiary">A watch or another command holds the database. Documents can be added when it has finished.</p>
          ) : null}
          {problems.length > 0 ? (
            <ul className="space-y-1 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-sm text-error" aria-live="polite">
              {problems.map((problem) => (
                <li key={problem} className="break-words">{problem}</li>
              ))}
            </ul>
          ) : null}

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-muted pt-3">
            <Button variant={unread > 0 ? "primary" : "secondary"} disabled={locked} onClick={analyse}>
              <ListChecks size={14} strokeWidth={1.75} />
              {analysing ? "Analysing" : "Analyse this notice"}
            </Button>
            <p className="min-w-0 flex-1 basis-48 text-body-sm text-tertiary" aria-live="polite">
              {analysing
                ? mine?.events.at(-1)?.message ?? "Starting."
                : result
                  ? <span className={OUTCOME_TEXT[result.tone]}>{result.text}</span>
                  : unread > 0
                    ? `${count(unread)} ${unread === 1 ? "document has" : "documents have"} not been read yet. Only this notice is assessed again, inside the model budget.`
                    : "Assesses only this notice again, inside the model budget."}
            </p>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
