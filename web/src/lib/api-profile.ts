import { request } from "@/lib/api";

/** Wayam's credentials as eligibility reads them. Null, or an empty list, means not stated. */
export type CompanyProfile = {
  name: string;
  annual_turnover_inr_crore: number | null;
  average_turnover_3y_inr_crore: number | null;
  years_in_operation: number | null;
  certifications: string[];
  msme_registered: boolean | null;
  dpiit_startup: boolean | null;
  past_government_projects: number | null;
  cert_in_empanelled: boolean | null;
};

export type ProfileState = {
  profile: CompanyProfile;
  /** Someone has saved the profile from this page at least once. */
  saved: boolean;
  saved_at: string | null;
  /** The file the saved values live in, inside the data folder. */
  stored_in: string;
  config_file: string | null;
};

export const profileApi = {
  read: () => request<ProfileState>("/api/profile"),
  save: (profile: Partial<CompanyProfile>) =>
    request<ProfileState>("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }),
};
