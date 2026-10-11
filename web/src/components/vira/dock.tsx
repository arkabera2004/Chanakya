"use client";

import { Send, X } from "lucide-react";
import { useState } from "react";

import { WayamOrb, type Mood } from "@/components/vira/orb";
import { useDesk } from "@/context/desk";
import { api, type ViraReply } from "@/lib/api";

export function ViraDock({ opportunityId }: { opportunityId?: string }) {
  const { brief } = useDesk();
  const [open, setOpen] = useState(false);
  const [mood, setMood] = useState<Mood>(brief?.vira.mood ?? "curious");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [turns, setTurns] = useState<{ role: "assistant" | "user"; content: string }[]>([]);

  const greeting = brief?.vira.greeting;
  const suggestions = brief?.vira.suggestions ?? [];

  async function ask(message: string) {
    const text = message.trim();
    if (!text || pending) return;
    setOpen(true);
    setDraft("");
    setTurns((prev) => [...prev, { role: "user", content: text }]);
    setPending(true);
    setMood("focused");
    try {
      const reply: ViraReply = await api.vira(text, opportunityId);
      setMood(reply.mood);
      setTurns((prev) => [...prev, { role: "assistant", content: reply.reply }]);
    } catch (err) {
      setMood("neutral");
      setTurns((prev) => [
        ...prev,
        { role: "assistant", content: err instanceof Error ? err.message : "I could not reach the desk." },
      ]);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-40 flex flex-col items-end gap-3">
      {open ? (
        <section className="pointer-events-auto flex h-[min(32rem,70vh)] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-muted bg-container shadow-[0_16px_48px_rgba(0,0,0,0.18)]">
          <header className="flex items-center gap-3 border-b border-muted px-3 py-2.5">
            <WayamOrb id="dock" size={36} mood={pending ? "focused" : mood} talking={pending} />
            <div className="min-w-0 flex-1">
              <div className="text-label-md text-primary">Vira</div>
              <div className="text-body-sm text-tertiary">Discovery agent{brief?.demo ? " · demo data" : ""}</div>
            </div>
            <button type="button" aria-label="Close Vira" onClick={() => setOpen(false)} className="rounded-lg p-1.5 text-tertiary hover:bg-action-tertiary-hover">
              <X size={16} />
            </button>
          </header>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
            {greeting ? <p className="text-body-md text-secondary">{greeting}</p> : null}
            {turns.map((turn, index) => (
              <p
                key={index}
                className={`whitespace-pre-wrap text-body-md ${turn.role === "user" ? "ml-8 rounded-xl bg-raised px-3 py-2 text-primary" : "text-secondary"}`}
              >
                {turn.content}
              </p>
            ))}
            {pending ? <p className="text-body-sm text-tertiary">Reading the record…</p> : null}
          </div>
          <div className="flex flex-wrap gap-1.5 border-t border-muted px-3 py-2">
            {suggestions.slice(0, 3).map((item) => (
              <button key={item} type="button" onClick={() => ask(item)} className="rounded-full border border-muted px-2.5 py-1 text-body-sm text-secondary hover:bg-raised">
                {item}
              </button>
            ))}
          </div>
          <form
            className="flex items-center gap-2 border-t border-muted p-2"
            onSubmit={(event) => {
              event.preventDefault();
              ask(draft);
            }}
          >
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask about a lead, a rejection, or the last pass"
              className="h-9 min-w-0 flex-1 rounded-lg border border-muted bg-page px-3 text-body-md text-primary outline-none placeholder:text-quaternary"
            />
            <button type="submit" aria-label="Send" className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-action-primary text-action-primary-content">
              <Send size={14} />
            </button>
          </form>
        </section>
      ) : null}
      <button
        type="button"
        className="pointer-events-auto rounded-full"
        aria-label={open ? "Vira is open" : "Ask Vira"}
        onClick={() => setOpen((value) => !value)}
      >
        <WayamOrb id="launcher" size={56} mood={mood} talking={pending} />
      </button>
    </div>
  );
}
