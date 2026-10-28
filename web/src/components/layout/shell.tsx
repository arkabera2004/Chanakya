"use client";

import { Activity, BadgeCheck, Boxes, Coins, FlaskConical, Menu, Moon, Plug, Radar, Radio, Server, SunMedium, Telescope, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { StatusChip } from "@/components/ui";
import { ViraDock } from "@/components/vira/dock";
import { useDesk } from "@/context/desk";
import { useTheme } from "@/context/theme-context";
import { count, triggerLabel, when } from "@/lib/format";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/", label: "Desk", icon: Radar },
  { href: "/leads", label: "Leads", icon: Telescope },
  { href: "/products", label: "Products", icon: Boxes },
  { href: "/runs", label: "Passes", icon: Activity },
  { href: "/sources", label: "Sources", icon: Server },
  { href: "/providers", label: "Providers", icon: Plug },
  { href: "/costs", label: "Costs", icon: Coins },
  { href: "/profile", label: "Profile", icon: BadgeCheck },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { isDark, toggleTheme } = useTheme();
  const { brief, busy, writerBusy, job, watchStatus } = useDesk();
  const [open, setOpen] = useState(false);
  const watching = writerBusy && watchStatus?.mode === "watch";
  const activity = watching
    ? "Watching"
    : writerBusy
      ? watchStatus?.mode === "analyze" ? "Scoring" : "Scanning"
      : busy
        ? job?.kind === "analyze" ? "Scoring" : "Pass in progress"
        : null;
  const last = brief?.last_run ?? null;
  const sources = brief?.configured_sources.filter((source) => source.enabled).length ?? 0;

  // The drawer closes when the page changes, and on Escape.
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-page">
      {open ? (
        <button type="button" aria-label="Close navigation" className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />
      ) : null}
      <aside
        className={[
          "fixed inset-y-0 left-0 z-50 flex w-[232px] flex-col border-r border-muted bg-container transition-transform duration-200 lg:static lg:transition-none",
          open ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        ].join(" ")}
      >
        <Link href="/" className="flex h-14 items-center gap-2.5 border-b border-muted px-4">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/mark-128.png" alt="" width={28} height={28} className="h-7 w-7 shrink-0 rounded-lg" />
          <div>
            <div className="font-display text-display-base text-primary">CHANAKYA</div>
            <div className="text-caption text-quaternary">Wayam discovery</div>
          </div>
        </Link>
        <nav className="flex flex-1 flex-col gap-1 px-3 py-3" aria-label="Main">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            const badge = item.href === "/leads" && brief && brief.counts.p1 > 0 ? brief.counts.p1 : null;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={[
                  "relative flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-label-md",
                  active
                    ? "bg-action-secondary text-primary before:absolute before:top-2 before:bottom-2 before:-left-3 before:w-0.5 before:rounded-full before:bg-vira"
                    : "text-secondary hover:bg-action-tertiary-hover hover:text-primary",
                ].join(" ")}
              >
                <Icon size={16} strokeWidth={1.75} />
                <span className="flex-1">{item.label}</span>
                {badge ? (
                  <span className="rounded-md border border-error-stroke bg-error-surface px-1.5 text-label-sm text-error tabular" title={`${badge} in P1`}>
                    {count(badge)}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center gap-2 border-t border-muted px-4 py-3 text-body-sm text-tertiary">
          <span className={`h-2 w-2 shrink-0 rounded-full ${activity ? "animate-pulse bg-vira" : "bg-raised-2"}`} />
          {watching ? "A watch holds the database." : activity ? "Vira is mid-pass." : "Vira is standing by."}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-muted bg-container px-4 sm:px-6">
          <button type="button" aria-label="Open menu" className="-ml-2 rounded-lg p-2 text-secondary hover:bg-action-tertiary-hover lg:hidden" onClick={() => setOpen(true)}>
            <Menu size={16} strokeWidth={1.75} />
          </button>
          <p className="min-w-0 flex-1 truncate text-body-sm text-tertiary">
            {brief ? (
              <>
                <span className="text-secondary">{count(brief.counts.notices)} notices</span>
                <span className="hidden sm:inline">
                  {" · "}
                  {count(sources)} {sources === 1 ? "source" : "sources"}
                  {last ? ` · last ${triggerLabel(last.trigger).toLowerCase()} ${when(last.finished_at ?? last.started_at)}` : " · no pass yet"}
                </span>
              </>
            ) : (
              "Connecting to the desk…"
            )}
          </p>
          {brief?.demo ? (
            <StatusChip tone="warning">
              <FlaskConical size={14} strokeWidth={1.75} />
              Demo data
            </StatusChip>
          ) : null}
          {activity ? (
            <StatusChip tone="info">
              <Radio size={14} strokeWidth={1.75} className="animate-pulse" />
              {activity}
            </StatusChip>
          ) : null}
          <button type="button" aria-label={isDark ? "Use the light theme" : "Use the dark theme"} onClick={toggleTheme} className="-mr-2 rounded-lg p-2 text-secondary hover:bg-action-tertiary-hover">
            {isDark ? <SunMedium size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
          </button>
        </header>
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
      <ViraDock />
    </div>
  );
}
