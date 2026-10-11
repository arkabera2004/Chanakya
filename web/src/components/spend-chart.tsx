"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { count, usd } from "@/lib/format";

export type SpendDay = { date: string; cost_usd: number; calls: number; cache_hits: number; blocked: number };

const HEIGHT = 208;
const TOP = 12;
const BOTTOM = 24;
const GAP = 2;

/** "10 Oct" from an IST calendar day. The day is already resolved, so it is read as UTC. */
function dayLabel(date: string, long = false): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "UTC",
    day: "numeric",
    month: "short",
    ...(long ? { weekday: "short" } : {}),
  }).format(parsed);
}

/** Round an axis step up to 1, 2, 2.5 or 5 times a power of ten. */
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return nice * power;
}

/** A bar that sits square on the baseline and is rounded at the data end. */
function barPath(x: number, y: number, width: number, height: number): string {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
}

/**
 * Model spend per IST day, one series. Hover, touch or the arrow keys read a day;
 * the table underneath carries the same figures for a screen reader.
 */
export function SpendChart({ days }: { days: SpendDay[] }) {
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const measure = () => setWidth(Math.max(240, Math.floor(node.clientWidth)));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const peak = Math.max(0, ...days.map((day) => day.cost_usd));
  const step = peak > 0 ? niceStep(peak / 2) : 1;
  const top = peak > 0 ? Math.max(step * 2, Math.ceil(peak / step - 1e-9) * step) : 1;
  const ticks: number[] = [];
  for (let value = 0; value <= top + step / 2; value += step) ticks.push(value);

  const left = Math.max(40, ...ticks.map((tick) => usd(tick).length * 7 + 10));
  const plotWidth = Math.max(1, width - left - 4);
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const band = plotWidth / Math.max(1, days.length);
  const barWidth = Math.max(2, band - GAP);
  const y = (value: number) => TOP + plotHeight - (value / top) * plotHeight;
  const every = band >= 22 ? 5 : band >= 11 ? 10 : 15;

  const total = days.reduce((sum, day) => sum + day.cost_usd, 0);
  const peakDay = days.find((day) => day.cost_usd === peak && peak > 0);
  const summary = `Bar chart of model spend per day for the last ${days.length} days. Total ${usd(total)}${
    peakDay ? `, highest ${usd(peak)} on ${dayLabel(peakDay.date)}` : ""
  }. The table below has every day.`;

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (days.length === 0) return;
    const last = days.length - 1;
    if (event.key === "ArrowRight") setActive((at) => (at == null ? last : Math.min(last, at + 1)));
    else if (event.key === "ArrowLeft") setActive((at) => (at == null ? last : Math.max(0, at - 1)));
    else if (event.key === "Home") setActive(0);
    else if (event.key === "End") setActive(last);
    else if (event.key === "Escape") setActive(null);
    else return;
    event.preventDefault();
  }

  const shown = active != null ? days[active] : null;
  const tipCentre = active != null ? left + band * active + band / 2 : 0;
  const tipLeft = Math.min(Math.max(tipCentre, 84), Math.max(84, width - 84));

  return (
    <div>
      <div
        ref={frame}
        className="relative w-full rounded-lg"
        tabIndex={0}
        role="group"
        aria-label="Spend per day. Use the left and right arrow keys to read each day."
        onKeyDown={onKey}
        onBlur={() => setActive(null)}
        onPointerLeave={() => setActive(null)}
      >
        <svg width={width} height={HEIGHT} viewBox={`0 0 ${width} ${HEIGHT}`} role="img" aria-label={summary} className="block max-w-full">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={left} x2={width} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "stroke-default" : "stroke-muted"} strokeWidth={1} />
              <text x={left - 8} y={y(tick)} textAnchor="end" dominantBaseline="middle" className="fill-tertiary text-body-sm tabular">
                {usd(tick)}
              </text>
            </g>
          ))}
          {days.map((day, index) => {
            const x = left + band * index + (band - barWidth) / 2;
            // A real cost always gets a visible bar, however small it is against the peak.
            const height = day.cost_usd > 0 ? Math.max(2, (day.cost_usd / top) * plotHeight) : 0;
            const labelled = index === days.length - 1 || (days.length - 1 - index) % every === 0;
            return (
              <g key={day.date}>
                {height > 0 ? (
                  <path
                    d={barPath(x, TOP + plotHeight - height, barWidth, height)}
                    className="fill-vira"
                    opacity={active == null || active === index ? 1 : 0.45}
                  />
                ) : null}
                {labelled ? (
                  <text
                    x={left + band * index + band / 2}
                    y={HEIGHT - 6}
                    textAnchor={index === days.length - 1 ? "end" : "middle"}
                    dx={index === days.length - 1 ? band / 2 : 0}
                    className="fill-tertiary text-body-sm tabular"
                  >
                    {dayLabel(day.date)}
                  </text>
                ) : null}
                {/* The whole column is the target, so an empty day and a 2px bar are as easy to read as a tall one. */}
                <rect
                  x={left + band * index}
                  y={TOP}
                  width={band}
                  height={plotHeight}
                  fill="transparent"
                  onPointerEnter={() => setActive(index)}
                  onPointerDown={() => setActive(index)}
                />
              </g>
            );
          })}
        </svg>
        {shown ? (
          <div
            className="pointer-events-none absolute top-0 z-10 w-40 -translate-x-1/2 rounded-lg border border-default bg-container px-2.5 py-2 text-body-sm"
            style={{ left: tipLeft }}
            aria-live="polite"
          >
            <div className="text-tertiary">{dayLabel(shown.date, true)}</div>
            <div className="mt-0.5 text-label-md text-primary tabular">{usd(shown.cost_usd)}</div>
            <div className="mt-0.5 text-tertiary tabular">
              {count(shown.calls)} {shown.calls === 1 ? "call" : "calls"}
              {shown.cache_hits > 0 ? `, ${count(shown.cache_hits)} from cache` : ""}
              {shown.blocked > 0 ? `, ${count(shown.blocked)} refused` : ""}
            </div>
          </div>
        ) : null}
      </div>

      <details className="mt-2 text-body-sm">
        <summary className="inline-flex cursor-pointer rounded-md px-1 text-secondary hover:text-primary">Show the days as a table</summary>
        <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-muted">
          <table className="w-full text-left tabular">
            <caption className="sr-only">Model spend per day, newest first</caption>
            <thead className="text-caption uppercase tracking-wide text-quaternary">
              <tr className="border-b border-muted">
                <th scope="col" className="px-3 py-2 font-medium">Day (IST)</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Spend</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Calls</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Cache</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Refused</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-muted text-secondary">
              {[...days].reverse().map((day) => (
                <tr key={day.date}>
                  <th scope="row" className="px-3 py-1.5 font-normal whitespace-nowrap">{dayLabel(day.date, true)}</th>
                  <td className="px-3 py-1.5 text-right text-primary">{usd(day.cost_usd)}</td>
                  <td className="px-3 py-1.5 text-right">{count(day.calls)}</td>
                  <td className="px-3 py-1.5 text-right">{count(day.cache_hits)}</td>
                  <td className="px-3 py-1.5 text-right">{count(day.blocked)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
