"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Button, Field, Input, Notice, PageHeader, Textarea } from "@/components/ui";
import { api } from "@/lib/api";

function Upload() {
  const router = useRouter();
  const params = useSearchParams();
  const resumeId = params.get("resume");
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (mode === "file" && !file) return setError("Choose a PDF or DOCX file.");
    if (mode === "file" && file && file.size > 5 * 1024 * 1024) return setError("The file is larger than the 5 MB limit.");
    if (mode === "paste" && text.trim().length < 40) return setError("Paste the full text of your resume.");
    const form = new FormData();
    if (mode === "file" && file) form.append("file", file);
    else form.append("pasted_text", text);
    if (label) form.append("label", label);
    if (resumeId) form.append("resume_id", resumeId);
    setBusy(true);
    try {
      const res = await api<{ version: { id: string } }>("/api/resumes/upload", { method: "POST", body: form });
      router.push(`/profile/${res.version.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title={resumeId ? "Upload a new version" : "Add your resume"}>
        RoleMatch reads your resume, shows you what it extracted, and lets you correct it before any matching happens. The original file is kept private to your account and can be deleted at any time.
      </PageHeader>
      <form onSubmit={submit} className="max-w-2xl space-y-5">
        <fieldset className="flex gap-2">
          <legend className="sr-only">Input method</legend>
          {(["file", "paste"] as const).map((m) => (
            <label key={m} className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm ${mode === m ? "border-pine bg-pine-wash" : "border-rule-strong bg-panel"}`}>
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} className="sr-only" />
              {m === "file" ? "Upload PDF or DOCX" : "Paste text"}
            </label>
          ))}
        </fieldset>

        {mode === "file" ? (
          <div
            className="rounded-lg border border-dashed border-rule-strong bg-panel px-6 py-10 text-center"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) setFile(f);
            }}
          >
            <p className="font-medium">{file ? file.name : "Drop your resume here"}</p>
            <p className="mt-1 text-sm text-muted">PDF or DOCX, up to 5 MB. Scanned image PDFs can't be read; paste the text instead.</p>
            <input ref={input} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="sr-only" id="resume-file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <Button type="button" className="mt-4" onClick={() => input.current?.click()}>
              {file ? "Choose a different file" : "Choose file"}
            </Button>
          </div>
        ) : (
          <Field label="Resume text" hint="Keep the section headings (Experience, Projects, Education, Skills) so items are grouped correctly.">
            {(id) => <Textarea id={id} rows={16} value={text} onChange={(e) => setText(e.target.value)} />}
          </Field>
        )}

        {!resumeId && (
          <Field label="Name for this resume" hint="Optional, e.g. “Backend resume”.">
            {(id) => <Input id={id} value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />}
          </Field>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" variant="primary" busy={busy}>
          {busy ? "Reading your resume" : "Read resume"}
        </Button>
      </form>
    </>
  );
}

export default function NewResumePage() {
  return (
    <Suspense>
      <Upload />
    </Suspense>
  );
}
