"use client";

import { Check, ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

export type SelectOption = {
  value: string;
  label: string;
  /** A quieter second piece of text on the right, for a count or a code. */
  hint?: string;
};

type Placement = { left: number; top?: number; bottom?: number; minWidth: number; maxHeight: number };

/**
 * The desk's dropdown. A button that opens a list of options, drawn with the desk's own
 * surfaces instead of the operating system's menu, and usable from the keyboard:
 * arrows, Home, End, Enter or Space to choose, Escape to close, and typing to jump.
 *
 * The list is positioned against the viewport, so a panel with clipped overflow cannot cut it off.
 */
export function Select({
  value,
  onChange,
  options,
  ariaLabel,
  className = "",
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [placement, setPlacement] = useState<Placement | null>(null);

  const selected = options.findIndex((option) => option.value === value);
  const current = selected >= 0 ? options[selected] : undefined;

  const place = useCallback(() => {
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return;
    const gap = 4;
    const below = window.innerHeight - rect.bottom - gap - 8;
    const above = rect.top - gap - 8;
    const up = below < 180 && above > below;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8));
    setPlacement({
      left,
      minWidth: rect.width,
      maxHeight: Math.max(120, Math.min(320, up ? above : below)),
      ...(up ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap }),
    });
  }, []);

  const show = useCallback(
    (index?: number) => {
      if (disabled) return;
      setActive(index ?? Math.max(0, selected));
      place();
      setOpen(true);
    },
    [disabled, place, selected],
  );

  const close = useCallback((focus = true) => {
    setOpen(false);
    if (focus) button.current?.focus();
  }, []);

  const choose = useCallback(
    (index: number) => {
      const option = options[index];
      if (!option) return;
      if (option.value !== value) onChange(option.value);
      close();
    },
    [close, onChange, options, value],
  );

  // Close on a press outside, and keep the list attached while the page moves under it.
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!button.current?.contains(target) && !list.current?.contains(target)) close(false);
    };
    const moved = (event: Event) => {
      if (list.current?.contains(event.target as Node)) return;
      place();
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", moved, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", moved, true);
    };
  }, [open, close, place]);

  // The list can be wider than its button. Pull it back inside the right edge of the window.
  useLayoutEffect(() => {
    if (!open || !placement || !list.current) return;
    const overflow = list.current.getBoundingClientRect().right - (window.innerWidth - 8);
    if (overflow > 0.5) setPlacement({ ...placement, left: Math.max(8, placement.left - overflow) });
  }, [open, placement]);

  useLayoutEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  function jump(key: string) {
    const now = Date.now();
    const text = (now - typed.current.at > 600 ? "" : typed.current.text) + key.toLowerCase();
    typed.current = { text, at: now };
    const from = open ? active : Math.max(0, selected);
    const order = [...options.keys()].map((offset) => (from + (text.length === 1 ? 1 : 0) + offset) % options.length);
    const hit = order.find((index) => options[index].label.toLowerCase().startsWith(text));
    if (hit === undefined) return;
    if (open) setActive(hit);
    else if (options[hit].value !== value) onChange(options[hit].value);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled || options.length === 0) return;
    const last = options.length - 1;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (open) setActive((index) => Math.min(last, index + 1));
        else show();
        break;
      case "ArrowUp":
        event.preventDefault();
        if (open) setActive((index) => Math.max(0, index - 1));
        else show();
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setActive(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setActive(last);
        }
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (open) choose(active);
        else show();
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          event.stopPropagation();
          close();
        }
        break;
      case "Tab":
        if (open) close(false);
        break;
      default:
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) jump(event.key);
    }
  }

  return (
    <>
      <button
        ref={button}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-activedescendant={open ? `${id}-option-${active}` : undefined}
        disabled={disabled}
        onClick={() => (open ? close() : show())}
        onKeyDown={onKeyDown}
        className={[
          "inline-flex h-9 min-w-0 items-center justify-between gap-2 rounded-lg border bg-container pr-2 pl-2.5 text-left text-body-md",
          "disabled:cursor-not-allowed disabled:opacity-40",
          open ? "border-active" : "border-muted hover:border-default",
          selected > 0 || (selected === 0 && options[0]?.value !== "") ? "text-primary" : "text-secondary",
          className,
        ].join(" ")}
      >
        <span className="truncate">{current?.label ?? value ?? ""}</span>
        <ChevronDown size={14} strokeWidth={1.75} className={`shrink-0 text-tertiary transition-transform duration-150 ${open ? "rotate-180" : ""}`} />
      </button>
      {open && placement ? (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          aria-label={ariaLabel}
          className="settle fixed z-[60] overflow-y-auto rounded-lg border border-default bg-container p-1 shadow-[0_12px_32px_rgba(0,0,0,0.28)]"
          style={{ ...placement, maxWidth: "min(30rem, calc(100vw - 16px))" }}
        >
          {options.map((option, index) => {
            const isSelected = index === selected;
            return (
              <li
                key={option.value}
                id={`${id}-option-${index}`}
                data-index={index}
                role="option"
                aria-selected={isSelected}
                onPointerEnter={() => setActive(index)}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
                className={[
                  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-body-md",
                  index === active ? "bg-raised-2 text-primary" : "text-secondary",
                ].join(" ")}
              >
                <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-vira">
                  {isSelected ? <Check size={14} strokeWidth={2} /> : null}
                </span>
                <span className={`min-w-0 flex-1 truncate ${isSelected ? "text-primary" : ""}`}>{option.label}</span>
                {option.hint ? <span className="shrink-0 text-body-sm text-tertiary tabular">{option.hint}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </>
  );
}
