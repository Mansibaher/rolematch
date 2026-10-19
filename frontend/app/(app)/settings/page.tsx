"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useUser } from "@/components/shell";
import { Button, Field, Input, Notice, PageHeader, Select } from "@/components/ui";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const { user, refresh } = useUser();
  const router = useRouter();
  const [tz, setTz] = useState(user.timezone);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [del, setDel] = useState({ password: "", confirm: "" });
  const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [user.timezone];

  async function saveTz() {
    try {
      await api("/api/auth/me", { method: "PATCH", json: { timezone: tz } });
      await refresh();
      setMsg({ tone: "ok", text: "Time zone saved. Deadlines are now shown in this zone." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    }
  }

  return (
    <>
      <PageHeader title="Settings" />
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      <section className="mb-8 max-w-xl">
        <h2 className="text-lg font-semibold">Time zone</h2>
        <p className="mt-1 text-sm text-muted">Deadlines and reminders are displayed in this zone.</p>
        <div className="mt-3 flex gap-2">
          <Select aria-label="Time zone" value={tz} onChange={(e) => setTz(e.target.value)}>{zones.map((z) => <option key={z}>{z}</option>)}</Select>
          <Button onClick={saveTz}>Save</Button>
        </div>
      </section>

      <section className="mb-8 max-w-2xl">
        <h2 className="text-lg font-semibold">External AI assistance</h2>
        <p className="mt-1 text-sm text-muted">
          RoleMatch works entirely without external AI services: extraction, matching and tailoring suggestions run on this server. An optional provider can be configured by the server operator; it stays off unless you opt in here, and only the text needed for a request (never your contact details or work-authorization answers) would be sent.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={user.llm_opt_in} onChange={async (e) => {
            try {
              await api("/api/auth/me", { method: "PATCH", json: { llm_opt_in: e.target.checked } });
              await refresh();
            } catch (err) {
              setMsg({ tone: "error", text: (err as Error).message });
            }
          }} />
          Allow the optional external AI provider
        </label>
      </section>

      <section className="max-w-xl rounded-lg border border-danger/40 bg-panel p-5">
        <h2 className="text-lg font-semibold">Delete account and data</h2>
        <p className="mt-1 text-sm text-muted">Permanently deletes your uploaded files, resume versions, profile, preferences, work-authorization answers, manually added jobs and application history. This can't be undone.</p>
        <form className="mt-4 space-y-3" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api("/api/account/delete", { method: "POST", json: del });
            router.replace("/login");
          } catch (err) {
            setMsg({ tone: "error", text: (err as Error).message });
          }
        }}>
          <Field label="Password">{(id) => <Input id={id} type="password" required autoComplete="current-password" value={del.password} onChange={(e) => setDel({ ...del, password: e.target.value })} />}</Field>
          <Field label='Type "DELETE" to confirm'>{(id) => <Input id={id} required value={del.confirm} onChange={(e) => setDel({ ...del, confirm: e.target.value })} />}</Field>
          <Button type="submit" variant="danger" disabled={del.confirm !== "DELETE"}>Delete everything</Button>
        </form>
      </section>
    </>
  );
}
