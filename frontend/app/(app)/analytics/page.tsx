"use client";

import { useEffect, useState } from "react";
import { Loading, Notice, PageHeader } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";

interface Analytics {
  observation_window: { from: string | null; to: string; days: number };
  counts: Record<string, number>;
  rates: { oa_per_submitted: number | null; interview_per_submitted: number | null; offer_per_submitted: number | null; denominator: number };
  stage_times: Record<string, { median_days: number | null; n: number }>;
  warnings: string[];
  note: string;
}

const FUNNEL: [string, string][] = [
  ["submitted", "Applications submitted"],
  ["reached_oa", "Reached an online assessment"],
  ["reached_recruiter_screen", "Reached a recruiter screen"],
  ["reached_interview", "Reached an interview"],
  ["offers", "Offers"],
];
const TIMES: Record<string, string> = {
  applied_to_first_response: "Applied to first response",
  applied_to_oa: "Applied to OA",
  oa_to_interview: "OA to interview",
  interview_to_offer: "Interview to offer",
};

export default function AnalyticsPage() {
  const [a, setA] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Analytics>("/api/analytics").then(setA).catch((e) => setError(e.message));
  }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!a) return <Loading />;
  const n = a.counts.submitted;

  return (
    <>
      <PageHeader title="Outcomes">
        Counts from your own tracker{a.observation_window.from ? `, ${fmtDate(a.observation_window.from, { month: "short", day: "numeric", year: "numeric" })} to today (${a.observation_window.days} days)` : ""}. Descriptive only.
      </PageHeader>
      <div className="mb-6 space-y-2">{a.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}</div>

      <section className="mb-8">
        <h2 className="mb-3 text-lg font-semibold">How far applications got</h2>
        <table className="w-full max-w-2xl text-sm">
          <tbody>
            {FUNNEL.map(([k, label]) => {
              const v = a.counts[k] ?? 0;
              return (
                <tr key={k} className="border-t border-rule">
                  <td className="py-2.5 pr-4">{label}</td>
                  <td className="w-full py-2.5">
                    <div className="h-3 rounded-sm bg-paper" aria-hidden>
                      <div className="h-3 rounded-sm bg-pine" style={{ width: n ? `${(v / n) * 100}%` : 0 }} />
                    </div>
                  </td>
                  <td className="whitespace-nowrap py-2.5 pl-4 text-right font-medium">{v}{k !== "submitted" && n > 0 && <span className="font-normal text-muted"> of {n}</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="mt-3 text-sm text-muted">
          {a.counts.pending_no_response} still waiting for a reply (counted as pending, not rejected). {a.counts.rejected} rejected, {a.counts.withdrawn} withdrawn, {a.counts.saved_only} saved but not applied.
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Time between stages</h2>
        <table className="w-full max-w-2xl text-sm">
          <thead className="text-left text-muted"><tr><th className="py-2 font-medium">Stage</th><th className="py-2 font-medium">Median</th><th className="py-2 font-medium">Based on</th></tr></thead>
          <tbody>
            {Object.entries(TIMES).map(([k, label]) => (
              <tr key={k} className="border-t border-rule">
                <td className="py-2.5">{label}</td>
                <td className="py-2.5">{a.stage_times[k]?.median_days == null ? <span className="text-muted">No data</span> : `${a.stage_times[k].median_days} days`}</td>
                <td className="py-2.5 text-muted">{a.stage_times[k]?.n ?? 0} applications</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-6 max-w-prose text-xs text-muted">{a.note}</p>
      </section>
    </>
  );
}
