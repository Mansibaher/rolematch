"use client";

import { useEffect, useState } from "react";
import { Button, Field, Input, Loading, Notice, PageHeader, Select, cx } from "@/components/ui";
import { api, ago } from "@/lib/api";

interface Run { id: string; started_at: string; status: string; complete_listing: boolean; jobs_seen: number; jobs_new: number; jobs_updated: number; jobs_closed: number; error_kind: string | null; error_detail: string | null }
interface Source {
  id: string; kind: string; identifier: string; employer_name: string; region: string; enabled: boolean; from_config: boolean; watching: boolean;
  health: { state: string; last_success_at: string | null; consecutive_failures: number; jobs: { active: number; closed: number }; recent_runs: Run[] };
}

const STATE: Record<string, { label: string; cls: string }> = {
  healthy: { label: "Up to date", cls: "text-pine" },
  stale: { label: "Not checked recently", cls: "text-partial" },
  degraded: { label: "Last check failed", cls: "text-partial" },
  failing: { label: "Repeatedly failing", cls: "text-danger" },
  never_checked: { label: "Not checked yet", cls: "text-muted" },
  static: { label: "Bundled demo data", cls: "text-muted" },
};
const KIND: Record<string, string> = { greenhouse: "Greenhouse board", lever: "Lever board", fixture: "Demo fixtures" };
const ERR: Record<string, string> = {
  not_found: "The board wasn't found. Check the identifier in the employer's careers URL.",
  timeout: "The source didn't respond in time.", server_error: "The source returned a server error.", rate_limited: "The source asked us to slow down.",
  connection_error: "Couldn't connect to the source.", forbidden: "The source refused access.", unexpected_schema: "The response wasn't in the documented format.",
};

export default function SourcesPage() {
  const [data, setData] = useState<{ coverage_note: string; sources: Source[] } | null>(null);
  const [form, setForm] = useState({ kind: "greenhouse", identifier: "", employer_name: "", region: "us" });
  const [msg, setMsg] = useState<{ tone: "ok" | "error" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setData(await api("/api/sources"));
  }
  useEffect(() => {
    load().catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      const r = await fn();
      const check = (r as { check?: { status: string; error_kind: string | null; jobs_seen: number } })?.check;
      if (check && check.status === "failed") setMsg({ tone: "warn", text: `Added, but the first check failed: ${ERR[check.error_kind ?? ""] ?? check.error_kind}` });
      else if (check) setMsg({ tone: "ok", text: `Added. Found ${check.jobs_seen} open jobs.` });
      else if (ok) setMsg({ tone: "ok", text: ok });
      await load();
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  if (!data) return msg ? <Notice tone="error">{msg.text}</Notice> : <Loading />;

  return (
    <>
      <PageHeader title="Companies & sources">{data.coverage_note}</PageHeader>
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}

      <section className="mb-8 rounded-lg border border-rule bg-panel p-5">
        <h2 className="font-semibold">Follow a company</h2>
        <p className="mt-1 max-w-prose text-sm text-muted">
          Use the identifier from the company's job board address: <span className="whitespace-nowrap">boards.greenhouse.io/<strong>identifier</strong></span> or <span className="whitespace-nowrap">jobs.lever.co/<strong>identifier</strong></span>.
        </p>
        <form className="mt-4 grid gap-3 sm:grid-cols-[160px_1fr_1fr_100px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); void act("add", () => api("/api/sources", { method: "POST", json: form })); }}>
          <Field label="Board type">{(id) => <Select id={id} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="greenhouse">Greenhouse</option><option value="lever">Lever</option></Select>}</Field>
          <Field label="Board identifier">{(id) => <Input id={id} required pattern="[A-Za-z0-9][A-Za-z0-9_\-]*" value={form.identifier} onChange={(e) => setForm({ ...form, identifier: e.target.value })} />}</Field>
          <Field label="Company name">{(id) => <Input id={id} required value={form.employer_name} onChange={(e) => setForm({ ...form, employer_name: e.target.value })} />}</Field>
          <Field label="Region">{(id) => <Select id={id} value={form.region} disabled={form.kind !== "lever"} onChange={(e) => setForm({ ...form, region: e.target.value })}><option value="us">US</option><option value="eu">EU</option></Select>}</Field>
          <Button type="submit" variant="primary" busy={busy === "add"}>Follow and check</Button>
        </form>
      </section>

      <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
        {data.sources.map((s) => {
          const st = STATE[s.health.state] ?? STATE.never_checked;
          const last = s.health.recent_runs[0];
          return (
            <li key={s.id} className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center">
              <div>
                <p className="font-semibold">{s.employer_name}</p>
                <p className="text-sm text-muted">{KIND[s.kind] ?? s.kind}{s.kind !== "fixture" && `: ${s.identifier}${s.region === "eu" ? " (EU)" : ""}`}{!s.enabled && ", disabled in watchlist"}</p>
              </div>
              <div className="text-sm">
                <p className={cx("font-medium", st.cls)}>{st.label}</p>
                <p className="text-muted">{s.health.jobs.active} open{s.health.jobs.closed ? `, ${s.health.jobs.closed} closed` : ""}{s.health.last_success_at && `, checked ${ago(s.health.last_success_at)}`}</p>
                {last?.status === "failed" && <p className="text-xs text-muted">{ERR[last.error_kind ?? ""] ?? last.error_kind}</p>}
                {last?.status === "partial" && <p className="text-xs text-muted">Last listing was incomplete, so no jobs were marked closed.</p>}
              </div>
              <div className="flex gap-2">
                {s.watching ? (
                  <>
                    {s.kind !== "fixture" && <Button busy={busy === `r${s.id}`} onClick={() => act(`r${s.id}`, () => api(`/api/sources/${s.id}/refresh`, { method: "POST" }), "Check started.")}>Check now</Button>}
                    <Button variant="quiet" onClick={() => act(`w${s.id}`, () => api(`/api/sources/${s.id}/watch`, { method: "DELETE" }), "Unfollowed.")}>Unfollow</Button>
                  </>
                ) : (
                  <Button onClick={() => act(`w${s.id}`, () => api(`/api/sources/${s.id}/watch`, { method: "POST" }), "Following.")}>Follow</Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
