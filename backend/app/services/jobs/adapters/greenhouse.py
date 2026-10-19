"""Greenhouse Job Board API adapter.

Documentation: https://developers.greenhouse.io/job-board.html (verified 2026-10-19)
- List:   GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
          -> {"jobs": [...], "meta": {"total": N}}. No authentication for GET; no documented pagination.
- Single: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id}?pay_transparency=true
Coverage: one employer per board token. RoleMatch only queries boards listed in the watchlist.
Dates:    `updated_at` is a last-modified time, NOT a posting date. `first_published` (when present) is used as
          the posting date; otherwise the posting date is unknown and only RoleMatch's first-seen time is shown.
"""

from __future__ import annotations

from app.services.jobs.adapters.base import FetchResult, parse_iso
from app.services.jobs.http import Counter, SourceError, get_json, make_client
from app.services.jobs.normalize import NormalizedJob, Salary, finish, html_to_text
from app.services.url_safety import IDENT_RX, canonicalize_url

BASE = "https://boards-api.greenhouse.io/v1/boards"


def _validate(token: str) -> str:
    if not IDENT_RX.match(token or ""):
        raise SourceError("invalid_identifier", "board token may only contain letters, digits, '-' and '_'")
    return token


def _salary(job: dict) -> Salary | None:
    for r in job.get("pay_input_ranges") or []:
        try:
            lo, hi = float(r["min_cents"]) / 100, float(r["max_cents"]) / 100
        except (KeyError, TypeError, ValueError):
            continue
        cur = (r.get("currency_type") or "USD")[:3].upper()
        return Salary(lo, hi, cur, "year", f"Greenhouse pay range “{(r.get('title') or '').strip()[:60]}”")
    return None


def normalize(job: dict, employer_name: str) -> NormalizedJob:
    content_html = job.get("content") or ""
    text = html_to_text(content_html)
    posted = parse_iso(job.get("first_published"))
    location = ((job.get("location") or {}).get("name") or "").strip()
    offices = [o.get("name") for o in job.get("offices") or [] if o.get("name")]
    if not location and offices:
        location = "; ".join(offices)
    url = job.get("absolute_url")
    nj = NormalizedJob(
        source_job_id=str(job["id"]),
        employer=(job.get("company_name") or employer_name).strip()[:200],
        title=(job.get("title") or "").strip()[:300],
        description_text=text,
        description_html=content_html or None,
        department=", ".join(d.get("name", "") for d in job.get("departments") or [] if d.get("name")) or None,
        requisition_id=(str(job["requisition_id"]) if job.get("requisition_id") else None),
        location_raw=location,
        salary=_salary(job),
        posted_at=posted,
        posted_at_basis="Greenhouse first_published" if posted else None,
        source_updated_at=parse_iso(job.get("updated_at")),
        apply_url=url,
        canonical_url=canonicalize_url(url),
        raw_payload={k: v for k, v in job.items() if k not in ("content", "questions")},
    )
    return finish(nj)


class GreenhouseAdapter:
    kind = "greenhouse"

    def __init__(self, client=None):
        self._client = client

    def _c(self):
        return self._client or make_client()

    def fetch_all(self, identifier: str, employer_name: str, region: str = "us") -> FetchResult:
        token = _validate(identifier)
        counter = Counter()
        client = self._c()
        try:
            data = get_json(client, f"{BASE}/{token}/jobs", params={"content": "true"}, counter=counter)
        finally:
            if self._client is None:
                client.close()
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("unexpected_schema", "expected an object with a 'jobs' array")
        jobs: list[NormalizedJob] = []
        skipped = 0
        for j in data["jobs"]:
            if not isinstance(j, dict) or "id" not in j or not j.get("title"):
                skipped += 1
                continue
            jobs.append(normalize(j, employer_name))
        total = (data.get("meta") or {}).get("total")
        complete = skipped == 0 and (total is None or total == len(data["jobs"]))
        notes = []
        if skipped:
            notes.append(f"{skipped} malformed job objects skipped")
        if total is not None and total != len(data["jobs"]):
            notes.append(f"meta.total={total} but {len(data['jobs'])} jobs returned; closures not inferred this run")
        return FetchResult(jobs, complete, pages=1, counter=counter, notes=notes)

    def fetch_one(self, identifier: str, job_id: str, employer_name: str, region: str = "us") -> NormalizedJob:
        token = _validate(identifier)
        if not job_id.isdigit():
            raise SourceError("invalid_identifier", "Greenhouse job ids are numeric")
        client = self._c()
        try:
            data = get_json(client, f"{BASE}/{token}/jobs/{job_id}", params={"pay_transparency": "true"})
        finally:
            if self._client is None:
                client.close()
        if not isinstance(data, dict) or "id" not in data:
            raise SourceError("unexpected_schema", "expected a job object")
        return normalize(data, employer_name)
