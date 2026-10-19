"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ButtonLink, Empty, EvidenceStrip, Input, Loading, Notice, PageHeader, Select, cx } from "@/components/ui";
import { api, ApiError, APP_STATUS_LABEL, ago, fmtDate, type JobCard, type ReqStatus } from "@/lib/api";

interface Feed {
  jobs: JobCard[];
  excluded: JobCard[];
  excluded_count: number;
  stats: { visible: number; candidates: number; computed: number; cached: number; timing_ms: { total: number } };
  no_results: { suggestions: string[]; note: string } | null;
  disclaimer: string;
}

const CHECK_LABEL: Record<string, string> = {
  excluded_employers: "excluded employer", excluded_keywords: "excluded keyword", role_family: "role", location: "location",
  work_mode: "work arrangement", employment_type: "employment type", experience_level: "level", salary: "salary",
  sponsorship: "sponsorship", citizenship: "citizenship requirement",
};

function Freshness({ f }: { f: JobCard["freshness"] }) {
  const when = f.posted_at ? `Posted ${ago(f.posted_at)}` : `First seen ${ago(f.first_seen_at)}, posting date not given`;
  return (
    <span className={cx(f.unverified && "text-partial")}>
      {when}
      {f.unverified && ", not re-checked recently"}
    </span>
  );
}

function JobRow({ j, dim }: { j: JobCard; dim?: boolean }) {
  return (
    <li className={cx("grid gap-3 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_150px] sm:px-5", dim && "bg-paper/60")}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <Link href={`/jobs/${j.id}`} className="text-base font-semibold hover:text-pine hover:underline">{j.title}</Link>
          {j.application_status && <span className="rounded bg-pine-wash px-2 py-0.5 text-xs text-pine-deep">{APP_STATUS_LABEL[j.application_status]}</span>}
          {j.source.is_fixture && <span className="rounded bg-paper px-2 py-0.5 text-xs text-muted">Demo posting</span>}
        </div>
        <p className="text-sm">
          {j.employer}, {j.location || "location not stated"}
          {j.remote_type !== "unknown" && <span className="text-muted"> ({j.remote_type === "onsite" ? "on-site" : j.remote_type})</span>}
        </p>
        <p className="text-xs text-muted"><Freshness f={j.freshness} />.{!j.source.is_fixture && ` From ${j.source.label}.`}</p>
        <EvidenceStrip className="mt-2.5" statuses={(j.required_statuses ?? []) as ReqStatus[]} />
        <div className="mt-2 space-y-0.5 text-sm">
          {j.strengths.length > 0 && <p><span className="text-muted">Shown on your resume: </span>{j.strengths.join(", ")}</p>}
          {j.gaps.length > 0 && <p className="text-ink/85"><span className="text-muted">Not shown: </span>{j.gaps[0]}{j.gaps.length > 1 && ` and ${j.gaps.length - 1} more`}</p>}
          {j.unknowns.length > 0 && <p><span className="text-muted">Unknown from posting: </span>{j.unknowns.join(", ").toLowerCase()}</p>}
          {j.excluded_by.length > 0 && <p className="text-danger">Hidden by your filter: {j.excluded_by.map((c) => CHECK_LABEL[c] ?? c).join(", ")}</p>}
          {j.excluded_by.length === 0 && j.deprioritized_by.length > 0 && <p className="text-muted">Ranked lower for {j.deprioritized_by.map((c) => CHECK_LABEL[c] ?? c).join(", ")}</p>}
        </div>
      </div>
      <div className="flex flex-row items-start justify-between gap-3 sm:flex-col sm:items-end sm:text-right">
        <div>
          <p className="num text-2xl font-semibold leading-none">{Math.round(j.scores.qualification_match)}</p>
          <p className="text-xs text-muted">qualification match</p>
          <p className="mt-1 text-xs text-muted">priority {Math.round(j.scores.application_priority)}</p>
        </div>
        {j.apply_url && <a className="text-sm text-pine hover:underline" href={j.apply_url} target="_blank" rel="noopener noreferrer">Employer page<span className="sr-only"> (opens in a new tab)</span></a>}
      </div>
    </li>
  );
}

export default function JobsPage() {
  const [sort, setSort] = useState("priority");
  const [family, setFamily] = useState("");
  const [loc, setLoc] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [showExcluded, setShowExcluded] = useState(false);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ sort, include_excluded: "true" });
    if (family) params.set("family", family);
    if (loc) params.set("location", loc);
    if (status) params.set("app_status", status);
    if (q) params.set("q", q);
    try {
      setFeed(await api<Feed>(`/api/jobs/feed?${params}`));
      setError(null);
    } catch (e) {
      setError({ status: e instanceof ApiError ? e.status : 0, message: (e as Error).message });
    } finally {
      setLoading(false);
    }
  }, [sort, family, loc, status, q]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  if (error?.status === 409) {
    return (
      <>
        <PageHeader title="Jobs" />
        <Empty title="Review your profile first" action={<ButtonLink href="/profile" variant="primary">Go to resume profile</ButtonLink>}>{error.message}</Empty>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Jobs" actions={<ButtonLink href="/jobs/new">Add a job manually</ButtonLink>}>
        Ranked by application priority: qualification match, your preferences and posting freshness. Scores describe documented fit, not your chance of an interview.
      </PageHeader>

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5" role="search">
        <label className="text-sm">Sort by
          <Select value={sort} onChange={(e) => setSort(e.target.value)} className="mt-1">
            <option value="priority">Application priority</option>
            <option value="qualification">Qualification match</option>
            <option value="posted">Posting date (when known)</option>
            <option value="first_seen">First seen by RoleMatch</option>
          </Select>
        </label>
        <label className="text-sm">Role
          <Select value={family} onChange={(e) => setFamily(e.target.value)} className="mt-1">
            <option value="">Any role</option>
            <option value="software_engineer">Software engineer</option>
            <option value="backend_engineer">Backend engineer</option>
            <option value="ml_engineer">Applied AI/ML engineer</option>
            <option value="data_engineer">Data engineer</option>
            <option value="frontend_engineer">Frontend engineer</option>
          </Select>
        </label>
        <label className="text-sm">Location contains
          <Input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="e.g. Los Angeles or remote" className="mt-1" />
        </label>
        <label className="text-sm">Tracking status
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1">
            <option value="">Any</option>
            <option value="none">Not tracked yet</option>
            {Object.entries(APP_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </label>
        <label className="text-sm">Title or employer
          <Input value={q} onChange={(e) => setQ(e.target.value)} className="mt-1" />
        </label>
      </div>

      {error && <Notice tone="error">{error.message}</Notice>}
      {loading && !feed && <Loading label="Ranking jobs against your profile" />}
      {feed && (
        <>
          <p className="mb-2 text-sm text-muted" aria-live="polite">
            {feed.jobs.length} jobs shown{feed.excluded_count > 0 && `, ${feed.excluded_count} hidden by your filters`}. Checked {feed.stats.visible} postings from the companies you follow
            {" "}in {Math.round(feed.stats.timing_ms.total)} ms.
          </p>
          <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted" aria-label="Legend">
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-[2px] bg-supported" />supported</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-[2px] bg-partial" />partly supported</span>
            <span className="flex items-center gap-1.5"><span className="hatch-unclear h-2.5 w-4 rounded-[2px]" />unclear</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-[2px] border border-absent" />not shown on resume</span>
          </div>
          {feed.jobs.length === 0 ? (
            <Empty title={feed.stats.visible === 0 ? "No jobs to rank yet" : "No jobs pass your filters"}
              action={<div className="flex gap-2"><ButtonLink href="/preferences">Review preferences</ButtonLink><ButtonLink href="/sources">Follow companies</ButtonLink></div>}>
              {feed.no_results && (
                <ul className="list-disc space-y-1 pl-5">{feed.no_results.suggestions.map((s) => <li key={s}>{s}</li>)}<li>{feed.no_results.note}</li></ul>
              )}
            </Empty>
          ) : (
            <ul className={cx("divide-y divide-rule rounded-lg border border-rule bg-panel", loading && "opacity-60")}>
              {feed.jobs.map((j) => <JobRow key={j.id} j={j} />)}
            </ul>
          )}
          {feed.excluded_count > 0 && (
            <section className="mt-8">
              <button type="button" className="text-sm font-medium text-pine hover:underline" aria-expanded={showExcluded} onClick={() => setShowExcluded((v) => !v)}>
                {showExcluded ? "Hide" : "Show"} the {feed.excluded_count} jobs hidden by your filters
              </button>
              {showExcluded && (
                <ul className="mt-3 divide-y divide-rule rounded-lg border border-rule bg-panel">
                  {feed.excluded.map((j) => <JobRow key={j.id} j={j} dim />)}
                </ul>
              )}
            </section>
          )}
          <p className="mt-6 text-xs text-muted">Data as of {fmtDate(new Date().toISOString(), { hour: "numeric", minute: "2-digit" })}. {feed.disclaimer}</p>
        </>
      )}
    </>
  );
}
