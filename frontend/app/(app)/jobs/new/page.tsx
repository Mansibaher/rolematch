"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Field, Input, Notice, PageHeader, Textarea } from "@/components/ui";
import { api } from "@/lib/api";

export default function NewJobPage() {
  const router = useRouter();
  const [f, setF] = useState({ url: "", title: "", employer: "", location: "", description: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const supported = /^(https?:\/\/)?(boards|job-boards)(\.eu)?\.greenhouse\.io\/|^(https?:\/\/)?jobs(\.eu)?\.lever\.co\//i.test(f.url.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ id: string }>("/api/jobs/manual", { method: "POST", json: { ...f, url: f.url || null } });
      router.push(`/jobs/${r.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Add a job manually">
        Paste a posting from anywhere. Links to Greenhouse or Lever postings can be fetched directly; for other sites RoleMatch never fetches the link, so paste the description and the link is kept for reference. Jobs you add are visible only to you.
      </PageHeader>
      <form onSubmit={submit} className="max-w-3xl space-y-4">
        <Field label="Posting link" hint={supported ? "Supported source: you can leave the fields below empty and RoleMatch will fetch the posting." : "Optional for pasted postings."}>
          {(id) => <Input id={id} type="url" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://…" />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Job title">{(id) => <Input id={id} value={f.title} required={!supported} onChange={(e) => setF({ ...f, title: e.target.value })} />}</Field>
          <Field label="Employer">{(id) => <Input id={id} value={f.employer} required={!supported} onChange={(e) => setF({ ...f, employer: e.target.value })} />}</Field>
          <Field label="Location">{(id) => <Input id={id} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} placeholder="City, ST or Remote - US" />}</Field>
        </div>
        <Field label="Job description" hint="Include the requirements and qualifications sections.">
          {(id) => <Textarea id={id} rows={14} required={!supported} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />}
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" variant="primary" busy={busy}>{supported && !f.description ? "Fetch and add posting" : "Add posting"}</Button>
      </form>
    </>
  );
}
