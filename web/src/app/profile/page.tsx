"use client";

import { Spot } from "@/components/vira/art";
import { BadgeCheck, ListChecks, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Button, PageBody, PageHeader, Panel } from "@/components/ui";
import { useDesk } from "@/context/desk";
import { api } from "@/lib/api";
import { profileApi, type CompanyProfile, type ProfileState } from "@/lib/api-profile";
import { count, when } from "@/lib/format";

type Answer = boolean | null;

/** The form holds text. A number field that is empty is not stated, never zero. */
type Form = {
  name: string;
  average: string;
  annual: string;
  years: string;
  projects: string;
  certifications: string[];
  draft: string;
  certIn: Answer;
  msme: Answer;
  dpiit: Answer;
};

const text = (value: number | null) => (value == null ? "" : String(value));

function toForm(profile: CompanyProfile): Form {
  return {
    name: profile.name,
    average: text(profile.average_turnover_3y_inr_crore),
    annual: text(profile.annual_turnover_inr_crore),
    years: text(profile.years_in_operation),
    projects: text(profile.past_government_projects),
    certifications: profile.certifications,
    draft: "",
    certIn: profile.cert_in_empanelled,
    msme: profile.msme_registered,
    dpiit: profile.dpiit_startup,
  };
}

/** Null for an empty field, a number for a good one, and a message for anything else. */
function parse(raw: string, whole = false): number | null | string {
  const cleaned = raw.trim().replace(/,/g, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  if (!Number.isFinite(value) || !/^\d*\.?\d+$|^\d+\.$/.test(cleaned)) return "Enter a number, or leave it empty.";
  if (whole && !Number.isInteger(value)) return "Enter a whole number, or leave it empty.";
  return value;
}

const number = (value: number | null | string) => (typeof value === "string" ? null : value);

function withDraft(form: Form): string[] {
  const draft = form.draft.trim();
  const held = form.certifications.map((item) => item.toUpperCase().replace(/\s+/g, ""));
  return draft && !held.includes(draft.toUpperCase().replace(/\s+/g, "")) ? [...form.certifications, draft] : form.certifications;
}

function toProfile(form: Form): Partial<CompanyProfile> & { name: string | null } {
  return {
    name: form.name.trim() || null,
    average_turnover_3y_inr_crore: number(parse(form.average)),
    annual_turnover_inr_crore: number(parse(form.annual)),
    years_in_operation: number(parse(form.years)),
    past_government_projects: number(parse(form.projects, true)),
    certifications: withDraft(form),
    cert_in_empanelled: form.certIn,
    msme_registered: form.msme,
    dpiit_startup: form.dpiit,
  } as Partial<CompanyProfile> & { name: string | null };
}

const input = "h-9 w-full rounded-lg border border-muted bg-page px-2.5 text-body-md text-primary placeholder:text-quaternary hover:border-default";

function Row({ label, hint, htmlFor, error, children }: { label: string; hint: string; htmlFor?: string; error?: string | null; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <label htmlFor={htmlFor} className="text-label-md text-primary">{label}</label>
        <span className="text-caption uppercase tracking-wide text-quaternary">Optional</span>
      </div>
      <div className="mt-1.5">{children}</div>
      {error ? <p className="mt-1 text-body-sm text-error">{error}</p> : null}
      <p className="mt-1 text-body-sm text-tertiary">{hint}</p>
    </div>
  );
}

function NumberRow({
  id, label, hint, unit, value, whole = false, onChange,
}: {
  id: string; label: string; hint: string; unit: string; value: string; whole?: boolean; onChange: (value: string) => void;
}) {
  const parsed = parse(value, whole);
  const error = typeof parsed === "string" ? parsed : null;
  return (
    <Row label={label} hint={hint} htmlFor={id} error={error}>
      <div className="flex items-center gap-2">
        <input
          id={id}
          value={value}
          inputMode={whole ? "numeric" : "decimal"}
          autoComplete="off"
          placeholder="Not stated"
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
          className={`${input} tabular ${error ? "border-error-stroke" : ""}`}
        />
        <span className="shrink-0 text-body-sm text-tertiary">{unit}</span>
      </div>
    </Row>
  );
}

const ANSWERS: { label: string; value: Answer }[] = [
  { label: "Not stated", value: null },
  { label: "Yes", value: true },
  { label: "No", value: false },
];

function AnswerRow({ label, hint, value, onChange }: { label: string; hint: string; value: Answer; onChange: (value: Answer) => void }) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex flex-wrap gap-2" role="group" aria-label={label}>
        {ANSWERS.map((answer) => {
          const chosen = value === answer.value;
          return (
            <button
              key={answer.label}
              type="button"
              aria-pressed={chosen}
              onClick={() => onChange(answer.value)}
              className={`h-9 rounded-lg border px-3 text-label-sm ${chosen ? "border-default bg-raised text-primary" : "border-muted text-secondary hover:border-default"}`}
            >
              {answer.label}
            </button>
          );
        })}
      </div>
    </Row>
  );
}

export default function ProfilePage() {
  const { job, busy, writerBusy, followJob } = useDesk();
  const [state, setState] = useState<ProfileState | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [bookJob, setBookJob] = useState<string | null>(null);
  const [bookError, setBookError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    profileApi.read()
      .then((next) => {
        if (cancelled) return;
        setState(next);
        setForm(toForm(next.profile));
      })
      .catch((err: Error) => {
        if (!cancelled) setLoadError(err instanceof TypeError ? "The desk API is not reachable. Start it with: chanakya serve" : err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const set = (patch: Partial<Form>) => {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));
    setJustSaved(false);
    setSaveError(null);
  };

  const invalid = form ? [parse(form.average), parse(form.annual), parse(form.years), parse(form.projects, true)].some((value) => typeof value === "string") : false;
  const dirty = !!form && !!state && JSON.stringify(toProfile(form)) !== JSON.stringify(toProfile(toForm(state.profile)));
  const profile = state?.profile;
  const stated = profile
    ? [
        profile.average_turnover_3y_inr_crore, profile.annual_turnover_inr_crore, profile.years_in_operation,
        profile.past_government_projects, profile.cert_in_empanelled, profile.msme_registered, profile.dpiit_startup,
      ].filter((value) => value != null).length + (profile.certifications.length > 0 ? 1 : 0)
    : 0;

  function addCertificate() {
    if (!form) return;
    set({ certifications: withDraft(form), draft: "" });
  }

  async function save() {
    if (!form || invalid) return;
    setSaving(true);
    setSaveError(null);
    try {
      const next = await profileApi.save(toProfile(form) as Partial<CompanyProfile>);
      setState(next);
      setForm(toForm(next.profile));
      setJustSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "The profile was not saved.");
    } finally {
      setSaving(false);
    }
  }

  async function reassess() {
    setBookError(null);
    try {
      const { job: next } = await api.analyze("all");
      setBookJob(next.id);
      followJob(next);
    } catch (err) {
      setBookError(err instanceof Error ? err.message : "The book could not be re-assessed.");
    }
  }

  const mine = job && job.id === bookJob ? job : null;

  return (
    <>
      <PageHeader
        title="Company profile"
        description="What Vira may take as known about Wayam when a tender sets a condition. She compares each clause with these figures, and with nothing else."
      />
      <PageBody>
        <div className="max-w-[60rem] space-y-4">
          <div className="flex items-start gap-3 rounded-xl border border-info-stroke bg-info-surface px-4 py-3">
            <Spot name="profile" size={64} className="hidden sm:block" />
            <BadgeCheck size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-info sm:hidden" />
            <div className="min-w-0 text-body-md text-info">
              <p className="text-label-md">Verified figures only.</p>
              <p className="mt-0.5">
                Nothing on this page is filled in for you. Every field is optional, and an empty field stays not stated: Vira then reports that
                clause as unknown and leaves it for a person. Enter what an audited statement, a certificate or a registration document shows.
              </p>
            </div>
          </div>

          {loadError ? <p className="rounded-lg border border-error-stroke bg-error-surface px-3 py-2 text-body-md text-error">{loadError}</p> : null}
          {!form && !loadError ? <p className="text-body-md text-tertiary">Opening the profile…</p> : null}

          {form && state ? (
            <>
              <Panel title="Turnover" bodyClassName="grid gap-x-6 gap-y-5 p-4 sm:grid-cols-2">
                <NumberRow
                  id="average"
                  label="Average turnover, last three years"
                  unit="INR crore"
                  value={form.average}
                  onChange={(value) => set({ average: value })}
                  hint="Compared with the turnover a tender asks for. Use the audited average of the last three financial years."
                />
                <NumberRow
                  id="annual"
                  label="Latest annual turnover"
                  unit="INR crore"
                  value={form.annual}
                  onChange={(value) => set({ annual: value })}
                  hint="Used only when the three-year average is not stated."
                />
              </Panel>

              <Panel title="Track record" bodyClassName="grid gap-x-6 gap-y-5 p-4 sm:grid-cols-2">
                <NumberRow
                  id="years"
                  label="Years in operation"
                  unit="years"
                  value={form.years}
                  onChange={(value) => set({ years: value })}
                  hint="Compared with clauses that ask for a minimum number of years. Count from the date of incorporation."
                />
                <NumberRow
                  id="projects"
                  label="Past government projects"
                  unit="projects"
                  whole
                  value={form.projects}
                  onChange={(value) => set({ projects: value })}
                  hint="With 0, a clause that asks for government experience is marked not met. With any other number, that clause is still left for a person to judge."
                />
              </Panel>

              <Panel title="Certificates" bodyClassName="grid gap-x-6 gap-y-5 p-4 sm:grid-cols-2">
                <Row
                  label="Certificates held"
                  htmlFor="certificate"
                  hint="Checked against ISO, CMMI and STQC clauses. Add only certificates that are valid today. Once one is listed, a certificate a tender asks for that is not on the list counts as not held."
                >
                  {form.certifications.length > 0 ? (
                    <ul className="mb-2 flex flex-wrap gap-2">
                      {form.certifications.map((item) => (
                        <li key={item} className="inline-flex h-7 max-w-full items-center gap-1 rounded-md bg-raised-2 pr-1 pl-2 text-label-sm text-secondary">
                          <span className="truncate">{item}</span>
                          <button
                            type="button"
                            aria-label={`Remove ${item}`}
                            onClick={() => set({ certifications: form.certifications.filter((held) => held !== item) })}
                            className="rounded p-0.5 text-tertiary hover:bg-action-tertiary-hover hover:text-primary"
                          >
                            <X size={14} strokeWidth={1.75} />
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mb-2 text-body-sm text-quaternary">Not stated</p>
                  )}
                  <div className="flex gap-2">
                    <input
                      id="certificate"
                      value={form.draft}
                      maxLength={80}
                      autoComplete="off"
                      placeholder="For example ISO 27001"
                      onChange={(event) => set({ draft: event.target.value })}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          addCertificate();
                        }
                      }}
                      className={input}
                    />
                    <Button variant="secondary" onClick={addCertificate} disabled={!form.draft.trim()}>Add</Button>
                  </div>
                </Row>
                <AnswerRow
                  label="CERT-In empanelled"
                  value={form.certIn}
                  onChange={(value) => set({ certIn: value })}
                  hint="Checked against clauses that ask for a CERT-In empanelled auditor."
                />
              </Panel>

              <Panel title="Registrations" bodyClassName="grid gap-x-6 gap-y-5 p-4 sm:grid-cols-2">
                <AnswerRow
                  label="MSME (Udyam) registered"
                  value={form.msme}
                  onChange={(value) => set({ msme: value })}
                  hint="Kept on record. Eligibility does not read it yet: an exemption clause for small enterprises is shown for a person to judge."
                />
                <AnswerRow
                  label="DPIIT recognised startup"
                  value={form.dpiit}
                  onChange={(value) => set({ dpiit: value })}
                  hint="Kept on record. Eligibility does not read it yet: an exemption clause for startups is shown for a person to judge."
                />
              </Panel>

              <Panel title="Company name" bodyClassName="p-4">
                <Row label="Name" htmlFor="name" hint={`Shown on the desk and in reports. Leave it empty to keep the name in ${state.config_file ?? "the config"}.`}>
                  <input
                    id="name"
                    value={form.name}
                    maxLength={120}
                    autoComplete="off"
                    onChange={(event) => set({ name: event.target.value })}
                    className={`${input} sm:max-w-sm`}
                  />
                </Row>
              </Panel>

              <Panel bodyClassName="space-y-3 p-4">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <Button onClick={save} disabled={saving || invalid || !dirty}>{saving ? "Saving" : "Save profile"}</Button>
                  <p className="min-w-0 flex-1 basis-56 text-body-sm text-tertiary" aria-live="polite">
                    {saveError ? (
                      <span className="text-error">{saveError}</span>
                    ) : invalid ? (
                      "One field is not a number. Correct it, or leave it empty."
                    ) : dirty ? (
                      "You have changes that are not saved."
                    ) : justSaved ? (
                      <span className="text-success">Saved. {count(stated)} of 8 stated.</span>
                    ) : state.saved ? (
                      `${count(stated)} of 8 stated. Last saved ${when(state.saved_at)}.`
                    ) : (
                      `${count(stated)} of 8 stated. Nothing has been saved from this page yet.`
                    )}
                  </p>
                </div>
                <p className="break-words text-body-sm text-tertiary">
                  Saved values are kept in {state.stored_in} and laid over the company profile in {state.config_file ?? "the config"} each time the
                  config is loaded. The config file itself is not rewritten.
                </p>
              </Panel>

              {state.saved ? (
                <Panel title="Apply it to the book" bodyClassName="space-y-3 p-4">
                  <p className="max-w-[72ch] text-body-md text-secondary">
                    A saved profile is used the next time a notice is assessed. Notices already in the book keep the eligibility they were given
                    until then. Re-assessing judges every stored notice again with these figures. No portal is opened, and model calls stay inside
                    the budget in the config.
                  </p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <Button variant="secondary" onClick={reassess} disabled={busy || writerBusy || dirty}>
                      <ListChecks size={14} strokeWidth={1.75} />
                      Re-assess the book
                    </Button>
                    <p className="min-w-0 flex-1 basis-56 text-body-sm text-tertiary" aria-live="polite">
                      {bookError ? (
                        <span className="text-error">{bookError}</span>
                      ) : mine?.status === "running" ? (
                        <>
                          Re-assessing. <Link href="/" className="text-secondary underline hover:text-primary">Follow it on the desk</Link>.
                        </>
                      ) : mine?.status === "failed" ? (
                        <span className="text-error">{mine.error || "The re-assessment did not finish."}</span>
                      ) : mine ? (
                        <span className="text-success">
                          The book was re-assessed{mine.stats ? `: ${count(mine.stats.assessed)} notices` : ""}.
                        </span>
                      ) : writerBusy ? (
                        "A watch or another command holds the database. Re-assess when it has finished."
                      ) : busy ? (
                        "A pass is running. Re-assess when it has finished."
                      ) : dirty ? (
                        "Save your changes first."
                      ) : null}
                    </p>
                  </div>
                </Panel>
              ) : null}
            </>
          ) : null}
        </div>
      </PageBody>
    </>
  );
}
