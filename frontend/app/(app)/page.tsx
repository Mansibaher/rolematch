"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useUser } from "@/components/shell";
import { ButtonLink, Empty, Loading, Notice, cx } from "@/components/ui";
import { api, APP_STATUS_LABEL, ago, type Freshness, type Scores } from "@/lib/api";

interface Dash {
  reminders: {
    timezone: string;
    deadlines: { id: string; application_id: string; title: string; employer: string; job_title: string; due_local_display: string; overdue: boolean; hours_left: number }[];
    follow_ups: { application_id: string; employer: string; title: string; follow_up_local_display: string; overdue: boolean }[];
  };
  stale_saved: { application_id: string; employer: string; title: string; reason: string }[];
  awaiting_response: { application_id: string; employer: string; title: string; days: number }[];
  top_new_jobs: { id: string; employer: string; title: string; scores: Scores; freshness: Freshness }[];
  pipeline: Record<string, number>;
  active_pipeline: number;
  sources_with_errors: string[];
  profile_ready: boolean;
}

function when(hours: number, overdue: boolean) {
  if (overdue) return "overdue";
  if (hours < 24) return `in ${Math.max(1, Math.round(hours))} h`;
  return `in ${Math.round(hours / 24)} days`;
}

export default function Today() {
  const { user } = useUser();
  const [d, setD] = useState<Dash | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Dash>("/api/dashboard").then(setD).catch((e) => setError(e.message));
  }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!d) return <Loading />;

  const actions = d.reminders.deadlines.length + d.reminders.follow_ups.length;
  const date = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: user.timezone });

  return (
    <>
      <header className="mb-8 border-b border-rule pb-5">
        <p className="text-sm text-muted">{date}</p>
        <h1 className="mt-1 text-[2rem] font-semibold leading-tight tracking-[-0.015em]">
          {actions === 0 ? "No deadlines in the next week" : `${actions} thing${actions === 1 ? "" : "s"} due in the next week`}
        </h1>
        <p className="mt-1 text-sm text-muted">{d.active_pipeline} applications in progress. Times shown in {d.reminders.timezone.replace("_", " ")}.</p>
      </header>

      {!d.profile_ready && (
        <div className="mb-6"><Empty title="Start by adding your resume" action={<ButtonLink href="/profile/new" variant="primary">Add resume</ButtonLink>}>Matching starts once you've reviewed what RoleMatch extracted.</Empty></div>
      )}
      {d.sources_with_errors.length > 0 && (
        <div className="mb-6"><Notice tone="warn">Couldn't refresh {d.sources_with_errors.join(", ")} on the last check. Their jobs stay listed until a complete check shows they're gone. <Link className="text-pine underline" href="/sources">See sources</Link></Notice></div>
      )}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section aria-labelledby="due-h">
          <h2 id="due-h" className="mb-3 text-lg font-semibold">Deadlines and follow-ups</h2>
          {actions === 0 ? (
            <p className="text-sm text-muted">Nothing scheduled. Add OA and interview deadlines from an application to see them here.</p>
          ) : (
            <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
              {d.reminders.deadlines.map((x) => (
                <li key={x.id} className="flex items-start justify-between gap-4 px-4 py-3">
                  <div>
                    <Link href={`/applications/${x.application_id}`} className="font-medium hover:text-pine hover:underline">{x.title}</Link>
                    <p className="text-sm text-muted">{x.job_title}, {x.employer}</p>
                  </div>
                  <div className="text-right text-sm">
                    <p className={cx("font-semibold", x.overdue ? "text-danger" : x.hours_left < 48 ? "text-partial" : "")}>{when(x.hours_left, x.overdue)}</p>
                    <p className="text-muted">{x.due_local_display}</p>
                  </div>
                </li>
              ))}
              {d.reminders.follow_ups.map((x) => (
                <li key={x.application_id} className="flex items-start justify-between gap-4 px-4 py-3">
                  <div>
                    <Link href={`/applications/${x.application_id}`} className="font-medium hover:text-pine hover:underline">Follow up</Link>
                    <p className="text-sm text-muted">{x.title}, {x.employer}</p>
                  </div>
                  <p className={cx("text-right text-sm", x.overdue && "text-danger")}>{x.follow_up_local_display}</p>
                </li>
              ))}
            </ul>
          )}

          {(d.stale_saved.length > 0 || d.awaiting_response.length > 0) && (
            <>
              <h2 className="mb-3 mt-8 text-lg font-semibold">Worth a look</h2>
              <ul className="space-y-2 text-sm">
                {d.stale_saved.map((x) => <li key={x.application_id}><Link className="font-medium text-pine hover:underline" href={`/applications/${x.application_id}`}>{x.title}, {x.employer}</Link>: {x.reason}</li>)}
                {d.awaiting_response.map((x) => <li key={x.application_id}><Link className="font-medium text-pine hover:underline" href={`/applications/${x.application_id}`}>{x.title}, {x.employer}</Link>: applied {x.days} days ago, no reply recorded. Consider a follow-up.</li>)}
              </ul>
            </>
          )}
        </section>

        <section aria-labelledby="new-h">
          <h2 id="new-h" className="mb-3 text-lg font-semibold">New jobs worth considering</h2>
          {d.top_new_jobs.length === 0 ? (
            <p className="text-sm text-muted">No untracked jobs first seen in the last three days pass your filters.</p>
          ) : (
            <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
              {d.top_new_jobs.map((j) => (
                <li key={j.id} className="flex items-start justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <Link href={`/jobs/${j.id}`} className="font-medium hover:text-pine hover:underline">{j.title}</Link>
                    <p className="text-sm text-muted">{j.employer}, {j.freshness.posted_at ? `posted ${ago(j.freshness.posted_at)}` : `first seen ${ago(j.freshness.first_seen_at)}`}</p>
                  </div>
                  <p className="text-right"><span className="text-lg font-semibold">{Math.round(j.scores.qualification_match)}</span><span className="block text-xs text-muted">match</span></p>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold">Pipeline</h3>
            {Object.keys(d.pipeline).length === 0 ? <p className="text-sm text-muted">Nothing tracked yet.</p> : (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                {Object.entries(d.pipeline).map(([k, v]) => <div key={k} className="flex justify-between border-b border-rule py-1"><dt>{APP_STATUS_LABEL[k] ?? k}</dt><dd className="font-medium">{v}</dd></div>)}
              </dl>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
