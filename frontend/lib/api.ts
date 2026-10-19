// Thin client for the RoleMatch API. All requests are same-origin (proxied by Next.js) with the session cookie.

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function messageFrom(detail: unknown, status: number): { message: string; code?: string } {
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; code?: string };
    return { message: d.message, code: d.code };
  }
  if (Array.isArray(detail) && detail.length && typeof detail[0] === "object" && detail[0] && "msg" in detail[0]) {
    return { message: (detail as { msg: string; loc?: string[] }[]).map((d) => `${(d.loc ?? []).slice(-1)[0] ?? "field"}: ${d.msg}`).join("; ") };
  }
  return { message: status >= 500 ? "The server hit an error. Try again in a moment." : `Request failed (${status}).` };
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET") headers.set("X-RoleMatch-CSRF", "1");
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, method, headers, body, credentials: "same-origin" });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    const { message, code } = messageFrom(detail, res.status);
    throw new ApiError(res.status, message, code);
  }
  const ctype = res.headers.get("content-type") ?? "";
  return (ctype.includes("json") ? res.json() : res.text()) as Promise<T>;
}

// ---------------------------------------------------------------- types

export type ReqStatus = "supported" | "partially_supported" | "not_evidenced" | "unclear";

export interface Evidence {
  text: string;
  context: string;
  start?: number | null;
  end?: number | null;
  entry_title?: string | null;
  skill?: string;
  location?: { kind: string; index: number } | null;
}

export interface RequirementResult {
  id: string;
  kind: "required" | "preferred" | "responsibility" | "unclear";
  text: string;
  category: string;
  status: ReqStatus;
  confidence: "high" | "medium" | "low";
  basis: string;
  evidence: Evidence[];
  notes: string[];
  matched_skills: string[];
  missing_skills: string[];
  related: { text: string; similarity: number; entry_title: string; context: string } | null;
}

export interface Check {
  name: string;
  label: string;
  status: "pass" | "fail" | "unknown" | "not_applicable";
  hard: boolean;
  reason: string;
  evidence: string | null;
}

export interface Scores {
  qualification_match: number;
  preference_fit: number;
  freshness: number;
  application_priority: number;
}

export interface Freshness {
  score: number;
  basis: "posted" | "first_seen";
  age_days: number;
  posted_at: string | null;
  posted_at_basis: string | null;
  first_seen_at: string;
  last_verified_at: string | null;
  unverified: boolean;
  status: string;
}

export interface JobCard {
  id: string;
  employer: string;
  title: string;
  location: string;
  remote_type: string;
  employment_type: string;
  source: { kind: string | null; label: string; is_fixture: boolean; is_manual: boolean };
  freshness: Freshness;
  apply_url: string | null;
  scores: Scores;
  eligible: boolean;
  excluded_by: string[];
  uncertain_hard: string[];
  deprioritized_by: string[];
  strengths: string[];
  gaps: string[];
  unknowns: string[];
  role_families: string[];
  seniority: string;
  application_status: string | null;
  salary: { min: number; max: number; currency: string; interval: string } | null;
  required_statuses?: ReqStatus[];
}

export interface MatchResult {
  eligible: boolean;
  eligibility: { eligible: boolean; excluded_by: string[]; uncertain_hard: string[]; soft_score: number | null; soft_deprioritized_by: string[] };
  checks: Check[];
  requirements: RequirementResult[];
  scores: Scores;
  components: {
    required_coverage: number | null;
    preferred_coverage: number | null;
    evidence_depth: number | null;
    seniority: number | null;
    semantic_similarity: number | null;
    lexical_evidence_strength: number | null;
    feedback_adjustment: number;
    counts: { required: number; preferred: number };
  };
  weights: { qualification: Record<string, number>; priority: Record<string, number> };
  seniority: { level: string; basis: string | null; note: string };
  freshness: Freshness;
  explanation: { strengths: unknown[]; gaps: { requirement: string }[]; unknowns: { requirement: string; notes: string[] }[]; warnings: string[] };
  disclaimer: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  timezone: string;
  is_demo: boolean;
  llm_opt_in: boolean;
}

export const STATUS_LABEL: Record<ReqStatus, string> = {
  supported: "Supported",
  partially_supported: "Partly supported",
  not_evidenced: "Not shown on resume",
  unclear: "Unclear",
};

export const APP_STATUS_LABEL: Record<string, string> = {
  saved: "Saved",
  applied: "Applied",
  oa_received: "OA received",
  oa_completed: "OA completed",
  recruiter_screen: "Recruiter screen",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  closed_without_application: "Closed without applying",
};

export function fmtDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" }): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, opts);
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}
