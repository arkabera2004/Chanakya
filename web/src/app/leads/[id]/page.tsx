"use client";

import { ArrowLeft, Bookmark, CalendarClock, Check, ExternalLink, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { LeadDocuments } from "@/components/lead-documents";
import { Button, PageBody, PageHeader, Panel, Priority, StatusChip, Tag } from "@/components/ui";
import { WayamOrb } from "@/components/vira/orb";
import { api, type LeadDetail } from "@/lib/api";
import { SCORE_LABELS, closingSoon, daysLeft, inr, when } from "@/lib/format";

const REVIEWS = ["Shortlisted", "Reviewing", "Partner needed", "Pursuing", "Rejected"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3 py-1.5">
      <dt className="text-body-sm text-tertiary">{label}</dt>
      <dd className="text-body-md text-primary">{children}</dd>
    </div>
  );
}

export default function LeadPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLead(null);
    setError(null);
    api.lead(id)
      .then((row) => {
        if (!cancelled) setLead(row);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // After documents are analysed, show the new record without losing the page.
  const reload = useCallback(() => {
    api.lead(id).then(setLead).catch(() => undefined);
  }, [id]);

  async function review(status?: string, label?: string) {
    setSaving(true);
    setActionError(null);
    setSaved(null);
    try {
      const next = await api.review(id, { status, label, note: note || undefined });
      setLead((prev) => (prev ? { ...prev, ...next } : prev));
      setSaved(status ? `Marked ${status}.` : `Recorded as ${label}.`);
      setNote("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "The decision was not saved.");
    } finally {
      setSaving(false);
    }
  }

  const back = (
    <Link href="/leads" className="inline-flex items-center gap-1.5 text-label-sm text-secondary hover:text-primary">
      <ArrowLeft size={14} strokeWidth={1.75} />
      Back to leads
    </Link>
  );

  if (error) {
    return (
      <>
        <PageHeader title="Lead" actions={back} />
        <PageBody>
          <p className="rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">
            {error === "unknown opportunity" ? "This notice is not in the book. It may have been removed, or the link is from another database." : error}
          </p>
        </PageBody>
      </>
    );
  }
  if (!lead) {
    return (
      <>
        <PageHeader title="Lead" actions={back} />
        <PageBody>
          <p className="text-body-md text-tertiary">Opening the record…</p>
        </PageBody>
      </>
    );
  }

  const components = lead.score_breakdown.components ?? {};
  const left = daysLeft(lead.closing_at);
  const soon = closingSoon(lead.closing_at);
  const fit = lead.capabilities[0];

  return (
    <>
      <PageHeader title="Lead" description={`${lead.id}${lead.tender_id ? ` · Tender ID ${lead.tender_id}` : ""}`} actions={back} />
      <PageBody>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(19rem,0.8fr)]">
          <div className="space-y-4">
            <section className="vira-glow rounded-xl border border-muted bg-container p-5">
              <div className="flex flex-wrap items-center gap-2">
                <Priority value={lead.priority} />
                {fit ? <Tag>{fit.offering?.split(",")[0] || fit.id}</Tag> : null}
                <StatusChip tone={lead.review_status === "New" ? "neutral" : "info"}>{lead.review_status}</StatusChip>
                {lead.closing_at ? (
                  <span className={`inline-flex items-center gap-1 text-body-sm ${soon ? "text-warning" : "text-tertiary"}`}>
                    <CalendarClock size={14} strokeWidth={1.75} />
                    Closes {left ?? when(lead.closing_at)}
                  </span>
                ) : null}
              </div>
              <h3 className="mt-3 text-heading-md text-primary">{lead.title}</h3>
              <p className="mt-1 text-body-md text-tertiary">
                {lead.organisation || "Buyer not stated"}
                {lead.department ? `, ${lead.department}` : ""}
                {lead.state ? `, ${lead.state}` : ""}
              </p>
              <div className="mt-4 flex gap-3 border-t border-muted pt-4">
                <WayamOrb id="lead" size={44} mood="focused" />
                <div className="min-w-0">
                  <div className="text-caption uppercase tracking-wide text-quaternary">Why Vira kept it</div>
                  <p className="mt-1 text-body-lg text-primary">{lead.why || "No capability match was recorded."}</p>
                </div>
              </div>
              {lead.next_action ? (
                <div className="mt-4 rounded-lg border border-muted bg-raised px-3 py-2.5">
                  <div className="text-caption uppercase tracking-wide text-quaternary">Next action</div>
                  <p className="mt-1 text-body-md text-secondary">{lead.next_action}</p>
                </div>
              ) : null}
            </section>

            <LeadDocuments lead={lead} onAnalysed={reload} />

            <Panel
              title="How the score was built"
              action={
                lead.score != null ? (
                  <span className="text-body-sm text-tertiary">
                    <span className="font-display text-display-lg text-primary tabular">{lead.score}</span> of 100
                  </span>
                ) : null
              }
              bodyClassName="space-y-4 p-4"
            >
              {Object.entries(components).map(([name, component]) => (
                <div key={name}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-label-md text-primary">{SCORE_LABELS[name] ?? name}</span>
                    <span className="text-body-sm text-secondary tabular">{Math.round(component.score * 100)}%</span>
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-raised-2">
                    <div className="h-full rounded-full bg-vira" style={{ width: `${Math.max(0, Math.min(1, component.score)) * 100}%` }} />
                  </div>
                  <p className="mt-1.5 text-body-sm text-tertiary">{component.reason}</p>
                </div>
              ))}
              {Object.keys(components).length === 0 ? <p className="text-body-md text-tertiary">This notice has not been scored.</p> : null}
            </Panel>

            <Panel title="Eligibility, in her words" bodyClassName="space-y-3 p-4">
              <p className="text-body-md text-secondary">
                {lead.eligibility || "Unknown"}. <span className="text-tertiary">Basis: {lead.confidence_basis || "not recorded"}.</span>
              </p>
              {lead.findings.length === 0 ? <p className="text-body-sm text-tertiary">No eligibility clause was extracted from the text she had.</p> : null}
              {lead.findings.map((finding, index) => (
                <div key={index} className="rounded-lg bg-raised px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2 text-label-sm text-primary">
                    {finding.criterion}
                    <Tag>{finding.assessment}</Tag>
                    <span className="font-normal text-tertiary">{finding.explicit ? "stated in the notice" : "inferred"}</span>
                  </div>
                  <p className="mt-1 text-body-sm text-secondary">{finding.text}</p>
                </div>
              ))}
              {lead.missing_information.length > 0 ? (
                <p className="text-body-sm text-tertiary">Still missing: {lead.missing_information.join("; ")}.</p>
              ) : null}
            </Panel>
          </div>

          <div className="space-y-4">
            <Panel title="Your decision" bodyClassName="space-y-3 p-4">
              <div className="flex flex-wrap gap-2">
                {REVIEWS.map((status) => {
                  const current = lead.review_status === status;
                  return (
                    <Button
                      key={status}
                      variant={current ? "primary" : "secondary"}
                      aria-pressed={current}
                      disabled={saving}
                      onClick={() => review(status)}
                    >
                      {current ? <Check size={14} strokeWidth={1.75} /> : status === "Shortlisted" ? <Bookmark size={14} strokeWidth={1.75} /> : null}
                      {status}
                    </Button>
                  );
                })}
              </div>
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Note for the calibration log (optional)"
                aria-label="Note for the calibration log"
                className="min-h-20 w-full rounded-lg border border-muted bg-page px-3 py-2 text-body-md text-primary placeholder:text-quaternary hover:border-default"
              />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-body-sm text-tertiary">Was Vira right to keep it?</span>
                <Button variant="ghost" disabled={saving} onClick={() => review(undefined, "relevant")}>Relevant</Button>
                <Button variant="ghost" disabled={saving} onClick={() => review(undefined, "irrelevant")}>Not relevant</Button>
              </div>
              <p className="min-h-4 text-body-sm" aria-live="polite">
                {actionError ? <span className="text-error">{actionError}</span> : saved ? <span className="text-success">{saved}</span> : null}
              </p>
            </Panel>

            <Panel title="Record" bodyClassName="p-4">
              <dl className="divide-y divide-muted">
                <Field label="Buyer">{lead.organisation || "Not stated"}</Field>
                <Field label="Platform">{lead.platform}</Field>
                {lead.ref_no ? <Field label="Reference">{lead.ref_no}</Field> : null}
                <Field label="Closes">{lead.closing_label || when(lead.closing_at)}</Field>
                <Field label="Value">{inr(lead.value_inr)}</Field>
                <Field label="EMD">{inr(lead.emd_inr)}</Field>
                <Field label="Type">{lead.opportunity_type}</Field>
                <Field label="Filter">{lead.decision || "Not assessed"}</Field>
              </dl>
              {lead.official_url ? (
                <a
                  href={lead.official_url}
                  className="mt-3 inline-flex h-9 items-center gap-2 rounded-lg border border-muted bg-action-secondary px-3 text-label-sm text-action-secondary-content hover:bg-action-secondary-hover"
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={14} strokeWidth={1.75} />
                  Official listing
                </a>
              ) : null}
              {lead.evidence_line ? <p className="mt-3 break-words text-body-sm text-tertiary">{lead.evidence_line}</p> : null}
            </Panel>

            {lead.flags.length > 0 ? (
              <Panel title="Flags" bodyClassName="space-y-2 p-4">
                {lead.flags.map((flag) => (
                  <p key={flag} className="flex items-start gap-2 text-body-sm text-warning">
                    <TriangleAlert size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
                    {flag}
                  </p>
                ))}
              </Panel>
            ) : null}
            {lead.reasons.length > 0 ? (
              <Panel title="Rule trace" bodyClassName="space-y-1 p-4">
                {lead.reasons.map((reason) => (
                  <p key={reason} className="text-body-sm text-secondary">{reason}</p>
                ))}
              </Panel>
            ) : null}
          </div>
        </div>
      </PageBody>
    </>
  );
}
