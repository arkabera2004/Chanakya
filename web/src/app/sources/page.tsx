"use client";

import { Building2, CircleAlert, CircleCheck, CircleDashed, FolderInput, Landmark, ShieldBan, type LucideIcon } from "lucide-react";

import { EmptyState, Metric, PageBody, PageHeader, Panel, StatusChip, type Tone } from "@/components/ui";
import { EmptySources } from "@/components/vira/art";
import { useDesk } from "@/context/desk";
import type { ConfiguredSource, SourceRow } from "@/lib/api";
import { count, when } from "@/lib/format";

const GROUPS: { type: string; title: string; icon: LucideIcon; note?: string }[] = [
  { type: "cppp", title: "Central feeds (CPPP)", icon: Landmark, note: "Paged listings. Vira follows them until the date window or the last page." },
  { type: "gepnic", title: "GePNIC portals", icon: Building2 },
  { type: "other", title: "Manual and fixture inboxes", icon: FolderInput, note: "Files a person drops in. The compliant path for portals that do not allow automated reading." },
];

type Health = { tone: Tone; label: string; icon: LucideIcon };

function healthOf(source: SourceRow | undefined): Health {
  const health = source?.health ?? "unknown";
  if (source?.restricted || health === "restricted") return { tone: "neutral", label: "Restricted", icon: ShieldBan };
  if (health === "failing" || (source?.consecutive_failures ?? 0) > 0) return { tone: "error", label: "Failing", icon: CircleAlert };
  if (health === "ok") return { tone: "success", label: "Healthy", icon: CircleCheck };
  return { tone: "neutral", label: "Not contacted", icon: CircleDashed };
}

function SourceLine({ source, seen }: { source: ConfiguredSource; seen: SourceRow | undefined }) {
  const health = healthOf(seen);
  const Icon = health.icon;
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={`min-w-0 flex-1 basis-56 text-label-md ${source.enabled ? "text-primary" : "text-tertiary"}`}>{source.name}</span>
        <span className="text-body-sm text-tertiary tabular">
          {seen?.pages != null ? `${count(seen.pages)} ${seen.pages === 1 ? "page" : "pages"} last pass` : "no pass yet"}
        </span>
        <span className="hidden w-44 text-body-sm text-tertiary lg:block">
          {seen?.high_water_mark ? `Read up to ${when(seen.high_water_mark)}` : "No high-water mark"}
        </span>
        {source.enabled ? (
          <StatusChip tone={health.tone}>
            <Icon size={14} strokeWidth={1.75} />
            {health.label}
          </StatusChip>
        ) : (
          <StatusChip>Off</StatusChip>
        )}
      </div>
      {seen?.consecutive_failures ? (
        <p className="mt-1 text-body-sm text-error">{seen.consecutive_failures} consecutive failures</p>
      ) : null}
      {seen?.last_error ? <p className="mt-1 text-body-sm text-error">{seen.last_error}</p> : null}
    </li>
  );
}

export default function SourcesPage() {
  const { brief } = useDesk();
  const configured = brief?.configured_sources ?? [];
  const seen = new Map((brief?.sources ?? []).map((source) => [source.id, source]));
  const restricted = brief?.restricted_portals ?? [];
  const known = new Set(["cppp", "gepnic"]);
  const enabled = configured.filter((source) => source.enabled);
  const healthy = enabled.filter((source) => healthOf(seen.get(source.id)).label === "Healthy").length;
  const failing = enabled.filter((source) => healthOf(seen.get(source.id)).label === "Failing").length;
  const caption = configured.find((source) => source.type === "gepnic" && source.caption)?.caption;

  return (
    <>
      <PageHeader
        title="Sources"
        description="Vira only reads sources you have enabled. A portal that blocks her is marked as policy, and the rest of the pass still finishes."
      />
      <PageBody>
        {brief && configured.length === 0 ? (
          <EmptyState art={<EmptySources />} title="No sources in the config">
            Add a source to config/chanakya.yaml, then run a pass.
          </EmptyState>
        ) : null}

        {configured.length > 0 ? (
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <Metric label="Enabled" value={count(enabled.length)} hint={`${count(configured.length)} configured`} />
            <Metric label="Healthy" value={count(healthy)} hint="Answered on the last pass" />
            <Metric label="Failing" value={count(failing)} hint={failing ? "See the error on the row" : "None"} />
            <Metric label="Not fetched" value={count(restricted.length)} hint="Restricted by the site" />
          </div>
        ) : null}

        <div className="mt-4 space-y-4">
          {GROUPS.map((group) => {
            const members = configured.filter((source) => (group.type === "other" ? !known.has(source.type) : source.type === group.type));
            if (members.length === 0) return null;
            const Icon = group.icon;
            const note = group.type === "gepnic" ? caption : group.note;
            const unverified = members.filter((source) => source.verification_status !== "verified_working").length;
            return (
              <Panel key={group.type}>
                <header className="border-b border-muted px-4 py-3">
                  <h3 className="flex items-center gap-2 text-heading-sm text-primary">
                    <Icon size={16} strokeWidth={1.75} className="shrink-0 text-secondary" />
                    {group.title}
                    <span className="text-body-sm font-normal text-tertiary">{count(members.length)}</span>
                  </h3>
                  {note ? <p className="mt-1 max-w-[80ch] text-body-sm text-tertiary">{note}</p> : null}
                  {unverified > 0 ? (
                    <p className="mt-1 text-body-sm text-quaternary">
                      {unverified === members.length ? "Every parser here" : `${count(unverified)} of ${count(members.length)} parsers here`} still
                      {unverified === 1 ? " needs" : " need"} a live check with <span className="text-tertiary">chanakya sources test</span>.
                    </p>
                  ) : null}
                </header>
                <ul className="divide-y divide-muted">
                  {members.map((source) => (
                    <SourceLine key={source.id} source={source} seen={seen.get(source.id)} />
                  ))}
                </ul>
              </Panel>
            );
          })}

          {restricted.length > 0 ? (
            <Panel>
              <header className="border-b border-muted px-4 py-3">
                <h3 className="flex items-center gap-2 text-heading-sm text-primary">
                  <ShieldBan size={16} strokeWidth={1.75} className="shrink-0 text-secondary" />
                  Not fetched
                  <span className="text-body-sm font-normal text-tertiary">{count(restricted.length)}</span>
                </h3>
                <p className="mt-1 text-body-sm text-tertiary">These sites are restricted. Nothing here is a crash, and nothing here is bypassed.</p>
              </header>
              <ul className="divide-y divide-muted">
                {restricted.map((portal) => (
                  <li key={portal.id} className="px-4 py-3">
                    <div className="text-label-md text-primary">{portal.name}</div>
                    <p className="mt-0.5 max-w-[80ch] text-body-sm text-tertiary">{portal.reason}</p>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>
      </PageBody>
    </>
  );
}
