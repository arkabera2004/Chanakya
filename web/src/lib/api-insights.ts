import { request, type Lead, type RunRow } from "@/lib/api";

/** One bucket of model usage. A cache hit and a refused call are never counted as a call. */
export type Usage = {
  calls: number;
  cache_hits: number;
  blocked: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
};

export type Costs = {
  demo: boolean;
  llm_mode: string;
  /** Length of the daily series and of every 30 day figure, in IST days. */
  window_days: number;
  budget: {
    max_cost_per_day_usd: number;
    max_cost_per_run_usd: number;
    max_calls_per_run: number;
    max_deep_analyses_per_run: number;
  };
  spend: {
    today: Usage & { days: number };
    last_7_days: Usage & { days: number };
    last_30_days: Usage & { days: number };
  };
  totals: Usage;
  by_purpose: (Usage & { purpose: string })[];
  by_model: (Usage & { model: string; priced: boolean })[];
  /** Active notices whose model call has not happened yet. */
  waiting: { budget: number; documents: number };
  last_run: RunRow | null;
  /** Oldest first, today last. `date` is an IST calendar day, YYYY-MM-DD. */
  daily: (Usage & { date: string })[];
};

export type Capability = {
  id: string;
  name: string;
  offering: string;
  offering_status: string;
  confidence: string;
};

export type Band = "P1" | "P2" | "P3";

export type ProductGroup = Capability & {
  total: number;
  bands: Record<Band, number>;
  notices: (Lead & { capability_score: number | null })[];
  /** Notices in this group beyond the ones listed. */
  more: number;
};

export type Products = {
  kb_version: string;
  total: number;
  bands: Record<Band, number>;
  /** Active P1 to P3 notices whose leading match is not a capability in the book. */
  ungrouped: number;
  groups: ProductGroup[];
};

export const insights = {
  costs: () => request<Costs>("/api/costs"),
  products: (top?: number) => request<Products>(`/api/products${top ? `?top=${top}` : ""}`),
  capabilities: () => request<{ kb_version: string; capabilities: Capability[] }>("/api/capabilities"),
};

/** "Arjuna" from "Arjuna (safety compliance)". Null when the book says there is no named offering. */
export function offeringName(offering: string | null | undefined): string | null {
  const short = (offering || "").split(/[(:]/)[0].trim();
  return !short || /^none\b/i.test(short) ? null : short;
}

/** The label for a capability in a list: the offering when it has one, then what it does. */
export function capabilityLabel(capability: Capability): string {
  const offering = offeringName(capability.offering);
  return offering ? `${offering}: ${capability.name}` : capability.name;
}
