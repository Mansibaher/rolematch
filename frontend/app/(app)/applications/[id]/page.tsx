"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useUser } from "@/components/shell";
import { Button, Field, Input, Loading, Notice, Select, Textarea, cx } from "@/components/ui";
import { api, APP_STATUS_LABEL, fmtDate } from "@/lib/api";
import type { App } from "../page";

type Detail = App & { job_snapshot: { captured_at: string; description_text: string; title: string; employer: string; location: string; apply_url: string | null } | null };

const KINDS: Record<string, string> = { oa: "Online assessment", interview: "Interview", follow_up: "Follow-up", other: "Other" };
const ZONES = ["America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "UTC", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore"];

export default function ApplicationDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useUser();
  const router = useRouter();
  const [a, setA] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [st, setSt] = useState({ status: "", note: "", date: "" });
  const [dl, setDl] = useState({ kind: "oa", title: "", due_local: "", tz: user.timezone, notes: "" });
  const [notes, setNotes] = useState("");
  const [follow, setFollow] = useState("");
  const [showSnap, setShowSnap] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api<Detail>(`/api/applications/${id}`);
      setA(d);
      setNotes(d.notes);
      setSt((s) => ({ ...s, status: s.status || d.status }));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!a) return error ? <Notice tone="error">{error}</Notice> : <Loading />;
  const zones = Array.from(new Set([user.timezone, ...ZONES]));

  return (
    <article>
      <nav className="mb-4 text-sm"><Link href="/applications" className="text-pine hover:underline">Applications</Link></nav>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-5">
        <div>
          <h1 className="text-[1.75rem] font-semibold leading-tight">{a.title}</h1>
          <p className="text-lg">{a.employer}</p>
          <p className="mt-1 text-sm text-muted">
            {APP_STATUS_LABEL[a.status]}
            {a.applied_at && `. Applied ${fmtDate(a.applied_at, { month: "short", day: "numeric", year: "numeric" })}`}
            {a.resume_version && ` with resume version ${a.resume_version.version_number}${a.resume_version.origin === "tailored" ? " (tailored)" : ""}`}.
          </p>
        </div>
        <div className="flex gap-2">
          {a.job_id && <Link className="rounded-md border border-rule-strong bg-panel px-3.5 py-2 text-sm font-medium hover:border-ink" href={`/jobs/${a.job_id}`}>View match</Link>}
          {a.external_url && <a className="rounded-md border border-rule-strong bg-panel px-3.5 py-2 text-sm font-medium hover:border-ink" href={a.external_url} target="_blank" rel="noopener noreferrer">Employer page</a>}
        </div>
      </header>
      {error && <div className="mb-4"><Notice tone="error">{error}</Notice></div>}
      {a.source_listing_status === "closed" && <div className="mb-4"><Notice>The employer no longer lists this job. Your application status is unaffected.</Notice></div>}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-8">
          <section aria-labelledby="dl-h">
            <h2 id="dl-h" className="mb-2 text-lg font-semibold">Deadlines</h2>
            {a.deadlines.length === 0 && <p className="text-sm text-muted">No deadlines yet.</p>}
            <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
              {a.deadlines.map((d) => (
                <li key={d.id} className={cx("flex flex-wrap items-start justify-between gap-3 px-4 py-3", d.completed_at && "opacity-60")}>
                  <div>
                    <p className="font-medium">{d.title} <span className="text-sm font-normal text-muted">({KINDS[d.kind]})</span></p>
                    <p className={cx("text-sm", d.overdue ? "text-danger" : "")}>{d.due_local_display}{d.overdue && ", overdue"}</p>
                    {d.entered_tz !== user.timezone && <p className="text-xs text-muted">Entered as {d.entered_local_display}</p>}
                    {d.notes && <p className="text-sm text-muted">{d.notes}</p>}
                  </div>
                  <div className="flex gap-2">
                    <Button variant="quiet" onClick={() => run(() => api(`/api/deadlines/${d.id}/complete`, { method: "POST" }))}>{d.completed_at ? "Mark not done" : "Mark done"}</Button>
                    <Button variant="quiet" onClick={() => run(() => api(`/api/deadlines/${d.id}`, { method: "DELETE" }))}>Delete</Button>
                  </div>
                </li>
              ))}
            </ul>
            <form className="mt-4 grid gap-3 rounded-lg border border-rule bg-panel p-4 sm:grid-cols-2"
              onSubmit={(e) => { e.preventDefault(); void run(() => api(`/api/applications/${a.id}/deadlines`, { method: "POST", json: dl })).then(() => setDl({ ...dl, title: "", due_local: "", notes: "" })); }}>
              <Field label="Type">{(fid) => <Select id={fid} value={dl.kind} onChange={(e) => setDl({ ...dl, kind: e.target.value })}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>}</Field>
              <Field label="Title">{(fid) => <Input id={fid} value={dl.title} placeholder="e.g. HackerRank OA" onChange={(e) => setDl({ ...dl, title: e.target.value })} />}</Field>
              <Field label="Due (local time)">{(fid) => <Input id={fid} type="datetime-local" required value={dl.due_local} onChange={(e) => setDl({ ...dl, due_local: e.target.value })} />}</Field>
              <Field label="Time zone" hint="Use the zone the employer gave; it's shown in yours.">{(fid) => <Select id={fid} value={dl.tz} onChange={(e) => setDl({ ...dl, tz: e.target.value })}>{zones.map((z) => <option key={z}>{z}</option>)}</Select>}</Field>
              <div className="sm:col-span-2"><Button type="submit" variant="primary">Add deadline</Button></div>
            </form>
          </section>

          <section aria-labelledby="notes-h">
            <h2 id="notes-h" className="mb-2 text-lg font-semibold">Notes</h2>
            <Textarea aria-labelledby="notes-h" rows={5} value={notes} onChange={(e) => setNotes(e.target.value)} />
            <Button className="mt-2" onClick={() => run(() => api(`/api/applications/${a.id}`, { method: "PATCH", json: { notes } }))}>Save notes</Button>
          </section>

          {a.has_snapshot && a.job_snapshot && (
            <section>
              <button type="button" className="text-sm font-medium text-pine hover:underline" aria-expanded={showSnap} onClick={() => setShowSnap((v) => !v)}>
                {showSnap ? "Hide" : "Show"} the posting as it was when you applied
              </button>
              {a.job_changed_since_application && <p className="mt-1 text-sm text-partial">The employer has edited the posting since you applied.</p>}
              {showSnap && (
                <div className="mt-3 rounded-lg border border-rule bg-panel p-5">
                  <p className="text-xs text-muted">Saved {fmtDate(a.job_snapshot.captured_at, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}</p>
                  <p className="mt-2 font-semibold">{a.job_snapshot.title}, {a.job_snapshot.employer}</p>
                  <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">{a.job_snapshot.description_text}</div>
                </div>
              )}
            </section>
          )}
        </div>

        <aside className="space-y-5">
          <section className="rounded-lg border border-rule bg-panel p-5">
            <h2 className="font-semibold">Update status</h2>
            <form className="mt-3 space-y-3" onSubmit={(e) => {
              e.preventDefault();
              const when = st.date ? new Date(`${st.date}T12:00:00`) : null;
              void run(() => api(`/api/applications/${a.id}/status`, { method: "POST", json: { status: st.status, note: st.note, occurred_at: when && when < new Date() ? when.toISOString() : null } })).then(() => setSt({ ...st, note: "", date: "" }));
            }}>
              <Field label="New status">{(fid) => <Select id={fid} value={st.status} onChange={(e) => setSt({ ...st, status: e.target.value })}>{Object.entries(APP_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>}</Field>
              <Field label="When it happened" hint="Leave empty for today.">{(fid) => <Input id={fid} type="date" max={new Date().toISOString().slice(0, 10)} value={st.date} onChange={(e) => setSt({ ...st, date: e.target.value })} />}</Field>
              <Field label="Note">{(fid) => <Input id={fid} value={st.note} onChange={(e) => setSt({ ...st, note: e.target.value })} />}</Field>
              <Button type="submit" variant="primary">Add to history</Button>
            </form>
          </section>

          <section className="rounded-lg border border-rule bg-panel p-5">
            <h2 className="font-semibold">History</h2>
            <ol className="mt-3 space-y-3 border-l border-rule-strong pl-4">
              {[...a.events].reverse().map((e) => (
                <li key={e.id} className="text-sm">
                  <p className="font-medium">{APP_STATUS_LABEL[e.to] ?? e.to}</p>
                  <p className="text-xs text-muted">{fmtDate(e.occurred_at, { month: "short", day: "numeric", year: "numeric" })}</p>
                  {e.note && <p className="text-muted">{e.note}</p>}
                </li>
              ))}
            </ol>
          </section>

          <section className="rounded-lg border border-rule bg-panel p-5">
            <h2 className="font-semibold">Follow-up reminder</h2>
            {a.follow_up_at && <p className="mt-1 text-sm">{fmtDate(a.follow_up_at, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</p>}
            <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void run(() => api(`/api/applications/${a.id}`, { method: "PATCH", json: { follow_up_local: follow, tz: user.timezone } })); }}>
              <Input aria-label="Follow-up date and time" type="datetime-local" required value={follow} onChange={(e) => setFollow(e.target.value)} />
              <Button type="submit">Set</Button>
            </form>
            {a.follow_up_at && <Button variant="quiet" className="mt-2" onClick={() => run(() => api(`/api/applications/${a.id}`, { method: "PATCH", json: { clear_follow_up: true } }))}>Clear reminder</Button>}
          </section>

          <Button variant="danger" onClick={async () => { if (window.confirm("Remove this application and its history from your tracker?")) { await api(`/api/applications/${a.id}`, { method: "DELETE" }); router.push("/applications"); } }}>
            Remove from tracker
          </Button>
        </aside>
      </div>
    </article>
  );
}
