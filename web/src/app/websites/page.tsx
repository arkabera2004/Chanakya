"use client";

import { Globe, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, EmptyState, PageBody, PageHeader, Panel, StatusChip, type Tone } from "@/components/ui";
import { websitesApi, type CreateWebsiteInput, type Website } from "@/lib/api-websites";
import { when } from "@/lib/format";

const CATEGORIES = [
  { value: "government_procurement", label: "Government Procurement" },
  { value: "government_department", label: "Government Department" },
  { value: "nhai", label: "NHAI" },
  { value: "gepnic", label: "GePNIC" },
  { value: "state_eprocurement", label: "State E-Procurement" },
  { value: "psu", label: "PSU" },
  { value: "company_website", label: "Company Website" },
  { value: "other", label: "Other" },
];

const input = "h-9 w-full rounded-lg border border-muted bg-page px-2.5 text-body-md text-primary placeholder:text-quaternary hover:border-default";

function statusTone(status: string | null): Tone {
  if (status === "succeeded") return "success";
  if (status === "partial") return "warning";
  if (status === "failed") return "error";
  if (status === "running") return "info";
  return "neutral";
}

function AddWebsiteForm({ onAdded }: { onAdded: (w: Website) => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0].value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function submit() {
    if (!name.trim() || !baseUrl.trim()) {
      setError("Name and URL are required.");
      return;
    }
    setBusy(true);
    setError(null);
    const body: CreateWebsiteInput = { name: name.trim(), base_url: baseUrl.trim(), category };
    websitesApi
      .create(body)
      .then((website) => {
        setName("");
        setBaseUrl("");
        onAdded(website);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  }

  return (
    <Panel title="Add a website" bodyClassName="p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_3fr_2fr_auto] sm:items-end">
        <div>
          <label htmlFor="website-name" className="text-caption uppercase tracking-wide text-quaternary">Name</label>
          <input id="website-name" className={`${input} mt-1`} placeholder="e.g. NHAI" value={name}
                 onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label htmlFor="website-url" className="text-caption uppercase tracking-wide text-quaternary">Base URL</label>
          <input id="website-url" className={`${input} mt-1`} placeholder="https://nhai.gov.in" value={baseUrl}
                 onChange={(e) => setBaseUrl(e.target.value)} />
        </div>
        <div>
          <label htmlFor="website-category" className="text-caption uppercase tracking-wide text-quaternary">Category</label>
          <select id="website-category" className={`${input} mt-1`} value={category}
                  onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <Button onClick={submit} disabled={busy}>
          <Plus size={14} strokeWidth={1.75} /> Add
        </Button>
      </div>
      {error ? <p className="mt-2 text-body-sm text-error">{error}</p> : null}
    </Panel>
  );
}

export default function WebsitesPage() {
  const [websites, setWebsites] = useState<Website[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    websitesApi.list().then((r) => setWebsites(r.websites)).catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <>
      <PageHeader
        title="Website Intelligence"
        description="Add a government procurement or company website, crawl its permitted pages, and extract tenders with evidence back to the original source."
      />
      <PageBody>
        {error ? (
          <Panel bodyClassName="p-4">
            <p className="text-body-sm text-error">The website API is not reachable: {error}</p>
          </Panel>
        ) : null}

        <AddWebsiteForm onAdded={(w) => setWebsites((prev) => [w, ...(prev ?? [])])} />

        <div className="mt-4">
          <Panel title="Websites">
            {websites && websites.length === 0 ? (
              <EmptyState art={<Globe size={40} strokeWidth={1.5} />} title="No websites added yet">
                Add an official website URL above to start crawling it.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-muted">
                {(websites ?? []).map((w) => (
                  <li key={w.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
                    <Link href={`/websites/${w.id}`} className="min-w-0 flex-1 basis-56 text-left text-label-md text-primary hover:underline">
                      {w.name}
                    </Link>
                    <span className="text-body-sm text-tertiary">{w.base_url}</span>
                    <span className="text-body-sm text-tertiary">{w.category}</span>
                    <span className="text-body-sm text-tertiary">
                      {w.last_crawl_at ? `Last crawl ${when(w.last_crawl_at)}` : "Never crawled"}
                    </span>
                    {w.last_crawl_status ? (
                      <StatusChip tone={statusTone(w.last_crawl_status)}>{w.last_crawl_status}</StatusChip>
                    ) : null}
                    {!w.enabled ? <StatusChip>Disabled</StatusChip> : null}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
