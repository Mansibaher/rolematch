"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Loading, Notice, PageHeader, Textarea } from "@/components/ui";
import { api } from "@/lib/api";

interface Suggestion {
  id: string; entry_title: string; entry_kind: string; source_text: string; suggested_text: string; changes: string[];
  requirements: string[]; alternatives: { text: string; source: string }[];
}
interface Data {
  job: { id: string; title: string; employer: string };
  suggestions: Suggestion[];
  unsupported_requirements: { requirement: string; kind: string; status: string }[];
  note: string;
  llm_note: string | null;
}

export default function TailorPage() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<Data | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<Record<string, string[]>>({});
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Data>(`/api/tailoring/${id}`).then(setD).catch((e) => setMsg({ tone: "error", text: e.message }));
  }, [id]);

  if (!d) return msg ? <Notice tone="error">{msg.text}</Notice> : <Loading label="Finding relevant lines in your profile" />;

  async function checkEdit(s: Suggestion, text: string) {
    const r = await api<{ ok: boolean; problems: string[] }>("/api/tailoring/check", { method: "POST", json: { source_text: s.source_text, text } });
    setProblems((p) => ({ ...p, [s.id]: r.problems }));
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const accepted = Object.entries(picked).map(([sid, text]) => ({ id: sid, text }));
      const r = await api<{ version_number: number; note: string }>(`/api/tailoring/${id}/save`, { method: "POST", json: { accepted } });
      setMsg({ tone: "ok", text: `Saved as version ${r.version_number}. ${r.note}` });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const count = Object.keys(picked).length;
  const blocked = Object.keys(picked).some((k) => (problems[k] ?? []).length > 0);

  return (
    <>
      <nav className="mb-4 text-sm"><Link href={`/jobs/${id}`} className="text-pine hover:underline">{d.job.title}, {d.job.employer}</Link></nav>
      <PageHeader title="Tailor resume wording" actions={<Button variant="primary" busy={busy} disabled={!count || blocked} onClick={save}>Save {count || ""} accepted line{count === 1 ? "" : "s"} as a new version</Button>}>
        {d.note}
      </PageHeader>
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      {d.llm_note && <div className="mb-4"><Notice>{d.llm_note}</Notice></div>}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <ul className="space-y-4">
          {d.suggestions.length === 0 && <li className="text-sm text-muted">No lines in your verified profile relate to this posting's requirements.</li>}
          {d.suggestions.map((s) => {
            const on = s.id in picked;
            return (
              <li key={s.id} className="rounded-lg border border-rule bg-panel p-5">
                <p className="text-xs text-muted">{s.entry_title} ({s.entry_kind})</p>
                <p className="mt-2 text-sm font-medium">Your resume says</p>
                <p className="quote border-l-2 border-rule-strong pl-3">{s.source_text}</p>
                <p className="mt-3 text-sm font-medium">Suggested</p>
                {on ? (
                  <Textarea aria-label="Edit suggested wording" rows={3} value={picked[s.id]}
                    onChange={(e) => setPicked({ ...picked, [s.id]: e.target.value })} onBlur={(e) => void checkEdit(s, e.target.value)} />
                ) : (
                  <p className="border-l-2 border-pine pl-3">{s.suggested_text}</p>
                )}
                {(problems[s.id] ?? []).map((p) => <p key={p} className="mt-1 text-sm text-danger">This edit {p}.</p>)}
                <ul className="mt-2 list-disc pl-5 text-sm text-muted">{s.changes.map((c) => <li key={c}>{c}</li>)}</ul>
                {s.requirements.length > 0 && <p className="mt-2 text-sm"><span className="text-muted">Supports: </span>{s.requirements.join("; ")}</p>}
                {s.alternatives.map((a) => (
                  <div key={a.text} className="mt-3 rounded-md bg-paper p-3 text-sm">
                    <p className="text-xs text-muted">Alternative from {a.source}, checked against the source line</p>
                    <p>{a.text}</p>
                    <Button variant="quiet" onClick={() => setPicked({ ...picked, [s.id]: a.text })}>Use this wording</Button>
                  </div>
                ))}
                <div className="mt-3">
                  <Button onClick={() => {
                    const next = { ...picked };
                    if (on) delete next[s.id]; else next[s.id] = s.suggested_text;
                    setPicked(next);
                  }}>{on ? "Don't include" : "Accept"}</Button>
                </div>
              </li>
            );
          })}
        </ul>
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <section className="rounded-lg border border-rule bg-panel p-5">
            <h2 className="font-semibold">Can't be supported from your profile</h2>
            <p className="mt-1 text-sm text-muted">Tailoring won't add these. If you do have the experience, add it to your profile first.</p>
            {d.unsupported_requirements.length === 0 ? <p className="mt-3 text-sm">None.</p> : (
              <ul className="mt-3 space-y-2 text-sm">{d.unsupported_requirements.map((u) => <li key={u.requirement}>{u.requirement}<span className="block text-xs text-muted">{u.kind}, {u.status === "unclear" ? "unclear" : "not shown"}</span></li>)}</ul>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}
