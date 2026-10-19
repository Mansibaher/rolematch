"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, ButtonLink, Field, Input, Loading, Notice, Select, StatusMark, cx } from "@/components/ui";
import { api, APP_STATUS_LABEL, ago, fmtDate, type Check, type JobCard, type MatchResult, type RequirementResult } from "@/lib/api";

interface Detail {
  job: JobCard & {
    description_text: string; department: string | null; salary_basis: string | null; remote_restriction: string | null;
    employment_type_basis: string; status: string; requisition_id: string | null;
    also_listed_as: { id: string; source: string; reason: string; apply_url: string | null }[];
  };
  match: MatchResult;
  extraction: { structured: boolean; warnings: string[]; instruction_like_text: { text: string }[] | null; eligibility: { sponsorship: string } | null };
  application: { id: string; status: string } | null;
  feedback: string | null;
}

const KIND_TITLE: Record<string, string> = {
  required: "Required qualifications",
  preferred: "Preferred qualifications",
  unclear: "Qualifications (required or preferred not stated)",
  responsibility: "Responsibilities",
};
const COMPONENT_LABEL: Record<string, string> = {
  required_coverage: "Required qualifications covered",
  preferred_coverage: "Preferred qualifications covered",
  evidence_depth: "How directly skills are shown",
  seniority: "Level compatibility",
};
const CHECK_STATE: Record<Check["status"], string> = { pass: "Meets", fail: "Doesn't meet", unknown: "Not stated", not_applicable: "n/a" };

function RequirementRow({ r }: { r: RequirementResult }) {
  return (
    <li className="grid gap-2 px-4 py-4 sm:grid-cols-[170px_minmax(0,1fr)] sm:px-5">
      <div className="pt-0.5"><StatusMark status={r.status} /></div>
      <div className="min-w-0">
        <p className="font-medium">{r.text}</p>
        {r.confidence !== "high" && <p className="mt-0.5 text-xs text-unclear">Classification: {r.basis}</p>}
        {r.evidence.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {r.evidence.map((ev, i) => (
              <li key={i} className="border-l-2 border-pine/60 pl-3">
                {ev.context === "user_added" ? (
                  <p className="text-sm"><span className="font-medium">Added by you:</span> {ev.text}</p>
                ) : (
                  <p className="quote text-ink/90">{ev.text}</p>
                )}
                <p className="text-xs text-muted">
                  {ev.skill && `${ev.skill}, `}
                  {ev.context === "skills" ? "skills section" : ev.context === "user_added" ? "not in resume text" : ev.entry_title ? `${ev.entry_title} (${ev.context})` : ev.context}
                </p>
              </li>
            ))}
          </ul>
        )}
        {r.related && (
          <div className="mt-2 border-l-2 border-dashed border-unclear pl-3">
            <p className="quote text-ink/80">{r.related.text}</p>
            <p className="text-xs text-muted">Possibly related ({r.related.entry_title}); found by text similarity {r.related.similarity.toFixed(2)}, which is not proof.</p>
          </div>
        )}
        {r.notes.map((n) => <p key={n} className="mt-1 text-sm text-muted">{n}</p>)}
        {r.status === "not_evidenced" && r.notes.length === 0 && (
          <p className="mt-1 text-sm text-muted">Nothing on your resume shows this. If you have it, add it to your profile.</p>
        )}
      </div>
    </li>
  );
}

function ApplyPanel({ appId, onDone }: { appId: string; onDone: () => void }) {
  const [versions, setVersions] = useState<{ id: string; label: string }[]>([]);
  const [version, setVersion] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<{ active_version_id: string | null; resumes: { label: string; versions: { id: string; version_number: number; origin: string }[] }[] }>("/api/resumes").then((d) => {
      const list = d.resumes.flatMap((r) => r.versions.map((v) => ({ id: v.id, label: `${r.label}, version ${v.version_number}${v.origin === "tailored" ? " (tailored)" : ""}` })));
      setVersions(list.reverse());
      setVersion(d.active_version_id ?? list[0]?.id ?? "");
    });
  }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const occurred = new Date(`${date}T12:00:00`);
      const at = occurred > new Date() ? new Date() : occurred;
      await api(`/api/applications/${appId}/status`, { method: "POST", json: { status: "applied", resume_version_id: version || null, occurred_at: at.toISOString() } });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <form onSubmit={submit} className="mt-3 space-y-3 rounded-md border border-rule bg-paper p-3">
      <p className="text-sm">Record that you applied. RoleMatch saves a copy of this posting and the resume version you used.</p>
      <Field label="Resume version you submitted">{(id) => (
        <Select id={id} value={version} onChange={(e) => setVersion(e.target.value)}>{versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}</Select>
      )}</Field>
      <Field label="Date applied">{(id) => <Input id={id} type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} />}</Field>
      {error && <Notice tone="error">{error}</Notice>}
      <Button type="submit" variant="primary">Save as applied</Button>
    </form>
  );
}

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [showDesc, setShowDesc] = useState(false);

  const load = useCallback(async () => {
    try {
      setD(await api<Detail>(`/api/jobs/${id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!d) return <Loading label="Checking this posting against your profile" />;

  const { job, match } = d;
  const byKind = (k: string) => match.requirements.filter((r) => r.kind === k);

  async function save() {
    const a = await api<{ id: string }>("/api/applications", { method: "POST", json: { job_id: job.id } });
    await load();
    return a.id;
  }
  async function feedback(label: string) {
    await api(`/api/jobs/${job.id}/feedback`, { method: "POST", json: { label } });
    await load();
  }

  return (
    <article>
      <nav className="mb-4 text-sm"><Link href="/jobs" className="text-pine hover:underline">Jobs</Link></nav>
      <header className="mb-6 border-b border-rule pb-5">
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.01em]">{job.title}</h1>
        <p className="mt-1 text-lg">{job.employer}</p>
        <p className="mt-1 text-sm text-muted">
          {job.location || "Location not stated"}
          {job.remote_type !== "unknown" ? `, ${job.remote_type === "onsite" ? "on-site" : job.remote_type}` : ", work arrangement not stated"}
          {job.remote_restriction && ` (restricted to ${job.remote_restriction})`}
          {job.employment_type !== "unknown" && `, ${job.employment_type.replace("_", "-")}`}
          {job.salary && `, ${job.salary.currency} ${job.salary.min.toLocaleString()}–${job.salary.max.toLocaleString()} per ${job.salary.interval}`}
        </p>
        <p className="mt-1 text-sm text-muted">
          {job.freshness.posted_at ? `Posted ${fmtDate(job.freshness.posted_at, { month: "short", day: "numeric", year: "numeric" })}` : "Posting date not given by the source"}
          {`. First seen by RoleMatch ${ago(job.freshness.first_seen_at)}. `}
          {job.source.is_manual ? "Added by you, not monitored." : job.freshness.last_verified_at ? `Last confirmed listed ${ago(job.freshness.last_verified_at)}.` : ""}
          {` Source: ${job.source.label}.`}
        </p>
        <div className="mt-3 space-y-2">
          {job.status === "closed" && <Notice tone="warn">The employer no longer lists this job.</Notice>}
          {job.freshness.unverified && job.status !== "closed" && !job.source.is_manual && <Notice tone="warn">This listing hasn't been re-checked recently, so it may have closed.</Notice>}
          {d.extraction.instruction_like_text && d.extraction.instruction_like_text.length > 0 && (
            <Notice tone="warn" title="This posting contains text aimed at automated screeners">“{d.extraction.instruction_like_text[0].text}” was ignored for matching.</Notice>
          )}
          {job.also_listed_as.length > 0 && <Notice>Also listed via {job.also_listed_as.map((x) => x.source).join(", ")} ({job.also_listed_as[0].reason}).</Notice>}
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div className="space-y-8">
          {(["required", "unclear", "preferred"] as const).map((k) => byKind(k).length > 0 && (
            <section key={k} aria-labelledby={`req-${k}`}>
              <h2 id={`req-${k}`} className="mb-2 text-lg font-semibold">{KIND_TITLE[k]}</h2>
              <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
                {byKind(k).map((r) => <RequirementRow key={r.id} r={r} />)}
              </ul>
            </section>
          ))}
          {!match.requirements.some((r) => r.kind !== "responsibility") && <Notice tone="warn">No checkable qualifications could be extracted from this posting. Read the description below.</Notice>}
          {byKind("responsibility").length > 0 && (
            <details className="rounded-lg border border-rule bg-panel">
              <summary className="cursor-pointer px-5 py-3 font-semibold">Responsibilities ({byKind("responsibility").length})</summary>
              <ul className="divide-y divide-rule border-t border-rule">{byKind("responsibility").map((r) => <RequirementRow key={r.id} r={r} />)}</ul>
            </details>
          )}
          <section>
            <button type="button" className="text-sm font-medium text-pine hover:underline" aria-expanded={showDesc} onClick={() => setShowDesc((v) => !v)}>
              {showDesc ? "Hide" : "Show"} the full posting
            </button>
            {showDesc && <div className="mt-3 whitespace-pre-wrap rounded-lg border border-rule bg-panel p-5 text-sm leading-relaxed">{job.description_text}</div>}
          </section>
        </div>

        <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start">
          <section className="rounded-lg border border-rule bg-panel p-5" aria-labelledby="actions-h">
            <h2 id="actions-h" className="sr-only">Actions</h2>
            <div className="flex flex-col gap-2">
              {job.apply_url && <ButtonLink href={job.apply_url} external variant="primary">Open the employer's posting</ButtonLink>}
              {d.application ? (
                <p className="text-sm">In your tracker: <Link href={`/applications/${d.application.id}`} className="font-medium text-pine hover:underline">{APP_STATUS_LABEL[d.application.status]}</Link></p>
              ) : (
                <Button onClick={() => void save()}>Save to tracker</Button>
              )}
              {(!d.application || d.application.status === "saved") && !applying && (
                <Button onClick={async () => { if (!d.application) await save(); setApplying(true); }}>I applied to this job</Button>
              )}
              {applying && d.application && <ApplyPanel appId={d.application.id} onDone={() => { setApplying(false); void load(); }} />}
              <ButtonLink href={`/jobs/${job.id}/tailor`}>Tailor resume wording</ButtonLink>
            </div>
            <p className="mt-3 text-xs text-muted">Opening the posting doesn't change anything here. RoleMatch never applies or contacts anyone for you.</p>
          </section>

          <section className="rounded-lg border border-rule bg-panel p-5" aria-labelledby="score-h">
            <h2 id="score-h" className="font-semibold">How this was scored</h2>
            <div className="mt-3 grid grid-cols-2 gap-4">
              <div><p className="num text-3xl font-semibold leading-none">{Math.round(match.scores.qualification_match)}</p><p className="mt-1 text-xs text-muted">Qualification match out of 100</p></div>
              <div><p className="num text-3xl font-semibold leading-none">{Math.round(match.scores.application_priority)}</p><p className="mt-1 text-xs text-muted">Application priority out of 100</p></div>
            </div>
            <table className="mt-4 w-full text-sm">
              <caption className="mb-1 text-left text-xs text-muted">Qualification match components</caption>
              <tbody>
                {Object.entries(COMPONENT_LABEL).map(([k, label]) => {
                  const v = match.components[k as keyof typeof COMPONENT_LABEL & keyof MatchResult["components"]] as number | null;
                  return (
                    <tr key={k} className="border-t border-rule">
                      <td className="py-1.5 pr-2">{label}<span className="block text-xs text-muted">weight {Math.round((match.weights.qualification[k] ?? 0) * 100)}%</span></td>
                      <td className="py-1.5 text-right font-medium">{v === null ? <span className="text-muted">n/a</span> : Math.round(v * 100)}</td>
                    </tr>
                  );
                })}
                <tr className="border-t border-rule"><td className="py-1.5">Preference fit<span className="block text-xs text-muted">priority weight {Math.round(match.weights.priority.preference_fit * 100)}%</span></td><td className="py-1.5 text-right font-medium">{Math.round(match.scores.preference_fit)}</td></tr>
                <tr className="border-t border-rule"><td className="py-1.5">Freshness<span className="block text-xs text-muted">priority weight {Math.round(match.weights.priority.freshness * 100)}%</span></td><td className="py-1.5 text-right font-medium">{Math.round(match.scores.freshness)}</td></tr>
              </tbody>
            </table>
            <p className="mt-3 text-xs text-muted">{match.seniority.note}</p>
            {match.components.semantic_similarity !== null && <p className="mt-1 text-xs text-muted">Text similarity between your resume and the posting: {match.components.semantic_similarity.toFixed(2)} (one signal among several).</p>}
            <p className="mt-3 text-xs text-muted">{match.disclaimer}</p>
          </section>

          <section className="rounded-lg border border-rule bg-panel p-5" aria-labelledby="checks-h">
            <h2 id="checks-h" className="font-semibold">Your filters</h2>
            {!match.eligible && <p className="mt-2 rounded bg-danger-wash px-2 py-1 text-sm">Hidden from your feed by a filter you set to hide non-matching jobs.</p>}
            <ul className="mt-3 space-y-3 text-sm">
              {match.checks.map((c) => (
                <li key={c.name}>
                  <p className="flex justify-between gap-2">
                    <span className="font-medium">{c.label}</span>
                    <span className={cx(c.status === "fail" ? "text-danger" : c.status === "unknown" ? "text-unclear" : "text-pine")}>{CHECK_STATE[c.status]}</span>
                  </p>
                  <p className="text-muted">{c.reason}{!c.hard && c.status === "fail" ? " Ranked lower only." : ""}</p>
                  {c.evidence && <p className="quote mt-1 text-ink/80" style={{ fontFamily: "inherit", fontStyle: "normal" }}>“{c.evidence}”</p>}
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-lg border border-rule bg-panel p-5" aria-labelledby="fb-h">
            <h2 id="fb-h" className="font-semibold">Is this the kind of job you want?</h2>
            <p className="mt-1 text-xs text-muted">Adjusts ranking of similar jobs a little. It never changes filters or evidence.</p>
            <div className="mt-3 flex gap-2">
              <Button variant={d.feedback === "relevant" ? "primary" : "secondary"} aria-pressed={d.feedback === "relevant"} onClick={() => feedback("relevant")}>Relevant</Button>
              <Button variant={d.feedback === "not_relevant" ? "primary" : "secondary"} aria-pressed={d.feedback === "not_relevant"} onClick={() => feedback("not_relevant")}>Not relevant</Button>
            </div>
          </section>
          {match.explanation.warnings.length > 0 && <Notice tone="warn">{match.explanation.warnings.join(" ")}</Notice>}
        </aside>
      </div>
    </article>
  );
}
