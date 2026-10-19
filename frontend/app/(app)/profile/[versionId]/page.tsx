"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Field, Input, Loading, Notice, PageHeader, Select, cx } from "@/components/ui";
import { api } from "@/lib/api";

interface Bullet { id: string; text: string; start: number | null; end: number | null; user_added?: boolean }
interface Entry {
  id: string; kind: string; title: string; organization: string; location: string; employment_type: string;
  start_date: string | null; end_date: string | null; is_current: boolean; date_precision: string; date_text: string;
  degree: string; field_of_study: string; bullets: Bullet[];
  evidence: { start?: number; end?: number; review_flags?: string[]; section?: string };
  source: string; user_edited: boolean; excluded: boolean; extracted: Record<string, unknown> | null;
}
interface Skill {
  id: string; canonical: string; display_name: string; category: string; source: string; removed: boolean;
  evidence: { start: number; end: number; text: string; context: string }[]; contexts: string[]; user_note: string;
}
interface Group { min_months: number; point_months: number; max_months: number; overlap_months_removed: number; entries: number }
interface Profile {
  version: { id: string; resume_id: string; version_number: number; origin: string; reviewed_at: string | null; extraction_status: string; evidence_eligible: boolean };
  raw_text: string; warnings: string[]; instruction_like_text: { start: number; end: number; text: string }[];
  entries: Entry[]; skills: Skill[];
  experience: { groups: Record<string, Group>; uncertain: boolean; notes: string[] };
}

const KIND_LABEL: Record<string, string> = { employment: "Work experience", project: "Projects", education: "Education", activity: "Activities & leadership" };
const TYPE_LABEL: Record<string, string> = { full_time: "Full-time", internship: "Internship", part_time: "Part-time", research: "Research role", contract: "Contract", unknown: "Type not stated" };

function years(m: number) {
  return (m / 12).toFixed(1);
}
function range(g: Group) {
  return Math.abs(g.max_months - g.min_months) < 0.6 ? `${years(g.point_months)} years` : `${years(g.min_months)}–${years(g.max_months)} years`;
}

export default function ReviewPage() {
  const { versionId } = useParams<{ versionId: string }>();
  const router = useRouter();
  const [p, setP] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hl, setHl] = useState<{ start: number; end: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);
  const base = `/api/resumes/versions/${versionId}`;

  const load = useCallback(async () => {
    try {
      setP(await api<Profile>(base));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [base]);
  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    textRef.current?.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [hl]);

  async function mutate(path: string, method: string, json?: unknown) {
    setError(null);
    try {
      setP(await api<Profile>(`${base}${path}`, { method, json }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function review() {
    setBusy(true);
    try {
      await api(`${base}/review`, { method: "POST" });
      const prefs = await api<{ role_families: string[]; locations: string[] }>("/api/preferences");
      router.push(prefs.role_families.length || prefs.locations.length ? "/jobs" : "/preferences?onboarding=1");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const grouped = useMemo(() => {
    const g: Record<string, Entry[]> = {};
    for (const e of p?.entries ?? []) (g[e.kind] ??= []).push(e);
    return g;
  }, [p]);

  if (error && !p) return <Notice tone="error">{error}</Notice>;
  if (!p) return <Loading label="Loading profile" />;

  const readOnly = !p.version.evidence_eligible;
  const exp = p.experience.groups;

  return (
    <>
      <PageHeader
        title={`Review version ${p.version.version_number}`}
        actions={
          !readOnly && p.version.extraction_status !== "needs_text" ? (
            <Button variant="primary" busy={busy} onClick={review}>
              {p.version.reviewed_at ? "Use this version for matching" : "Confirm profile and continue"}
            </Button>
          ) : undefined
        }
      >
        Check what was extracted. Fix titles, dates or item types, remove skills that are wrong, and add ones the resume doesn't show. Your corrections are logged next to the original extraction.
      </PageHeader>

      <div className="mb-6 space-y-3">
        {readOnly && <Notice tone="info" title="Tailored copy">This version was produced by the tailoring assistant. It is kept for your records and is never used as matching evidence.</Notice>}
        {p.version.extraction_status === "needs_text" && (
          <Notice tone="warn" title="No readable text">
            {p.warnings[0] ?? "This file has no selectable text."}{" "}
            <a className="text-pine underline" href={`/profile/new?resume=${p.version.resume_id}`}>Paste the text as a new version</a>.
          </Notice>
        )}
        {p.warnings.filter((w) => !w.startsWith("The PDF")).map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
        {p.instruction_like_text.length > 0 && (
          <Notice tone="warn" title="Instruction-like text found">
            Passages such as “{p.instruction_like_text[0].text}” are treated as plain text and have no effect on matching.
          </Notice>
        )}
        {error && <Notice tone="error">{error}</Notice>}
      </div>

      {p.version.extraction_status !== "needs_text" && (
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="space-y-10">
            <section aria-labelledby="exp-h" className="rounded-lg border border-rule bg-panel p-5">
              <h2 id="exp-h" className="text-base font-semibold">Professional experience</h2>
              {exp.all_professional.entries === 0 ? (
                <p className="mt-2 text-sm text-muted">No work-experience entries. Projects and education are not counted as professional experience.</p>
              ) : (
                <dl className="mt-3 grid gap-4 text-sm sm:grid-cols-3">
                  <div><dt className="text-muted">Excluding internships</dt><dd className="text-lg font-semibold">{range(exp.excluding_internships)}</dd></div>
                  <div><dt className="text-muted">Including internships</dt><dd className="text-lg font-semibold">{range(exp.all_professional)}</dd></div>
                  <div><dt className="text-muted">Overlap not double-counted</dt><dd className="text-lg font-semibold">{years(exp.all_professional.overlap_months_removed)} years</dd></div>
                </dl>
              )}
              {p.experience.notes.map((n) => <p key={n} className="mt-2 text-xs text-muted">{n}</p>)}
            </section>

            {(["employment", "project", "education", "activity"] as const).map((kind) => (
              <section key={kind} aria-labelledby={`h-${kind}`}>
                <h2 id={`h-${kind}`} className="mb-3 text-lg font-semibold">{KIND_LABEL[kind]}</h2>
                {(grouped[kind] ?? []).length === 0 && <p className="text-sm text-muted">Nothing extracted.</p>}
                <ul className="divide-y divide-rule rounded-lg border border-rule bg-panel">
                  {(grouped[kind] ?? []).map((e) => (
                    <EntryRow key={e.id} e={e} readOnly={readOnly} onHighlight={setHl}
                      onSave={(changes) => mutate(`/entries/${e.id}`, "PATCH", changes)} />
                  ))}
                </ul>
              </section>
            ))}
            {!readOnly && <AddEntry onAdd={(body) => mutate("/entries", "POST", body)} />}

            <SkillsSection skills={p.skills} readOnly={readOnly} onHighlight={setHl}
              onToggle={(s) => mutate(`/skills/${s.id}`, "PATCH", { removed: !s.removed })}
              onAdd={(name, note) => mutate("/skills", "POST", { name, note })} />
          </div>

          <aside aria-label="Resume text" className="xl:sticky xl:top-6 xl:self-start">
            <div className="rounded-lg border border-rule bg-panel">
              <p className="border-b border-rule px-4 py-2.5 text-sm font-semibold">Source text</p>
              <div ref={textRef} className="max-h-[70vh] overflow-auto whitespace-pre-wrap px-4 py-3 font-[family-name:var(--font-quote)] text-[0.86rem] leading-relaxed">
                {hl ? (
                  <>
                    {p.raw_text.slice(0, hl.start)}
                    <mark className="rounded bg-pine-wash px-0.5 text-ink outline outline-1 outline-pine">{p.raw_text.slice(hl.start, hl.end)}</mark>
                    {p.raw_text.slice(hl.end)}
                  </>
                ) : p.raw_text}
              </div>
              <p className="border-t border-rule px-4 py-2 text-xs text-muted">Select an item or skill to see where it came from.</p>
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

function EntryRow({ e, readOnly, onSave, onHighlight }: { e: Entry; readOnly: boolean; onSave: (c: Partial<Entry>) => Promise<void>; onHighlight: (h: { start: number; end: number } | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<Entry>>({});
  const flags = e.evidence.review_flags ?? [];
  const dates = e.date_text || [e.start_date, e.is_current ? "present" : e.end_date].filter(Boolean).join(" – ") || "No dates";
  const set = (k: keyof Entry, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));
  const val = <K extends keyof Entry>(k: K) => (k in draft ? draft[k] : e[k]) as Entry[K];

  return (
    <li className={cx("px-4 py-4", e.excluded && "opacity-60")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button type="button" className="text-left" onClick={() => e.evidence.start != null && onHighlight({ start: e.evidence.start!, end: e.evidence.end! })}>
          <span className="block font-semibold">{e.title || <span className="text-muted">Untitled</span>}</span>
          <span className="block text-sm">{[e.organization, e.location].filter(Boolean).join(", ")}</span>
          <span className="block text-sm text-muted">
            {dates}
            {e.kind === "employment" && `, ${TYPE_LABEL[e.employment_type] ?? e.employment_type}`}
            {e.date_precision === "year" && ", year-only dates"}
          </span>
        </button>
        <div className="flex flex-wrap gap-2 text-xs">
          {e.source === "user_added" && <span className="rounded bg-pine-wash px-2 py-0.5">Added by you</span>}
          {e.user_edited && e.source !== "user_added" && <span className="rounded bg-paper px-2 py-0.5" title={`Original: ${JSON.stringify(e.extracted?.kind ?? "")}`}>Corrected by you</span>}
          {!readOnly && (
            <>
              <Button variant="quiet" onClick={() => { setDraft({}); setEditing((v) => !v); }}>{editing ? "Cancel" : "Edit"}</Button>
              <Button variant="quiet" onClick={() => onSave({ excluded: !e.excluded })}>{e.excluded ? "Include in matching" : "Exclude from matching"}</Button>
            </>
          )}
        </div>
      </div>
      {flags.map((f) => <p key={f} className="mt-2 rounded bg-partial-wash px-2 py-1 text-xs">{f}</p>)}
      {editing && (
        <form className="mt-4 grid gap-3 rounded-md border border-rule bg-paper p-4 sm:grid-cols-2"
          onSubmit={async (ev) => { ev.preventDefault(); await onSave(draft); setEditing(false); }}>
          <Field label="Item type">{(id) => (
            <Select id={id} value={val("kind")} onChange={(x) => set("kind", x.target.value)}>
              <option value="employment">Work experience (counts as professional experience)</option>
              <option value="project">Project</option>
              <option value="education">Education</option>
              <option value="activity">Activity / leadership</option>
            </Select>)}</Field>
          {val("kind") === "employment" && (
            <Field label="Employment type">{(id) => (
              <Select id={id} value={val("employment_type")} onChange={(x) => set("employment_type", x.target.value)}>
                {Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>)}</Field>
          )}
          <Field label={val("kind") === "education" ? "Degree" : "Title"}>{(id) => <Input id={id} value={val("title")} onChange={(x) => set("title", x.target.value)} />}</Field>
          <Field label="Organization">{(id) => <Input id={id} value={val("organization")} onChange={(x) => set("organization", x.target.value)} />}</Field>
          <Field label="Start date">{(id) => <Input id={id} type="date" value={val("start_date") ?? ""} onChange={(x) => set("start_date", x.target.value || null)} />}</Field>
          <Field label="End date">{(id) => <Input id={id} type="date" disabled={!!val("is_current")} value={val("end_date") ?? ""} onChange={(x) => set("end_date", x.target.value || null)} />}</Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!val("is_current")} onChange={(x) => set("is_current", x.target.checked)} /> I currently do this</label>
          <Field label="Date precision" hint="Year-only dates are reported as a range.">{(id) => (
            <Select id={id} value={val("date_precision")} onChange={(x) => set("date_precision", x.target.value)}>
              <option value="month">Month</option><option value="year">Year only</option><option value="unknown">Unknown</option>
            </Select>)}</Field>
          <div className="sm:col-span-2"><Button type="submit" variant="primary">Save correction</Button></div>
        </form>
      )}
      {e.bullets.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {e.bullets.map((b) => (
            <li key={b.id}>
              <button type="button" disabled={b.start == null} onClick={() => b.start != null && onHighlight({ start: b.start, end: b.end! })}
                className="quote text-left text-ink/85 hover:text-ink">
                {b.text}
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function AddEntry({ onAdd }: { onAdd: (b: Record<string, unknown>) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [b, setB] = useState({ kind: "employment", title: "", organization: "", start_date: "", end_date: "", is_current: false, bullets: "" });
  if (!open) return <Button onClick={() => setOpen(true)}>Add a missing item</Button>;
  return (
    <form className="grid gap-3 rounded-lg border border-rule bg-panel p-5 sm:grid-cols-2"
      onSubmit={async (e) => {
        e.preventDefault();
        await onAdd({ ...b, start_date: b.start_date || null, end_date: b.end_date || null, bullets: b.bullets.split("\n").filter(Boolean) });
        setOpen(false);
      }}>
      <p className="text-sm text-muted sm:col-span-2">Items you add are labelled as added by you, so they are never presented as text from your resume.</p>
      <Field label="Item type">{(id) => (
        <Select id={id} value={b.kind} onChange={(e) => setB({ ...b, kind: e.target.value })}>
          <option value="employment">Work experience</option><option value="project">Project</option><option value="education">Education</option><option value="activity">Activity</option>
        </Select>)}</Field>
      <Field label="Title">{(id) => <Input id={id} required value={b.title} onChange={(e) => setB({ ...b, title: e.target.value })} />}</Field>
      <Field label="Organization">{(id) => <Input id={id} value={b.organization} onChange={(e) => setB({ ...b, organization: e.target.value })} />}</Field>
      <Field label="Start date">{(id) => <Input id={id} type="date" value={b.start_date} onChange={(e) => setB({ ...b, start_date: e.target.value })} />}</Field>
      <Field label="End date">{(id) => <Input id={id} type="date" value={b.end_date} disabled={b.is_current} onChange={(e) => setB({ ...b, end_date: e.target.value })} />}</Field>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={b.is_current} onChange={(e) => setB({ ...b, is_current: e.target.checked })} /> Current</label>
      <div className="sm:col-span-2">
        <Field label="What you did" hint="One point per line.">{(id) => <textarea id={id} rows={3} className="w-full rounded-md border border-rule-strong px-3 py-2 text-sm" value={b.bullets} onChange={(e) => setB({ ...b, bullets: e.target.value })} />}</Field>
      </div>
      <div className="flex gap-2 sm:col-span-2"><Button type="submit" variant="primary">Add item</Button><Button type="button" onClick={() => setOpen(false)}>Cancel</Button></div>
    </form>
  );
}

function SkillsSection({ skills, readOnly, onToggle, onAdd, onHighlight }: {
  skills: Skill[]; readOnly: boolean; onToggle: (s: Skill) => void; onAdd: (n: string, note: string) => Promise<void>; onHighlight: (h: { start: number; end: number } | null) => void;
}) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const groups: [string, (s: Skill) => boolean][] = [
    ["Shown in work experience", (s) => s.contexts.includes("employment")],
    ["Shown in projects", (s) => !s.contexts.includes("employment") && s.contexts.includes("project")],
    ["Listed without an example", (s) => !s.contexts.some((c) => ["employment", "project", "user_added"].includes(c))],
    ["Added by you", (s) => s.source === "user_added"],
  ];
  return (
    <section aria-labelledby="skills-h">
      <h2 id="skills-h" className="text-lg font-semibold">Skills</h2>
      <p className="mt-1 max-w-prose text-sm text-muted">Skills shown in work or projects count as stronger evidence than skills that are only listed. Removed skills stay visible here so you can restore them.</p>
      <div className="mt-4 space-y-5">
        {groups.map(([label, pred]) => {
          const items = skills.filter(pred);
          if (!items.length) return null;
          return (
            <div key={label}>
              <h3 className="mb-2 text-sm font-medium">{label}</h3>
              <ul className="flex flex-wrap gap-2">
                {items.map((s) => (
                  <li key={s.id} className={cx("flex items-center rounded-full border text-sm", s.removed ? "border-dashed border-rule-strong text-muted line-through" : "border-rule-strong bg-panel")}>
                    <button type="button" className="py-1 pl-3 pr-2" title={s.user_note || undefined}
                      onClick={() => s.evidence[0] && onHighlight({ start: s.evidence[0].start, end: s.evidence[0].end })}>
                      {s.display_name}
                    </button>
                    {!readOnly && (
                      <button type="button" onClick={() => onToggle(s)} aria-label={`${s.removed ? "Restore" : "Remove"} ${s.display_name}`}
                        className="border-l border-rule px-2 py-1 text-muted hover:text-ink">
                        {s.removed ? "restore" : "×"}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {!readOnly && (
        <form className="mt-6 grid max-w-xl gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
          onSubmit={async (e) => { e.preventDefault(); if (!name.trim()) return; await onAdd(name, note); setName(""); setNote(""); }}>
          <Field label="Add a skill you have">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Kubernetes" />}</Field>
          <Field label="Where you used it (optional)">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
          <Button type="submit">Add skill</Button>
        </form>
      )}
    </section>
  );
}
