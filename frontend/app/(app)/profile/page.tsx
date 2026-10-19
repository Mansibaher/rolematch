"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, ButtonLink, Empty, Loading, Notice, PageHeader } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";

interface Version {
  id: string;
  version_number: number;
  origin: string;
  created_at: string;
  reviewed_at: string | null;
  extraction_status: string;
  evidence_eligible: boolean;
  tailored_for_job_id: string | null;
}
interface Listing {
  active_version_id: string | null;
  resumes: { id: string; label: string; created_at: string; versions: Version[] }[];
}

const ORIGIN: Record<string, string> = { upload: "Uploaded file", pasted: "Pasted text", tailored: "Tailored copy" };

export default function ProfileIndex() {
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setData(await api<Listing>("/api/resumes"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);

  async function remove(id: string, label: string) {
    if (!window.confirm(`Delete “${label}”, all its versions and the uploaded files? Applications keep their records but lose the link to the resume version.`)) return;
    await api(`/api/resumes/${id}`, { method: "DELETE" });
    await load();
  }

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;

  return (
    <>
      <PageHeader title="Resume profile" actions={<ButtonLink href="/profile/new" variant="primary">Add a resume</ButtonLink>}>
        Matching uses one reviewed version at a time. Tailored copies are kept for your records but never count as evidence.
      </PageHeader>
      {data.resumes.length === 0 ? (
        <Empty title="No resume yet" action={<ButtonLink href="/profile/new" variant="primary">Add your resume</ButtonLink>}>
          Upload a PDF or DOCX, or paste the text. You'll review everything RoleMatch extracts before it is used.
        </Empty>
      ) : (
        <div className="space-y-8">
          {data.resumes.map((r) => (
            <section key={r.id}>
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="text-lg font-semibold">{r.label}</h2>
                <div className="flex gap-2">
                  <ButtonLink href={`/profile/new?resume=${r.id}`}>Upload new version</ButtonLink>
                  <Button variant="danger" onClick={() => remove(r.id, r.label)}>Delete</Button>
                </div>
              </div>
              <table className="w-full border-collapse overflow-hidden rounded-lg border border-rule bg-panel text-sm">
                <thead className="bg-paper text-left text-muted">
                  <tr>
                    <th className="px-4 py-2 font-medium">Version</th>
                    <th className="px-4 py-2 font-medium">Source</th>
                    <th className="px-4 py-2 font-medium">Created</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...r.versions].reverse().map((v) => (
                    <tr key={v.id} className="border-t border-rule">
                      <td className="px-4 py-2.5">
                        <Link href={`/profile/${v.id}`} className="font-medium text-pine hover:underline">Version {v.version_number}</Link>
                      </td>
                      <td className="px-4 py-2.5">{ORIGIN[v.origin] ?? v.origin}</td>
                      <td className="px-4 py-2.5">{fmtDate(v.created_at, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}</td>
                      <td className="px-4 py-2.5">
                        {v.id === data.active_version_id ? (
                          <span className="rounded bg-pine-wash px-2 py-0.5 text-pine-deep">Used for matching</span>
                        ) : v.extraction_status === "needs_text" ? (
                          <span className="text-partial">Couldn't read text</span>
                        ) : v.reviewed_at ? "Reviewed" : v.evidence_eligible ? "Not reviewed" : "Not used for matching"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
