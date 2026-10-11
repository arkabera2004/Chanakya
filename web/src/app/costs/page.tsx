"use client";

import { Gauge } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { SpendChart } from "@/components/spend-chart";
import { EmptyState, Metric, PageBody, PageHeader, Panel, StatusChip, Tag } from "@/components/ui";
import { EmptyCosts } from "@/components/vira/art";
import { useDesk } from "@/context/desk";
import { insights, type Costs, type Usage } from "@/lib/api-insights";
import { budgetReason, count, triggerLabel, usd, when } from "@/lib/format";

const PURPOSES: Record<string, { label: string; note: string }> = {
  triage: { label: "Triage", note: "A short yes or no on the fast model, only for notices the rules could not decide." },
  deep: { label: "Deep analysis", note: "The larger model reads the documents of a strong match." },
};

/** How much of a cap is used. The words carry the figures; the bar repeats them. */
function Meter({ label, used, cap, format, note }: { label: string; used: number; cap: number; format: (value: number) => string; note?: string }) {
  const share = cap > 0 ? used / cap : 0;
  const reached = cap > 0 && used >= cap;
  const percent = share > 0 && share < 0.01 ? "under 1%" : `${Math.round(share * 100)}%`;
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-label-md text-primary">{label}</span>
        <span className="text-body-sm text-secondary tabular">
          {format(used)} of {format(cap)}
          <span className="text-tertiary"> · {percent}</span>
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-raised-2" aria-hidden="true">
        {/* A real spend keeps a sliver of bar, so a tiny share does not look like none. */}
        <div className="h-full rounded-full bg-vira" style={{ width: `${used > 0 ? Math.max(1, Math.min(100, share * 100)) : 0}%` }} />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-body-sm text-tertiary">
        {reached ? (
          <StatusChip tone="warning">
            <Gauge size={14} strokeWidth={1.75} />
            Cap reached
          </StatusChip>
        ) : null}
        {note ? <span>{note}</span> : null}
      </div>
    </div>
  );
}

function UsageTable({ caption, first, rows }: { caption: string; first: string; rows: { key: string; name: ReactNode; usage: Usage }[] }) {
  const cell = "px-4 py-2.5 text-right";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-body-md tabular">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-caption uppercase tracking-wide text-quaternary">
          <tr className="border-b border-muted">
            <th scope="col" className="px-4 py-2.5 font-medium">{first}</th>
            <th scope="col" className={`${cell} font-medium`}>Calls</th>
            <th scope="col" className={`${cell} hidden font-medium sm:table-cell`}>Cache hits</th>
            <th scope="col" className={`${cell} hidden font-medium sm:table-cell`}>Refused</th>
            <th scope="col" className={`${cell} hidden font-medium md:table-cell`}>Tokens in</th>
            <th scope="col" className={`${cell} hidden font-medium md:table-cell`}>Tokens out</th>
            <th scope="col" className={`${cell} font-medium`}>Cost</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-muted text-secondary">
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row" className="px-4 py-2.5 font-normal text-primary">{row.name}</th>
              <td className={cell}>{count(row.usage.calls)}</td>
              <td className={`${cell} hidden sm:table-cell`}>{count(row.usage.cache_hits)}</td>
              <td className={`${cell} hidden sm:table-cell`}>{count(row.usage.blocked)}</td>
              <td className={`${cell} hidden md:table-cell`}>{count(row.usage.input_tokens)}</td>
              <td className={`${cell} hidden md:table-cell`}>{count(row.usage.output_tokens)}</td>
              <td className={`${cell} text-primary`}>{usd(row.usage.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function callsHint(usage: Usage): string {
  return `${count(usage.calls)} ${usage.calls === 1 ? "call" : "calls"}`;
}

function Loaded({ data }: { data: Costs }) {
  const { budget, spend, totals, waiting } = data;
  const last = data.last_run;
  const lastCalls = last ? last.triage_calls + last.deep_calls : 0;
  const anything = totals.calls + totals.cache_hits + totals.blocked > 0;
  const free = data.llm_mode !== "cloud";
  const unpriced = data.by_model.filter((row) => !row.priced && row.calls > 0);

  return (
    <>
      {free ? (
        <p className="mb-3 text-body-sm text-tertiary">
          {data.llm_mode === "fake"
            ? "This desk runs on the simulated model. Its calls are counted and recorded at zero cost."
            : data.llm_mode === "local"
              ? "The model runs on your own hardware. Calls and tokens are counted, and no cost per token is recorded."
              : "The model is switched off, so passes use rules and lexical matching only."}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Today" value={usd(spend.today.cost_usd)} hint={`${callsHint(spend.today)} since midnight IST`} accent />
        <Metric label="Last 7 days" value={usd(spend.last_7_days.cost_usd)} hint={callsHint(spend.last_7_days)} />
        <Metric label={`Last ${data.window_days} days`} value={usd(spend.last_30_days.cost_usd)} hint={callsHint(spend.last_30_days)} />
        <Metric
          label="Waiting for the model"
          value={count(waiting.budget)}
          hint={waiting.budget > 0 ? "Refused by the budget. Retried on the next pass." : "No notice is held back by the budget"}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Against the caps" bodyClassName="space-y-5 p-4">
          <Meter
            label="Spend today"
            used={spend.today.cost_usd}
            cap={budget.max_cost_per_day_usd}
            format={usd}
            note="The day runs from midnight IST. A call that would pass the cap is refused before it is made."
          />
          {last ? (
            <>
              <Meter label="Spend on the last pass" used={last.llm_cost_usd} cap={budget.max_cost_per_run_usd} format={usd} />
              <Meter
                label="Model calls on the last pass"
                used={lastCalls}
                cap={budget.max_calls_per_run}
                format={(value) => count(value)}
                note={`${count(last.triage_calls)} triage and ${count(last.deep_calls)} deep. At most ${count(budget.max_deep_analyses_per_run)} deep analyses per pass.`}
              />
              <p className="text-body-sm text-tertiary">
                Last pass: {triggerLabel(last.trigger).toLowerCase()}, {when(last.finished_at ?? last.started_at)}.{" "}
                {last.budget_blocked_reasons.length > 0 ? <span className="text-warning">{last.budget_blocked_reasons.map(budgetReason).join(" ")} </span> : null}
                <Link href="/runs" className="text-secondary underline underline-offset-2 hover:text-primary">
                  See every pass
                </Link>
              </p>
            </>
          ) : (
            <p className="text-body-sm text-tertiary">
              No pass has finished yet. Each pass may spend up to {usd(budget.max_cost_per_run_usd)} over {count(budget.max_calls_per_run)} model calls.
            </p>
          )}
        </Panel>

        <Panel title="Saved and held back" bodyClassName="p-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-4">
            {[
              { label: "Cache hits", value: totals.cache_hits, note: "Answers reused at no cost" },
              { label: "Refused by the budget", value: totals.blocked, note: "Calls stopped before they were made" },
              { label: "Notices waiting on budget", value: waiting.budget, note: "Scored by rules for now" },
              { label: "Notices waiting on documents", value: waiting.documents, note: "No deep analysis on a title alone" },
            ].map((item) => (
              <div key={item.label}>
                <dt className="text-caption uppercase tracking-wide text-quaternary">{item.label}</dt>
                <dd className="mt-0.5 text-heading-md text-primary tabular">{count(item.value)}</dd>
                <dd className="mt-0.5 text-body-sm text-tertiary">{item.note}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-body-sm text-tertiary">
            Cache hits and refused calls are for the last {data.window_days} days. The waiting counts are the active book right now.
          </p>
        </Panel>
      </div>

      {anything ? (
        <>
          <Panel title={`Spend per day, last ${data.window_days} days`} className="mt-4" bodyClassName="p-4">
            {totals.cost_usd > 0 ? (
              <SpendChart days={data.daily} />
            ) : (
              <p className="text-body-md text-tertiary">
                No cost was recorded in these {data.window_days} days. {count(totals.calls)} {totals.calls === 1 ? "call was" : "calls were"} made
                {free ? " on a model that is not billed per token." : "."}
              </p>
            )}
          </Panel>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Panel title="By purpose">
              <UsageTable
                caption={`Model usage by purpose, last ${data.window_days} days`}
                first="Purpose"
                rows={data.by_purpose.map((row) => ({
                  key: row.purpose,
                  name: (
                    <>
                      {PURPOSES[row.purpose]?.label ?? row.purpose}
                      {PURPOSES[row.purpose] ? <span className="mt-0.5 block min-w-44 text-body-sm text-tertiary">{PURPOSES[row.purpose].note}</span> : null}
                    </>
                  ),
                  usage: row,
                }))}
              />
            </Panel>
            <Panel title="By model">
              <UsageTable
                caption={`Model usage by model, last ${data.window_days} days`}
                first="Model"
                rows={data.by_model.map((row) => ({
                  key: row.model,
                  name: (
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {row.model}
                      {!row.priced && !free ? <Tag>No price in the config</Tag> : null}
                    </span>
                  ),
                  usage: row,
                }))}
              />
              {unpriced.length > 0 && !free ? (
                <p className="border-t border-muted px-4 py-3 text-body-sm text-warning">
                  {unpriced.map((row) => row.model).join(", ")} {unpriced.length === 1 ? "has" : "have"} no price in the config, so{" "}
                  {unpriced.length === 1 ? "its" : "their"} calls are recorded at zero. The true spend is higher than shown.
                </p>
              ) : null}
            </Panel>
          </div>
          <p className="mt-3 text-body-sm text-tertiary">
            Costs are estimates from the price list in the config and the token counts each call reported. A refused call and a cache hit are never counted as a call.
          </p>
        </>
      ) : (
        <div className="mt-4 overflow-hidden rounded-xl border border-muted bg-container">
          <EmptyState art={<EmptyCosts />} title="No model calls yet">
            Nothing has been sent to a model in the last {data.window_days} days. Rules and lexical matching are free, so a pass can finish without one.
          </EmptyState>
        </div>
      )}
    </>
  );
}

export default function CostsPage() {
  const { job } = useDesk();
  const [data, setData] = useState<Costs | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load once, and again when a pass the desk started changes state.
  useEffect(() => {
    let cancelled = false;
    insights.costs()
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

  return (
    <>
      <PageHeader
        title="Costs"
        description="What the model has cost, set against the caps in the config. Most notices are decided by rules for free, so only triage and deep analysis appear here."
      />
      <PageBody>
        {error ? (
          <p className="mb-3 rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">
            {data ? "The figures below could not be refreshed. " : "The costs could not be loaded. "}
            {error}
          </p>
        ) : null}
        {data === null && !error ? <p className="mb-3 text-body-md text-tertiary">Loading costs…</p> : null}
        {data ? <Loaded data={data} /> : null}
      </PageBody>
    </>
  );
}
