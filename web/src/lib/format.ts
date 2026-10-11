import type { RunRow } from "@/lib/api";

/** True when the model budget stopped calls on this pass, so some notices still wait for it. */
export function budgetHit(run: RunRow | null | undefined): boolean {
  if (!run) return false;
  return (
    run.llm_skipped_budget > 0 ||
    run.budget_blocked_reasons.length > 0 ||
    run.coverage_notes.some((note) => /budget|max_calls/i.test(note)) ||
    /budget|max_calls/i.test(run.error || "")
  );
}

export function inr(value: number | null | undefined): string {
  if (value == null) return "Not stated";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "Not stated";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Not stated";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function closingSoon(iso: string | null | undefined, days = 7): boolean {
  if (!iso) return false;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return false;
  const delta = date.getTime() - Date.now();
  return delta >= 0 && delta <= days * 86_400_000;
}

export function money(usd: number | null | undefined): string {
  return `$${Number(usd || 0).toFixed(4)}`;
}

/**
 * Model spend is fractions of a cent, so the places grow as the amount shrinks:
 * $2.00, $0.0039, $0.000083. A real cost never reads as zero.
 */
export function usd(value: number | null | undefined): string {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount) || amount === 0) return "$0";
  const size = Math.abs(amount);
  const cents = Math.round(amount * 100) / 100;
  if (size >= 1 || (size >= 0.01 && Math.abs(cents - amount) < 1e-9)) return `$${amount.toFixed(2)}`;
  if (size >= 0.001) return `$${amount.toFixed(4)}`;
  // Below a tenth of a cent, keep two significant figures.
  const places = Math.min(12, Math.ceil(-Math.log10(size)) + 1);
  return `$${amount.toFixed(places)}`;
}

export const SCORE_LABELS: Record<string, string> = {
  capability: "Capability",
  eligibility: "Eligibility",
  technical: "Technical fit",
  deadline: "Deadline",
  commercial: "Commercial",
  strategic: "Strategic",
};

/** "2 Nov" this year, "2 Nov 2027" otherwise. Always IST, like the portals. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "Not stated";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Not stated";
  const year = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", year: "numeric" }).format(date);
  const thisYear = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", year: "numeric" }).format(new Date());
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
    ...(year === thisYear ? {} : { year: "numeric" }),
  }).format(date);
}

/** "today", "in 3 days", "closed". Null when there is no date to count from. */
export function daysLeft(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const delta = date.getTime() - Date.now();
  if (delta < 0) return "closed";
  const days = Math.floor(delta / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

export function count(value: number | null | undefined): string {
  return value == null ? "…" : new Intl.NumberFormat("en-IN").format(value);
}

const TRIGGERS: Record<string, string> = {
  watch: "Watch",
  analyze: "Scoring",
  desk: "Desk pass",
  manual: "Manual pass",
  scan: "Scan",
  schedule: "Scheduled pass",
  notice: "One notice",
};

export function triggerLabel(trigger: string | null | undefined): string {
  return TRIGGERS[trigger || ""] ?? (trigger || "Pass");
}

/** Budget guard reasons arrive as config keys. Say them as sentences. */
export function budgetReason(reason: string): string {
  const cap = reason.match(/\(([^)]+)\)/)?.[1];
  if (reason.startsWith("max_calls_per_run")) return `The cap of ${cap ?? "the configured"} model calls per pass was reached.`;
  if (reason.startsWith("max_deep_analyses_per_run")) return `The cap of ${cap ?? "the configured"} deep analyses per pass was reached.`;
  if (reason.startsWith("max_cost_per_run")) return `The spend cap per pass (${cap ?? "configured"}) was reached.`;
  if (reason.startsWith("max_cost_per_day")) return `The spend cap per day (${cap ?? "configured"}) was reached.`;
  return reason;
}
