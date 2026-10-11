"use client";

import { Select } from "@/components/select";
import { ArrowRight, CalendarClock } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { EmptyState, PageBody, PageHeader, Panel, Priority, Score, StatusChip, Tag } from "@/components/ui";
import { EmptyProducts } from "@/components/vira/art";
import { useDesk } from "@/context/desk";
import { capabilityLabel, insights, offeringName, type Band, type ProductGroup, type Products } from "@/lib/api-insights";
import { closingSoon, count, daysLeft, shortDate } from "@/lib/format";

const BANDS: Band[] = ["P1", "P2", "P3"];
const VIEW_KEY = "chanakya-products-view";

// How Wayam can deliver the capability, as the knowledge base records it.
const DELIVERY: Record<string, string> = {
  existing_offering: "Existing offering",
  custom_build: "Custom build",
  partner: "With a partner",
};

function BandCounts({ bands }: { bands: Record<Band, number> }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {BANDS.map((band) => (
        <span key={band} className={`inline-flex items-center gap-1.5 text-label-sm tabular ${bands[band] > 0 ? "text-secondary" : "text-quaternary"}`}>
          <Priority value={band} />
          {count(bands[band])}
        </span>
      ))}
    </span>
  );
}

function Section({ group }: { group: ProductGroup }) {
  const offering = offeringName(group.offering);
  return (
    <Panel>
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-muted px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-heading-sm text-primary">{offering ?? group.name}</h3>
          {offering ? <p className="mt-0.5 text-body-md text-secondary">{group.name}</p> : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Tag>{group.id}</Tag>
            <StatusChip>{DELIVERY[group.offering_status] ?? group.offering_status}</StatusChip>
          </div>
          {group.offering !== offering ? <p className="mt-1.5 max-w-[80ch] text-body-sm text-tertiary">{group.offering}</p> : null}
        </div>
        {group.total > 0 ? <BandCounts bands={group.bands} /> : null}
      </header>

      {group.total === 0 ? (
        <p className="px-4 py-3 text-body-md text-tertiary">No active P1 to P3 notice leads with this capability.</p>
      ) : (
        <ol className="divide-y divide-muted">
          {group.notices.map((lead) => {
            const soon = closingSoon(lead.closing_at);
            const left = daysLeft(lead.closing_at);
            return (
              <li key={lead.id}>
                <Link href={`/leads/${lead.id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-raised sm:gap-4">
                  <Priority value={lead.priority} />
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-2 text-label-md text-primary">{lead.title}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-sm text-tertiary">
                      <span>{lead.organisation || "Buyer not stated"}</span>
                      {lead.state ? <span>{lead.state}</span> : null}
                      <span className={`inline-flex items-center gap-1 ${soon ? "text-warning" : ""}`}>
                        {soon ? <CalendarClock size={14} strokeWidth={1.75} /> : null}
                        {lead.closing_at ? `Closes ${shortDate(lead.closing_at)}${left ? `, ${left}` : ""}` : "No close date"}
                      </span>
                      {lead.eligibility ? <span className="text-quaternary">{lead.eligibility}</span> : null}
                    </div>
                  </div>
                  <div className="w-14 shrink-0 text-right">
                    <Score value={lead.score} />
                  </div>
                </Link>
              </li>
            );
          })}
        </ol>
      )}

      {group.total > 0 ? (
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-muted px-4 py-2.5 text-body-sm text-tertiary">
          <span>
            {group.more > 0
              ? `Showing the strongest ${count(group.notices.length)} of ${count(group.total)}.`
              : `${count(group.total)} ${group.total === 1 ? "notice" : "notices"}, strongest first.`}
          </span>
          <Link href={`/leads?capability=${encodeURIComponent(group.id)}`} className="inline-flex items-center gap-1 text-label-sm text-secondary hover:text-primary">
            Every notice that mentions it, in Leads
            <ArrowRight size={14} strokeWidth={1.75} />
          </Link>
        </footer>
      ) : null}
    </Panel>
  );
}

export default function ProductsPage() {
  const { job } = useDesk();
  const [data, setData] = useState<Products | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState("");

  // A product owner comes back to the offering they last looked at.
  useEffect(() => {
    try {
      setView(window.localStorage.getItem(VIEW_KEY) ?? "");
    } catch {
      /* private window: the page works without the saved view */
    }
  }, []);

  function choose(next: string) {
    setView(next);
    try {
      if (next) window.localStorage.setItem(VIEW_KEY, next);
      else window.localStorage.removeItem(VIEW_KEY);
    } catch {
      /* nothing to save to */
    }
  }

  useEffect(() => {
    let cancelled = false;
    insights.products(8)
      .then((next) => {
        if (cancelled) return;
        setData(next);
        setError(null);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [job?.status]);

  const groups = data?.groups ?? [];
  // A saved view for a capability that has left the book falls back to everything.
  const chosen = groups.some((group) => group.id === view) ? view : "";
  const visible = chosen ? groups.filter((group) => group.id === chosen) : groups;
  const withBook = groups.filter((group) => group.total > 0).length;
  const field = "h-9 max-w-full rounded-lg border border-muted bg-container px-2.5 text-body-md text-primary hover:border-default";

  return (
    <>
      <PageHeader
        title="Products"
        description="The active P1 to P3 book, grouped by the Wayam offering that leads each match. It is the Product-Wise sheet of the workbook, as a page."
      />
      <PageBody>
        {error ? (
          <p className="mb-3 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">
            {data ? "The book below could not be refreshed. " : "The product view could not be loaded. "}
            {error}
          </p>
        ) : null}
        {data === null && !error ? <p className="mb-3 text-body-md text-tertiary">Loading the product view…</p> : null}

        {data && data.total === 0 ? (
          <div className="overflow-hidden rounded-xl border border-muted bg-container">
            <EmptyState art={<EmptyProducts />} title="No active leads to group">
              Nothing active is in P1 to P3 yet. Run a pass from the desk, and each offering will list the notices it leads.
            </EmptyState>
          </div>
        ) : null}

        {data && data.total > 0 ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
              <Select
                ariaLabel="Offering"
                className="w-full sm:w-80"
                value={chosen}
                onChange={choose}
                options={[
                  { value: "", label: "All offerings" },
                  ...groups.map((group) => ({ value: group.id, label: capabilityLabel(group), hint: count(group.total) })),
                ]}
              />
              <BandCounts bands={data.bands} />
            </div>
            <p className="mb-3 text-body-sm text-tertiary" aria-live="polite">
              {count(data.total)} active {data.total === 1 ? "notice" : "notices"} across {count(withBook)} of {count(groups.length)} capabilities. A notice
              is listed once, under the capability with its strongest match.
              {chosen ? " This view is remembered on this browser." : ""}
              {data.ungrouped > 0
                ? ` ${count(data.ungrouped)} ${data.ungrouped === 1 ? "notice has" : "notices have"} no leading capability in the book and ${data.ungrouped === 1 ? "is" : "are"} not listed here.`
                : ""}
            </p>
            <div className="space-y-4">
              {visible.map((group) => (
                <Section key={group.id} group={group} />
              ))}
            </div>
          </>
        ) : null}
      </PageBody>
    </>
  );
}
