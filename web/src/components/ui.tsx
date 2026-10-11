import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const styles = {
    primary: "bg-action-primary text-action-primary-content hover:bg-action-primary-hover",
    secondary: "border border-muted bg-action-secondary text-action-secondary-content hover:bg-action-secondary-hover",
    ghost: "text-secondary hover:bg-action-tertiary-hover",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-label-sm active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40 ${styles} ${className}`}
    />
  );
}

export function Panel({
  title,
  action,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden rounded-xl border border-muted bg-container ${className}`}>
      {title ? (
        <header className="flex items-center justify-between gap-3 border-b border-muted px-4 py-3">
          <h3 className="text-heading-sm text-primary">{title}</h3>
          {action}
        </header>
      ) : null}
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="shrink-0 border-b border-muted px-4 py-4 sm:px-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h2 className="font-display text-display-lg text-primary sm:text-display-xl">{title}</h2>
          {description ? <p className="mt-1 max-w-[72ch] text-body-md text-tertiary">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}

/** Bottom padding keeps the last row clear of Vira's launcher. */
export function PageBody({ children }: { children: ReactNode }) {
  return <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-24 sm:px-6 sm:pt-5">{children}</div>;
}

const PRIORITY: Record<string, string> = {
  P1: "border-error-stroke bg-error-surface text-error",
  P2: "border-warning-stroke bg-warning-surface text-warning",
  P3: "border-info-stroke bg-info-surface text-info",
  P4: "border-neutral-stroke bg-neutral-surface text-neutral",
};

export function Priority({ value }: { value: string | null }) {
  const label = value || "New";
  return (
    <span className={`inline-flex h-6 min-w-9 shrink-0 items-center justify-center rounded-md border px-1.5 font-display text-display-base ${PRIORITY[label] ?? PRIORITY.P4}`}>
      {label}
    </span>
  );
}

export type Tone = "success" | "info" | "warning" | "error" | "neutral";

const TONES: Record<Tone, string> = {
  success: "border-success-stroke bg-success-surface text-success",
  info: "border-info-stroke bg-info-surface text-info",
  warning: "border-warning-stroke bg-warning-surface text-warning",
  error: "border-error-stroke bg-error-surface text-error",
  neutral: "border-neutral-stroke bg-neutral-surface text-neutral",
};

/** A status word with its colour. The word carries the meaning; the colour repeats it. */
export function StatusChip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2 text-label-sm ${TONES[tone]}`}>
      {children}
    </span>
  );
}

/** A quiet label: a capability, a platform, a trigger. */
export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-5 shrink-0 items-center rounded-md bg-raised-2 px-1.5 text-label-sm text-secondary">
      {children}
    </span>
  );
}

export function Metric({
  label,
  value,
  hint,
  href,
  accent = false,
}: {
  label: string;
  value: string | number;
  hint?: string;
  href?: string;
  accent?: boolean;
}) {
  const body = (
    <>
      <div className="flex items-center gap-2 text-caption uppercase tracking-wide text-quaternary">
        {accent ? <span className="h-1.5 w-1.5 rounded-full bg-vira" /> : null}
        {label}
      </div>
      <div className="mt-1.5 font-display text-display-2xl text-primary tabular">{value}</div>
      {hint ? <div className="mt-1 text-body-sm text-tertiary">{hint}</div> : null}
    </>
  );
  const frame = "block rounded-xl border border-muted bg-container px-4 py-3.5";
  return href ? (
    <Link href={href} className={`${frame} hover:border-default hover:bg-raised`}>
      {body}
    </Link>
  ) : (
    <div className={frame}>{body}</div>
  );
}

/** Score out of 100, with a bar so a column of them can be compared at a glance. */
export function Score({ value }: { value: number | null }) {
  if (value == null) return <span className="text-body-sm text-quaternary">Not scored</span>;
  const width = Math.max(0, Math.min(100, value));
  return (
    <span className="inline-flex flex-col items-end gap-1">
      <span className="text-label-md text-primary tabular">{Math.round(value * 10) / 10}</span>
      <span className="block h-1 w-12 overflow-hidden rounded-full bg-raised-2">
        <span className="block h-full rounded-full bg-vira" style={{ width: `${width}%` }} />
      </span>
    </span>
  );
}

/** Nothing to show yet. Says why, and what to do next. */
export function EmptyState({
  art,
  title,
  children,
  action,
}: {
  art?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      {art ? <div className="mb-4 text-quaternary">{art}</div> : null}
      <h3 className="text-heading-sm text-primary">{title}</h3>
      {children ? <p className="mt-1 max-w-[46ch] text-body-md text-tertiary">{children}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
