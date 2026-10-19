"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, Empty, Field, Input, Loading, Notice, PageHeader, Select } from "@/components/ui";
import { api, APP_STATUS_LABEL, fmtDate } from "@/lib/api";

export interface Deadline { id: string; kind: string; title: string; due_local_display: string; overdue: boolean; completed_at: string | null; hours_left: number; entered_tz: string; entered_local_display: string; notes: string }
export interface App {
  id: string; job_id: string | null; status: string; employer: string; title: string; applied_at: string | null;
  external_url: string | null; notes: string; follow_up_at: string | null; created_at: string; source_listing_status: string;
  job_changed_since_application: boolean; events: { id: string; from: string | null; to: string; occurred_at: string; note: string }[];
  deadlines: Deadline[]; has_snapshot: boolean; resume_version?: { id: string; version_number: number; origin: string };
}

const ACTIVE = ["saved", "applied", "oa_received", "oa_completed", "recruiter_screen", "interview"];

export default function ApplicationsPage() {
  const [apps, setApps] = useState<App[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [n, setN] = useState({ employer: "", title: "", external_url: "" });

  async function load() {
    try {
      setApps((await api<{ applications: App[] }>("/api/applications")).applications);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);

  async function changeStatus(a: App, status: string) {
    try {
      await api(`/api/applications/${a.id}/status`, { method: "POST", json: { status } });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!apps) return error ? <Notice tone="error">{error}</Notice> : <Loading />;
  const groups: [string, App[]][] = [
    ["In progress", apps.filter((a) => ACTIVE.includes(a.status))],
    ["Closed", apps.filter((a) => !ACTIVE.includes(a.status))],
  ];

  return (
    <>
      <PageHeader title="Applications" actions={
        <>
          <a href="/api/calendar.ics" className="inline-flex items-center rounded-md border border-rule-strong bg-panel px-3.5 py-2 text-sm font-medium hover:border-ink">Export deadlines (.ics)</a>
          <Button onClick={() => setAdding((v) => !v)}>Track a job from elsewhere</Button>
        </>
      }>
        Your own record of where each application stands. It's separate from whether the employer still lists the job. Changing a status adds to the history; nothing is overwritten.
      </PageHeader>
      {error && <div className="mb-4"><Notice tone="error">{error}</Notice></div>}
      {adding && (
        <form className="mb-6 grid gap-3 rounded-lg border border-rule bg-panel p-5 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end"
          onSubmit={async (e) => { e.preventDefault(); await api("/api/applications", { method: "POST", json: { ...n, external_url: n.external_url || null } }); setAdding(false); setN({ employer: "", title: "", external_url: "" }); await load(); }}>
          <Field label="Employer">{(id) => <Input id={id} required value={n.employer} onChange={(e) => setN({ ...n, employer: e.target.value })} />}</Field>
          <Field label="Job title">{(id) => <Input id={id} required value={n.title} onChange={(e) => setN({ ...n, title: e.target.value })} />}</Field>
          <Field label="Link (optional)">{(id) => <Input id={id} type="url" value={n.external_url} onChange={(e) => setN({ ...n, external_url: e.target.value })} />}</Field>
          <Button type="submit" variant="primary">Add</Button>
        </form>
      )}
      {apps.length === 0 ? (
        <Empty title="Nothing tracked yet">Save a job from the Jobs page, or track one you found elsewhere.</Empty>
      ) : groups.map(([label, list]) => list.length > 0 && (
        <section key={label} className="mb-8">
          <h2 className="mb-2 text-lg font-semibold">{label} <span className="text-sm font-normal text-muted">({list.length})</span></h2>
          <div className="overflow-x-auto rounded-lg border border-rule bg-panel">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-paper text-left text-muted">
                <tr><th className="px-4 py-2 font-medium">Job</th><th className="px-4 py-2 font-medium">Status</th><th className="px-4 py-2 font-medium">Applied</th><th className="px-4 py-2 font-medium">Next deadline</th><th className="px-4 py-2 font-medium">Listing</th></tr>
              </thead>
              <tbody>
                {list.map((a) => {
                  const next = a.deadlines.find((d) => !d.completed_at);
                  return (
                    <tr key={a.id} className="border-t border-rule align-top">
                      <td className="px-4 py-3"><Link href={`/applications/${a.id}`} className="font-medium text-pine hover:underline">{a.title}</Link><span className="block text-muted">{a.employer}</span></td>
                      <td className="px-4 py-3">
                        <Select aria-label={`Status for ${a.title}`} value={a.status} onChange={(e) => changeStatus(a, e.target.value)} className="py-1">
                          {Object.entries(APP_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </Select>
                      </td>
                      <td className="px-4 py-3">{a.applied_at ? fmtDate(a.applied_at) : <span className="text-muted">Not yet</span>}{a.resume_version && <span className="block text-xs text-muted">Resume v{a.resume_version.version_number}</span>}</td>
                      <td className="px-4 py-3">{next ? <span className={next.overdue ? "text-danger" : ""}>{next.title}<span className="block text-xs text-muted">{next.due_local_display}</span></span> : <span className="text-muted">None</span>}</td>
                      <td className="px-4 py-3">{a.source_listing_status === "closed" ? "No longer listed" : a.source_listing_status === "active" ? "Listed" : "Not monitored"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}
