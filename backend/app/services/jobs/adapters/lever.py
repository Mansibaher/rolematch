"""Lever Postings API adapter.

Documentation: https://github.com/lever/postings-api (README). In this build environment the official README could
not be fetched directly; the endpoint shape was cross-checked against a secondary source (100hires.com Lever API
guide, 2026) and the field names below follow the official README as of the author's knowledge. Re-verify before
relying on new fields.
- List:   GET https://api.lever.co/v0/postings/{site}?mode=json&skip={n}&limit={k}  (EU: api.eu.lever.co)
          -> JSON array of postings. No authentication for published postings.
- Single: GET https://api.lever.co/v0/postings/{site}/{posting_id}
Coverage: one employer per site name. Only sites in the watchlist are queried.
Dates:    `createdAt` (epoch ms) is when the posting was created in Lever, which can precede publication.
          It is shown as the posting date with that caveat.
Known quirk: the endpoint can intermittently return HTML; the HTTP client retries on non-JSON responses.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.services.jobs.adapters.base import FetchResult, parse_epoch_ms
from app.services.jobs.http import Counter, SourceError, get_json, make_client
from app.services.jobs.normalize import NormalizedJob, Salary, finish, html_to_text
from app.services.url_safety import IDENT_RX, JOB_ID_RX, canonicalize_url

PAGE = 100


def _base(region: str) -> str:
    return "https://api.eu.lever.co/v0/postings" if region == "eu" else "https://api.lever.co/v0/postings"


def _salary(p: dict) -> Salary | None:
    sr = p.get("salaryRange")
    if not isinstance(sr, dict) or sr.get("min") is None or sr.get("max") is None:
        return None
    interval_raw = str(sr.get("interval") or "").lower()
    interval = "hour" if "hour" in interval_raw else "month" if "month" in interval_raw else "year" if "year" in interval_raw else None
    if interval is None:
        return None
    try:
        return Salary(float(sr["min"]), float(sr["max"]), str(sr.get("currency") or "USD")[:3].upper(), interval, "Lever salaryRange")
    except (TypeError, ValueError):
        return None


def _description(p: dict) -> tuple[str, str]:
    parts_text: list[str] = []
    parts_html: list[str] = []
    if p.get("description"):
        parts_html.append(p["description"])
    parts_text.append(p.get("descriptionPlain") or html_to_text(p.get("description")))
    for lst in p.get("lists") or []:
        heading = (lst.get("text") or "").strip()
        body = html_to_text("<ul>" + (lst.get("content") or "") + "</ul>")
        parts_text.append(f"{heading}\n{body}" if heading else body)
        parts_html.append(f"<h3>{heading}</h3><ul>{lst.get('content') or ''}</ul>")
    if p.get("additionalPlain") or p.get("additional"):
        parts_text.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
        parts_html.append(p.get("additional") or "")
    return "\n\n".join(t.strip() for t in parts_text if t and t.strip()), "\n".join(parts_html)


def normalize(p: dict, employer_name: str) -> NormalizedJob:
    cats = p.get("categories") or {}
    all_locs = cats.get("allLocations") or []
    location = "; ".join(all_locs) if all_locs else (cats.get("location") or "")
    text, html = _description(p)
    created = parse_epoch_ms(p.get("createdAt"))
    url = p.get("hostedUrl") or p.get("applyUrl")
    nj = NormalizedJob(
        source_job_id=str(p["id"]),
        employer=employer_name[:200],
        title=(p.get("text") or "").strip()[:300],
        description_text=text,
        description_html=html or None,
        department=cats.get("department") or cats.get("team"),
        location_raw=location,
        salary=_salary(p),
        posted_at=created,
        posted_at_basis="Lever createdAt (posting creation; may precede publication)" if created else None,
        apply_url=p.get("applyUrl") or p.get("hostedUrl"),
        canonical_url=canonicalize_url(url),
        raw_payload={k: v for k, v in p.items() if k not in ("description", "descriptionPlain", "lists", "additional", "additionalPlain")},
    )
    return finish(nj, structured_workplace=p.get("workplaceType") if p.get("workplaceType") != "unspecified" else None,
                  structured_commitment=cats.get("commitment"))


class LeverAdapter:
    kind = "lever"

    def __init__(self, client=None):
        self._client = client

    def _c(self):
        return self._client or make_client()

    def fetch_all(self, identifier: str, employer_name: str, region: str = "us") -> FetchResult:
        if not IDENT_RX.match(identifier or ""):
            raise SourceError("invalid_identifier", "site name may only contain letters, digits, '-' and '_'")
        counter = Counter()
        client = self._c()
        jobs: list[NormalizedJob] = []
        seen: set[str] = set()
        pages = 0
        complete = False
        notes: list[str] = []
        try:
            for page in range(get_settings().lever_max_pages):
                data = get_json(client, f"{_base(region)}/{identifier}", params={"mode": "json", "skip": page * PAGE, "limit": PAGE}, counter=counter)
                pages += 1
                if not isinstance(data, list):
                    raise SourceError("unexpected_schema", "expected a JSON array of postings")
                for p in data:
                    if isinstance(p, dict) and p.get("id") and p.get("text") and p["id"] not in seen:
                        seen.add(p["id"])
                        jobs.append(normalize(p, employer_name))
                if len(data) < PAGE:
                    complete = True
                    break
            else:
                notes.append(f"stopped after {pages} pages (lever_max_pages); closures not inferred this run")
        finally:
            if self._client is None:
                client.close()
        return FetchResult(jobs, complete, pages=pages, counter=counter, notes=notes)

    def fetch_one(self, identifier: str, job_id: str, employer_name: str, region: str = "us") -> NormalizedJob:
        if not IDENT_RX.match(identifier or "") or not JOB_ID_RX.match(job_id or ""):
            raise SourceError("invalid_identifier", "invalid Lever site or posting id")
        client = self._c()
        try:
            data = get_json(client, f"{_base(region)}/{identifier}/{job_id}")
        finally:
            if self._client is None:
                client.close()
        if not isinstance(data, dict) or "id" not in data:
            raise SourceError("unexpected_schema", "expected a posting object")
        return normalize(data, employer_name)
