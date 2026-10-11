"use client";

import { CalendarClock, EyeOff, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Select } from "@/components/select";
import { Button, EmptyState, PageBody, PageHeader, Priority, Score, Tag } from "@/components/ui";
import { EmptyLeads } from "@/components/vira/art";
import { api, type Lead } from "@/lib/api";
import { capabilityLabel, insights, type Capability } from "@/lib/api-insights";
import { closingSoon, count, daysLeft, shortDate } from "@/lib/format";

const PAGE = 200;

const REVIEWS = ["New", "Reviewing", "Shortlisted", "Partner needed", "Rejected", "Pursuing"];

function LeadRow({ lead }: { lead: Lead }) {
  const fit = lead.capabilities[0];
  const left = daysLeft(lead.closing_at);
  const soon = closingSoon(lead.closing_at);
  return (
    <li>
      <Link href={`/leads/${lead.id}`} className="flex items-start gap-3 px-4 py-3.5 hover:bg-raised sm:gap-4">
        <Priority value={lead.priority} />
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-label-md text-primary">{lead.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-sm text-tertiary">
            {fit ? <Tag>{fit.offering?.split(",")[0] || fit.id}</Tag> : null}
            <span>{lead.organisation || "Buyer not stated"}</span>
            {lead.state ? <span>{lead.state}</span> : null}
            <span className="text-quaternary">{lead.platform}</span>
            {lead.review_status !== "New" ? <Tag>{lead.review_status}</Tag> : null}
          </div>
          <p className="mt-1.5 line-clamp-1 text-body-sm text-secondary">{lead.why || lead.reasons[0] || "No reason recorded"}</p>
        </div>
        <div className="hidden w-28 shrink-0 text-right sm:block">
          <div className={`inline-flex items-center gap-1 text-label-sm ${soon ? "text-warning" : "text-secondary"}`}>
            {soon ? <CalendarClock size={14} strokeWidth={1.75} /> : null}
            {lead.closing_at ? shortDate(lead.closing_at) : "No close date"}
          </div>
          {left ? <div className="mt-0.5 text-body-sm text-tertiary">{left}</div> : null}
        </div>
        <div className="w-14 shrink-0 text-right">
          <Score value={lead.score} />
        </div>
      </Link>
    </li>
  );
}

export default function LeadsPage() {
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("");
  const [includeRejected, setIncludeRejected] = useState(false);
  const [closing, setClosing] = useState("");
  const [capability, setCapability] = useState("");
  const [stateName, setStateName] = useState("");
  const [reviewStatus, setReviewStatus] = useState("");
  const [rows, setRows] = useState<Lead[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  // The desk metrics link here with a band already chosen.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const band = params.get("priority");
    if (band && ["P1", "P2", "P3", "P4"].includes(band)) setPriority(band);
    if (params.get("set_aside")) setIncludeRejected(true);
    const fit = params.get("capability");
    if (fit && /^CAP-[A-Z0-9-]+$/.test(fit)) setCapability(fit);
  }, []);

  // The capability names come from the knowledge base. Without them the other filters still work.
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  useEffect(() => {
    let cancelled = false;
    insights.capabilities()
      .then((data) => {
        if (!cancelled) setCapabilities(data.capabilities);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const filters = {
    q: query,
    priority: priority || undefined,
    includeRejected,
    closingWithinDays: closing ? Number(closing) : undefined,
    capability: capability || undefined,
    state: stateName || undefined,
    reviewStatus: reviewStatus || undefined,
  };
  const narrowed = !!(query || priority || includeRejected || closing || capability || stateName || reviewStatus);

  function clear() {
    setQuery("");
    setPriority("");
    setIncludeRejected(false);
    setClosing("");
    setCapability("");
    setStateName("");
    setReviewStatus("");
  }

  function more() {
    if (!rows) return;
    setLoadingMore(true);
    api.leads({ ...filters, limit: PAGE, offset: rows.length })
      .then((data) => {
        setRows((prev) => {
          const seen = new Set((prev ?? []).map((row) => row.id));
          return [...(prev ?? []), ...data.opportunities.filter((row) => !seen.has(row.id))];
        });
        setTotal(data.total);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoadingMore(false));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const handle = window.setTimeout(() => {
      api.leads({ ...filters, limit: PAGE })
        .then((data) => {
          if (cancelled) return;
          setRows(data.opportunities);
          setTotal(data.total);
          setError(null);
        })
        .catch((err: Error) => {
          if (!cancelled) setError(err.message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [query, priority, includeRejected, closing, capability, stateName, reviewStatus]);

  const visible = rows;
  const field = "h-9 rounded-lg border border-muted bg-container px-2.5 text-body-md text-primary hover:border-default";

  return (
    <>
      <PageHeader
        title="Leads"
        description="The default book is P1 to P3 and anything still unscored. P4 and rejected rows stay out until you ask for them."
      />
      <PageBody>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="relative w-full sm:max-w-xs">
            <Search size={14} strokeWidth={1.75} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-tertiary" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title, buyer, or id"
              aria-label="Search leads"
              className={`${field} w-full pl-8 placeholder:text-quaternary`}
            />
          </label>
          <Select
            ariaLabel="Priority"
            value={priority}
            onChange={setPriority}
            options={[{ value: "", label: "P1 to P3 and unscored" }, ...["P1", "P2", "P3", "P4"].map((item) => ({ value: item, label: item }))]}
          />
          <Select
            ariaLabel="Closing window"
            value={closing}
            onChange={setClosing}
            options={[
              { value: "", label: "Any closing date" },
              { value: "7", label: "Closes within 7 days" },
              { value: "14", label: "Closes within 14 days" },
              { value: "30", label: "Closes within 30 days" },
            ]}
          />
          <Select
            ariaLabel="Capability"
            className="max-w-full sm:max-w-64"
            value={capability}
            onChange={setCapability}
            options={[
              { value: "", label: "Any capability" },
              ...(capability && !capabilities.some((item) => item.id === capability) ? [{ value: capability, label: capability }] : []),
              ...capabilities.map((item) => ({ value: item.id, label: capabilityLabel(item) })),
            ]}
          />
          <input
            value={stateName}
            onChange={(event) => setStateName(event.target.value)}
            placeholder="Buyer state"
            className={`${field} w-32 placeholder:text-quaternary`}
            aria-label="Buyer state"
          />
          <Select
            ariaLabel="Review status"
            value={reviewStatus}
            onChange={setReviewStatus}
            options={[{ value: "", label: "Any review" }, ...REVIEWS.map((item) => ({ value: item, label: item }))]}
          />
          <label className={`inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border px-2.5 text-label-sm ${includeRejected ? "border-default bg-raised text-primary" : "border-muted text-secondary hover:border-default"}`}>
            <input type="checkbox" className="sr-only" checked={includeRejected} onChange={(event) => setIncludeRejected(event.target.checked)} />
            <EyeOff size={14} strokeWidth={1.75} />
            Include what she set aside
          </label>
          {narrowed ? (
            <Button variant="ghost" onClick={clear}>
              <X size={14} strokeWidth={1.75} />
              Clear
            </Button>
          ) : null}
        </div>

        <p className="mb-2 text-body-sm text-tertiary" aria-live="polite">
          {error
            ? null
            : visible === null
              ? "Loading the book…"
              : `${loading ? "Updating. " : ""}Showing ${count(visible.length)} of ${count(total ?? visible.length)}, strongest first.`}
        </p>
        {error ? <p className="mb-3 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">{error}</p> : null}

        <div className="overflow-hidden rounded-xl border border-muted bg-container">
          {visible && visible.length > 0 ? (
            <ol className="divide-y divide-muted">
              {visible.map((lead) => (
                <LeadRow key={lead.id} lead={lead} />
              ))}
            </ol>
          ) : null}
          {visible && visible.length === 0 && !error ? (
            <EmptyState
              art={<EmptyLeads />}
              title="No notices match"
              action={narrowed ? <Button variant="secondary" onClick={clear}>Clear the filters</Button> : undefined}
            >
              {narrowed
                ? "Nothing in the book fits these filters."
                : "Run a pass from the desk, or include what she set aside."}
            </EmptyState>
          ) : null}
          {visible === null && !error ? (
            <div className="divide-y divide-muted" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((row) => (
                <div key={row} className="flex animate-pulse gap-4 px-4 py-4">
                  <div className="h-6 w-9 rounded-md bg-raised-2" />
                  <div className="flex-1 space-y-2">
                    <div className="h-4 w-2/3 rounded bg-raised-2" />
                    <div className="h-3 w-1/3 rounded bg-raised" />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {visible && total !== null && visible.length < total ? (
          <div className="mt-3">
            <Button variant="secondary" onClick={more} disabled={loadingMore}>
              {loadingMore ? "Loading…" : `Show ${Math.min(PAGE, total - visible.length)} more`}
            </Button>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}
