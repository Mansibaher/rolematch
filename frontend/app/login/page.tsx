"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Button, Field, Input, Notice } from "@/components/ui";
import { api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent, creds?: { email: string; password: string }) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = creds ?? { email, password };
      if (mode === "register" && !creds) {
        await api("/api/auth/register", { method: "POST", json: { ...body, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } });
        router.replace("/profile/new");
      } else {
        await api("/api/auth/login", { method: "POST", json: body });
        router.replace(next.startsWith("/") ? next : "/");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto grid min-h-screen max-w-5xl items-center gap-12 px-6 py-12 lg:grid-cols-[1.1fr_1fr]">
      <section>
        <p className="text-lg font-semibold">RoleMatch</p>
        <h1 className="mt-6 max-w-md text-[2.4rem] font-semibold leading-[1.1] tracking-[-0.02em]">
          See which jobs your resume actually supports.
        </h1>
        <p className="mt-4 max-w-md text-muted">
          Every match shows the requirement, the line of your resume that backs it, and what is missing or unclear. Scores describe documented fit, not your odds of an interview.
        </p>
        <figure className="mt-8 max-w-md rounded-lg border border-rule bg-panel p-4">
          <p className="text-sm font-medium">Experience with PostgreSQL or another relational database</p>
          <blockquote className="quote mt-2 border-l-2 border-pine pl-3 text-ink/90">
            Designed and shipped REST APIs in Python (FastAPI) backed by PostgreSQL for a shipment-tracking service
          </blockquote>
          <figcaption className="mt-2 text-xs text-muted">Example from the synthetic demo resume</figcaption>
        </figure>
      </section>

      <section className="rounded-lg border border-rule bg-panel p-6 sm:p-8">
        <div role="tablist" aria-label="Account" className="mb-6 flex gap-1 rounded-md bg-paper p-1 text-sm">
          {(["signin", "register"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} type="button" onClick={() => setMode(m)}
              className={`flex-1 rounded px-3 py-1.5 ${mode === m ? "bg-panel font-semibold shadow-[0_0_0_1px_var(--color-rule)]" : "text-muted"}`}>
              {m === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Password" hint={mode === "register" ? "At least 8 characters." : undefined}>
            {(id) => <Input id={id} type="password" minLength={8} autoComplete={mode === "register" ? "new-password" : "current-password"} required value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          {error && <Notice tone="error">{error}</Notice>}
          <Button type="submit" variant="primary" busy={busy} className="w-full">
            {mode === "signin" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <div className="mt-6 border-t border-rule pt-5">
          <p className="text-sm text-muted">Trying it out locally? The demo account uses a synthetic resume and fictional job postings.</p>
          <Button className="mt-3 w-full" busy={busy} onClick={(e) => submit(e as unknown as React.FormEvent, { email: "demo@rolematch.local", password: "demo-password-123" })}>
            Open the demo account
          </Button>
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
