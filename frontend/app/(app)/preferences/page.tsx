"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Button, Chip, Field, Input, Loading, Notice, PageHeader, Select } from "@/components/ui";
import { api } from "@/lib/api";

interface Prefs {
  role_families: string[]; locations: string[]; remote_modes: string[]; experience_levels: string[]; employment_types: string[];
  min_salary: number | null; salary_currency: string | null; excluded_employers: string[]; excluded_keywords: string[];
  hard_filters: Record<string, boolean>; always_hard: string[]; feedback_adjustments: Record<string, number>;
  eligibility: { needs_sponsorship: boolean | null; authorization_note: string; exclude_citizenship_required: boolean };
  options: { role_families: { id: string; name: string }[]; remote_modes: string[]; experience_levels: string[]; employment_types: string[] };
}

const LABELS: Record<string, string> = {
  remote: "Remote", hybrid: "Hybrid", onsite: "On-site", intern: "Internship level", entry: "Entry / new grad", mid: "Mid-level",
  senior: "Senior", staff: "Staff and above", full_time: "Full-time", part_time: "Part-time", contract: "Contract", internship: "Internship",
};

function toggle(list: string[], v: string) {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function HardSwitch({ name, prefs, set }: { name: string; prefs: Prefs; set: (p: Prefs) => void }) {
  const on = !!prefs.hard_filters[name];
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={on} onChange={(e) => set({ ...prefs, hard_filters: { ...prefs.hard_filters, [name]: e.target.checked } })} />
      <span>{on ? "Hide jobs that don't match" : "Rank lower, don't hide"}</span>
    </label>
  );
}

function Section({ title, children, hint, control }: { title: string; children: React.ReactNode; hint?: string; control?: React.ReactNode }) {
  return (
    <section className="grid gap-3 border-t border-rule py-6 md:grid-cols-[240px_1fr]">
      <div>
        <h2 className="font-semibold">{title}</h2>
        {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
        {control && <div className="mt-3">{control}</div>}
      </div>
      <div>{children}</div>
    </section>
  );
}

function ListInput({ values, onChange, placeholder, label }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; label: string }) {
  const [v, setV] = useState("");
  return (
    <div>
      <ul className="mb-2 flex flex-wrap gap-2">
        {values.map((x) => (
          <li key={x} className="flex items-center rounded-full border border-rule-strong bg-panel text-sm">
            <span className="py-1 pl-3 pr-2">{x}</span>
            <button type="button" aria-label={`Remove ${x}`} onClick={() => onChange(values.filter((y) => y !== x))} className="border-l border-rule px-2 py-1 text-muted hover:text-ink">×</button>
          </li>
        ))}
      </ul>
      <form className="flex max-w-md gap-2" onSubmit={(e) => { e.preventDefault(); if (v.trim() && !values.includes(v.trim())) onChange([...values, v.trim()]); setV(""); }}>
        <Input aria-label={label} value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} />
        <Button type="submit">Add</Button>
      </form>
    </div>
  );
}

function Editor() {
  const router = useRouter();
  const onboarding = useSearchParams().get("onboarding") === "1";
  const [p, setP] = useState<Prefs | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Prefs>("/api/preferences").then(setP).catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);

  if (!p) return msg ? <Notice tone="error">{msg.text}</Notice> : <Loading />;

  async function save(resetFeedback = false) {
    if (!p) return;
    setBusy(true);
    setMsg(null);
    try {
      const { always_hard: _a, options: _o, feedback_adjustments: _f, ...body } = p;
      const saved = await api<Prefs>("/api/preferences", { method: "PUT", json: { ...body, reset_feedback: resetFeedback } });
      setP(saved);
      setMsg({ tone: "ok", text: "Preferences saved. Rankings use them from now on." });
      if (onboarding) router.push("/jobs");
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const fb = Object.entries(p.feedback_adjustments).filter(([, v]) => v !== 0);

  return (
    <>
      <PageHeader title="Preferences" actions={<Button variant="primary" busy={busy} onClick={() => save()}>{onboarding ? "Save and see jobs" : "Save preferences"}</Button>}>
        Each filter can either hide non-matching jobs or only rank them lower. A job is hidden only when the posting clearly fails a filter; when the posting doesn't say, the job stays visible and is marked as uncertain.
      </PageHeader>
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}

      <Section title="Roles" hint="Matched against job titles." control={<HardSwitch name="role_family" prefs={p} set={setP} />}>
        <div className="flex flex-wrap gap-2">
          {p.options.role_families.map((f) => (
            <Chip key={f.id} selected={p.role_families.includes(f.id)} onClick={() => setP({ ...p, role_families: toggle(p.role_families, f.id) })}>{f.name}</Chip>
          ))}
        </div>
      </Section>

      <Section title="Locations" hint="Cities (“Seattle, WA”), states (“California”) or countries (“United States”). Countries are also used to check remote-eligibility restrictions." control={<HardSwitch name="location" prefs={p} set={setP} />}>
        <ListInput label="Add location" values={p.locations} onChange={(v) => setP({ ...p, locations: v })} placeholder="Los Angeles, CA" />
      </Section>

      <Section title="Work arrangement" control={<HardSwitch name="work_mode" prefs={p} set={setP} />}>
        <div className="flex flex-wrap gap-2">
          {p.options.remote_modes.map((m) => <Chip key={m} selected={p.remote_modes.includes(m)} onClick={() => setP({ ...p, remote_modes: toggle(p.remote_modes, m) })}>{LABELS[m]}</Chip>)}
        </div>
      </Section>

      <Section title="Level" hint="Compared with the level stated or implied by the posting." control={<HardSwitch name="experience_level" prefs={p} set={setP} />}>
        <div className="flex flex-wrap gap-2">
          {p.options.experience_levels.map((m) => <Chip key={m} selected={p.experience_levels.includes(m)} onClick={() => setP({ ...p, experience_levels: toggle(p.experience_levels, m) })}>{LABELS[m]}</Chip>)}
        </div>
      </Section>

      <Section title="Employment type" control={<HardSwitch name="employment_type" prefs={p} set={setP} />}>
        <div className="flex flex-wrap gap-2">
          {p.options.employment_types.map((m) => <Chip key={m} selected={p.employment_types.includes(m)} onClick={() => setP({ ...p, employment_types: toggle(p.employment_types, m) })}>{LABELS[m]}</Chip>)}
        </div>
      </Section>

      <Section title="Salary" hint="Only compared when a posting lists an annual salary. Postings without one are never hidden for it." control={<HardSwitch name="salary" prefs={p} set={setP} />}>
        <div className="grid max-w-md grid-cols-[1fr_120px] gap-3">
          <Field label="Minimum annual salary">{(id) => <Input id={id} type="number" min={0} step={1000} value={p.min_salary ?? ""} onChange={(e) => setP({ ...p, min_salary: e.target.value ? Number(e.target.value) : null })} />}</Field>
          <Field label="Currency">{(id) => (
            <Select id={id} value={p.salary_currency ?? "USD"} onChange={(e) => setP({ ...p, salary_currency: e.target.value })}>
              {["USD", "CAD", "EUR", "GBP", "INR"].map((c) => <option key={c}>{c}</option>)}
            </Select>)}</Field>
        </div>
      </Section>

      <Section title="Work authorization" hint="Private. Used only to check postings that explicitly mention sponsorship or citizenship. Never shown to anyone else or sent to any external service.">
        <div className="max-w-xl space-y-4">
          <Field label="Do you need employer visa sponsorship now or in the future?">{(id) => (
            <Select id={id} value={p.eligibility.needs_sponsorship === null ? "" : String(p.eligibility.needs_sponsorship)}
              onChange={(e) => setP({ ...p, eligibility: { ...p.eligibility, needs_sponsorship: e.target.value === "" ? null : e.target.value === "true" } })}>
              <option value="">Prefer not to say</option><option value="true">Yes</option><option value="false">No</option>
            </Select>)}</Field>
          {p.eligibility.needs_sponsorship && (
            <>
              <HardSwitch name="sponsorship" prefs={p} set={setP} />
              <p className="text-sm text-muted">Postings that say nothing about sponsorship are shown and marked “sponsorship not mentioned”. Only postings that state they can't sponsor are affected.</p>
            </>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={p.eligibility.exclude_citizenship_required}
              onChange={(e) => setP({ ...p, eligibility: { ...p.eligibility, exclude_citizenship_required: e.target.checked } })} />
            Hide postings that require citizenship or a security clearance
          </label>
          <Field label="Private note" hint="For your own reference only.">{(id) => <Input id={id} maxLength={500} value={p.eligibility.authorization_note} onChange={(e) => setP({ ...p, eligibility: { ...p.eligibility, authorization_note: e.target.value } })} />}</Field>
        </div>
      </Section>

      <Section title="Exclusions" hint="Always applied. Matching employers or keywords hide the job.">
        <div className="space-y-5">
          <div><h3 className="mb-2 text-sm font-medium">Employers</h3><ListInput label="Add employer" values={p.excluded_employers} onChange={(v) => setP({ ...p, excluded_employers: v })} placeholder="Employer name" /></div>
          <div><h3 className="mb-2 text-sm font-medium">Keywords</h3><ListInput label="Add keyword" values={p.excluded_keywords} onChange={(v) => setP({ ...p, excluded_keywords: v })} placeholder="e.g. clearance" /></div>
        </div>
      </Section>

      <Section title="Feedback adjustments" hint="Your “relevant / not relevant” votes nudge ranking within ±15 points of preference fit. They never change eligibility or evidence.">
        {fb.length === 0 ? <p className="text-sm text-muted">No feedback yet.</p> : (
          <div className="space-y-3">
            <ul className="text-sm">{fb.map(([k, v]) => <li key={k}>{k.replace("family:", "Role family: ").replace("employer:", "Employer: ")} <span className="text-muted">{v > 0 ? "+" : ""}{Math.round(v * 100)}</span></li>)}</ul>
            <Button onClick={() => save(true)}>Clear feedback adjustments</Button>
          </div>
        )}
      </Section>
    </>
  );
}

export default function PreferencesPage() {
  return <Suspense><Editor /></Suspense>;
}
