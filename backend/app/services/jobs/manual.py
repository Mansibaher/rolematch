"""Manual job addition: pasted description text, optionally with a source URL.

- If the URL is a Greenhouse or Lever posting, the posting is fetched from that source's documented API host
  (never from the user-supplied host) and stored as a monitored public job.
- Otherwise nothing is fetched. The user must paste the description; the URL is kept as a reference link.
Manual jobs are private to the user who added them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Job, User, UserWatch
from app.services.jobs.http import SourceError
from app.services.jobs.ingest import adapter_for, dedupe, ensure_source, upsert_jobs
from app.services.jobs.normalize import NormalizedJob, finish
from app.services.matching.enrich import enrich_jobs
from app.services.url_safety import canonicalize_url, parse_supported_posting


class ManualImportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ManualInput:
    title: str
    employer: str
    description: str
    url: str | None = None
    location: str = ""


def import_from_url(db: Session, user: User, url: str) -> Job:
    sp = parse_supported_posting(url)
    if sp is None:
        raise ManualImportError(
            "unsupported_url",
            "Automatic fetching only supports Greenhouse and Lever posting links. Paste the job description instead; "
            "the link will be saved for reference.",
        )
    employer_guess = sp.identifier.replace("-", " ").replace("_", " ").title()
    adapter = adapter_for(sp.kind)
    try:
        nj = adapter.fetch_one(sp.identifier, sp.job_id, employer_guess, sp.region)
    except SourceError as exc:
        raise ManualImportError(exc.kind, f"Could not fetch the posting ({exc.kind}). Paste the description instead.") from exc
    source = ensure_source(db, sp.kind, sp.identifier, nj.employer or employer_guess, sp.region)
    db.flush()
    if db.get(UserWatch, (user.id, source.id)) is None:
        db.add(UserWatch(user_id=user.id, source_id=source.id))
    ids, _, _ = upsert_jobs(db, source, [nj], datetime.now(UTC))
    db.commit()
    enrich_jobs(db, ids)
    dedupe(db, ids)
    db.commit()
    return db.query(Job).filter(Job.source_id == source.id, Job.source_job_id == nj.source_job_id).one()


def import_pasted(db: Session, user: User, data: ManualInput) -> Job:
    if len(data.description.strip()) < 80:
        raise ManualImportError("description_too_short", "Paste the full job description (at least a few sentences).")
    if not data.title.strip() or not data.employer.strip():
        raise ManualImportError("missing_fields", "Title and employer are required.")
    source = ensure_source(db, "manual", f"user-{user.id}", "Added manually")
    db.flush()
    digest = hashlib.sha256((data.employer + "|" + data.title + "|" + data.description).encode()).hexdigest()[:24]
    nj = finish(
        NormalizedJob(
            source_job_id=f"manual-{digest}",
            employer=data.employer.strip()[:200],
            title=data.title.strip()[:300],
            description_text=data.description.strip(),
            location_raw=data.location.strip(),
            apply_url=data.url or None,
            canonical_url=canonicalize_url(data.url),
            raw_payload={"manual": True},
        )
    )
    ids, _, _ = upsert_jobs(db, source, [nj], datetime.now(UTC), owner_user_id=user.id)
    db.commit()
    enrich_jobs(db, ids)
    dedupe(db, ids)
    db.commit()
    job = db.query(Job).filter(Job.source_id == source.id, Job.source_job_id == nj.source_job_id).one()
    return job
