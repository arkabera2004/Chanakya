"use client";

import Link from "next/link";

import { ArrowRight, CalendarClock, Gauge, ListChecks, Play, Radio, Square } from "lucide-react";

import { Button, EmptyState, Metric, PageBody, PageHeader, Panel, Priority, Score, Tag } from "@/components/ui";
import { EmptyPasses } from "@/components/vira/art";
import { WayamOrb } from "@/components/vira/orb";
import { useDesk } from "@/context/desk";
import type { Lead, TraceEvent } from "@/lib/api";
import { budgetHit, clock, closingSoon, count, daysLeft, money, shortDate, triggerLabel } from "@/lib/format";

const STAGES = [
  { id: "acquire", label: "Open sources", copy: "Read the official listing. A blocked portal is reported, never bypassed." },
  { id: "match", label: "Match capabilities", copy: "Rules and lexical similarity against the Wayam book. Free." },
  { id: "judge", label: "Keep or set aside", copy: "Unrelated notices stop here. Only the grey band and strong matches can call a model." },
  { id: "recommend", label: "Recommend", copy: "P1 to P4, a written reason, eligibility, and one next action." },
];

const KIND_LABEL: Record<string, string> = {
  acquire: "Source",
  match: "Match",
  judge: "Decision",
  recommend: "Result",
  error: "Error",
};

function Stages({ active }: { active: string | null }) {
  const at = STAGES.findIndex((stage) => stage.id === active);
  return (
    <ol className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
      {STAGES.map((stage, index) => {
        const on = index === at;
        const passed = at >= 0 && index < at;
        return (
          <li key={stage.id} className="relative flex gap-3 xl:flex-col xl:gap-2.5">
            <div className="flex items-center gap-3">
              <span
                className={[
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-label-sm tabular",
                  on ? "beat border-vira bg-vira text-on-color" : passed ? "border-vira text-vira" : "border-default text-tertiary",
                ].join(" ")}
              >
                {index + 1}
              </span>
              {index < STAGES.length - 1 ? (
                <span className={`hidden h-px flex-1 xl:block ${passed ? "bg-vira" : "bg-default"}`} />
              ) : null}
            </div>
            <div className="min-w-0">
              <div className={`text-label-md ${on || passed ? "text-primary" : "text-secondary"}`}>{stage.label}</div>
              <p className="mt-0.5 text-body-sm text-tertiary">{stage.copy}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function EventRow({ event }: { event: TraceEvent }) {
  const body = (
    <>
      <div className="flex items-center gap-2 text-caption text-quaternary">
        <span className="tabular">{clock(event.at)}</span>
        <span className="uppercase tracking-wide">{KIND_LABEL[event.kind] ?? event.kind}</span>
        {event.kind === "judge" && event.priority ? <Priority value={event.priority} /> : null}
      </div>
      <p className={`mt-1 text-body-md ${event.kind === "error" ? "text-error" : "text-primary"}`}>{event.message}</p>
    </>
  );
  const frame = "settle block rounded-lg border border-transparent bg-raised px-3 py-2";
  return (
    <li>
      {event.opportunity_id ? (
        <Link href={`/leads/${event.opportunity_id}`} className={`${frame} hover:border-default`}>
          {body}
        </Link>
      ) : (
        <div className={frame}>{body}</div>
      )}
    </li>
  );
}

function LeadRow({ lead }: { lead: Lead }) {
  const fit = lead.capabilities[0];
  const left = daysLeft(lead.closing_at);
  return (
    <Link href={`/leads/${lead.id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-raised">
      <Priority value={lead.priority} />
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-label-md text-primary">{lead.title}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-sm text-tertiary">
          {fit ? <Tag>{fit.offering?.split(",")[0] || fit.id}</Tag> : null}
          <span className="truncate">{lead.organisation || "Buyer not stated"}</span>
          {lead.closing_at ? (
            <span className={`inline-flex items-center gap-1 ${closingSoon(lead.closing_at) ? "text-warning" : ""}`}>
              <CalendarClock size={14} strokeWidth={1.75} />
              {shortDate(lead.closing_at)}
              {left ? `, ${left}` : ""}
            </span>
          ) : null}
        </div>
      </div>
      <Score value={lead.score} />
    </Link>
  );
}

export default function DeskPage() {
  const { brief, job, busy, writerBusy, error, start, startWatch, stopWatch, analyze, watchStatus, events: fileEvents } = useDesk();
  const watching = writerBusy && watchStatus?.mode === "watch";
  const scoring = (busy && job?.kind === "analyze") || (writerBusy && watchStatus?.mode === "analyze");
  const ownJob = job ? job.events : [];
  const events = watching ? fileEvents : ownJob.length > 0 ? ownJob : fileEvents;
  const active = scoring ? "judge" : busy ? (job?.stage ?? "acquire") : watching ? "acquire" : null;
  const voice = brief?.vira;
  const demo = !!brief?.demo;
  const locked = busy || writerBusy;
  const last = brief?.last_run ?? null;
  const capped = budgetHit(last);
  const waiting = last?.llm_skipped_budget ?? 0;
  // Newest first: the line Vira just wrote is the one a person wants to read.
  const feed = [...events].reverse();

  return (
    <>
      <PageHeader
        title="Discovery desk"
        description="Vira runs the Chanakya pipeline in the open. You see the source, the rule, the score, and the action."
        actions={
          <>
            <Button onClick={() => start().catch(() => undefined)} disabled={locked}>
              <Play size={14} strokeWidth={1.75} />
              {busy && job?.kind !== "analyze" ? "Pass in progress" : demo ? "Run a demo pass" : "Scan portals"}
            </Button>
            {watching ? (
              <Button variant="secondary" onClick={() => stopWatch().catch(() => undefined)}>
                <Square size={14} strokeWidth={1.75} />
                Stop watch
              </Button>
            ) : null}
            {!watching && !demo ? (
              <Button variant="secondary" onClick={() => startWatch().catch(() => undefined)} disabled={locked}>
                <Radio size={14} strokeWidth={1.75} />
                Watch portals
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => analyze().catch(() => undefined)} disabled={locked}>
              <ListChecks size={14} strokeWidth={1.75} />
              Score stored notices
            </Button>
          </>
        }
      />
      <PageBody>
        {error ? <p className="mb-4 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">{error}</p> : null}

        <section className="vira-glow overflow-hidden rounded-xl border border-muted bg-container">
          <div className="flex gap-4 p-4 sm:gap-5 sm:p-5">
            <span className="shrink-0 sm:hidden">
              <WayamOrb id="brief-sm" size={48} mood={locked ? "focused" : voice?.mood ?? "curious"} talking={locked} />
            </span>
            <span className="hidden shrink-0 sm:block">
              <WayamOrb id="brief" size={72} mood={locked ? "focused" : voice?.mood ?? "curious"} talking={locked} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-caption uppercase tracking-wide text-quaternary">
                Vira
                <span className="normal-case tracking-normal text-tertiary">
                  {demo ? "Demo data, synthetic notices" : "Public procurement portals"}
                </span>
              </div>
              <p className="mt-1.5 max-w-[70ch] text-body-lg text-primary">{voice?.greeting ?? "Connecting to the desk…"}</p>
              {watching ? (
                <p className="mt-2 inline-flex items-center gap-1.5 text-body-md text-secondary">
                  <Radio size={14} strokeWidth={1.75} className="shrink-0 animate-pulse text-vira" />
                  A watch holds the database{watchStatus?.source_name ? `, on ${watchStatus.source_name}` : ""}.
                  {watchStatus?.pages != null ? ` ${count(watchStatus.pages)} pages read.` : ""}
                  {watchStatus?.notices_stored != null ? ` ${count(watchStatus.notices_stored)} notices stored.` : ""}
                </p>
              ) : null}
              {scoring ? <p className="mt-2 text-body-md text-secondary">Scoring stored notices. This does not open a portal.</p> : null}
              {capped && !locked ? (
                <p className="mt-2 flex items-start gap-1.5 text-body-md text-warning">
                  <Gauge size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
                  <span>
                    The model budget was hit on the last pass.
                    {waiting > 0 ? ` ${count(waiting)} notices still wait for a model call. Score stored notices to continue.` : ""}
                  </span>
                </p>
              ) : null}
            </div>
          </div>
          <div className="border-t border-muted px-5 py-4">
            <Stages active={active} />
          </div>
        </section>

        <div className="mt-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
          <Metric label="Notices read" value={count(brief?.counts.notices)} hint={brief ? `${count(brief.counts.active)} still open` : undefined} href="/leads" />
          <Metric label="P1 now" value={count(brief?.counts.p1)} hint="Decide these first" href="/leads?priority=P1" accent />
          <Metric label="P2 now" value={count(brief?.counts.p2)} hint="Review the documents" href="/leads?priority=P2" />
          <Metric
            label="Set aside"
            value={count(brief?.counts.rejected)}
            hint={last ? `Last model cost ${money(last.llm_cost_usd)}` : "No model call yet"}
            href="/leads?set_aside=1"
          />
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <Panel
            title="Worth a decision"
            action={
              <Link href="/leads" className="inline-flex items-center gap-1 text-label-sm text-secondary hover:text-primary">
                All leads
                <ArrowRight size={14} strokeWidth={1.75} />
              </Link>
            }
            bodyClassName="divide-y divide-muted"
          >
            {(brief?.leads.length ?? 0) === 0 ? (
              <EmptyState title={brief ? "Nothing in P1 to P3 yet" : "Reading the book…"}>
                {brief ? "Run a pass and the notices worth a decision land here, strongest first." : undefined}
              </EmptyState>
            ) : (
              brief?.leads.map((lead) => <LeadRow key={lead.id} lead={lead} />)
            )}
          </Panel>

          <Panel
            title="This pass"
            action={last ? <span className="text-body-sm text-tertiary">{triggerLabel(last.trigger)}, {last.status}</span> : null}
            bodyClassName="max-h-[30rem] overflow-y-auto p-3"
          >
            {feed.length === 0 ? (
              <EmptyState art={<EmptyPasses />} title={last ? "Standing by" : "No pass yet"}>
                {last
                  ? "Start a pass and this column fills line by line with each source Vira opens and each notice she keeps or sets aside."
                  : "Start one and this column fills with each notice Vira keeps or sets aside."}
              </EmptyState>
            ) : (
              <ol className="space-y-2">
                {feed.map((event, index) => (
                  <EventRow key={`${event.at}-${feed.length - index}`} event={event} />
                ))}
              </ol>
            )}
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
