"""Ingestion: fetch a source, upsert idempotently, infer closures safely, deduplicate conservatively.

Guarantees (each covered by tests):
- Idempotent: re-running a fetch, or re-delivering the same task, never creates duplicate rows. Rows are keyed
  by (source_id, source_job_id) and written with INSERT ... ON CONFLICT DO UPDATE; a run key that already
  completed is not executed twice.
- Outage-safe: a failed or incomplete fetch never changes the status of jobs it did not see. Closure is inferred
  only from *complete* listings, and only after `close_after_consecutive_misses` consecutive complete misses.
- first_seen_at is set once and never overwritten; posted_at comes only from the source.
- Dedup never merges rows from the same source and never merges rows with different requisition ids.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job, JobSource, SourceRun
from app.services.jobs.adapters.base import FetchResult
from app.services.jobs.adapters.fixtures import FixtureAdapter
from app.services.jobs.adapters.greenhouse import GreenhouseAdapter
from app.services.jobs.adapters.lever import LeverAdapter
from app.services.jobs.http import SourceError
from app.services.jobs.normalize import NormalizedJob

log = logging.getLogger(__name__)


def adapter_for(kind: str):
    return {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter, "fixture": FixtureAdapter}[kind]()


def _values(source: JobSource, nj: NormalizedJob, now: datetime, owner_user_id=None) -> dict:
    sal = nj.salary
    return {
        "id": uuid.uuid4(),
        "source_id": source.id,
        "source_job_id": nj.source_job_id,
        "owner_user_id": owner_user_id,
        "is_fixture": source.kind == "fixture",
        "employer": nj.employer,
        "title": nj.title,
        "description_text": nj.description_text,
        "description_html": nj.description_html,
        "department": nj.department,
        "requisition_id": nj.requisition_id,
        "location_raw": nj.location_raw[:500],
        "locations": nj.locations,
        "remote_type": nj.remote_type,
        "remote_restriction": (nj.remote_restriction or None) and nj.remote_restriction[:300],
        "employment_type": nj.employment_type,
        "employment_type_basis": nj.employment_type_basis,
        "salary_min": sal.min if sal else None,
        "salary_max": sal.max if sal else None,
        "salary_currency": sal.currency if sal else None,
        "salary_interval": sal.interval if sal else None,
        "salary_basis": sal.basis[:200] if sal else None,
        "posted_at": nj.posted_at,
        "posted_at_basis": nj.posted_at_basis,
        "source_updated_at": nj.source_updated_at,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
        "apply_url": nj.apply_url,
        "canonical_url": nj.canonical_url,
        "status": "active" if source.kind != "manual" else "unknown",
        "status_changed_at": now,
        "consecutive_misses": 0,
        "content_hash": nj.fingerprint(),
        "raw_payload": nj.raw_payload,
        "created_at": now,
        "updated_at": now,
    }


UPDATABLE = (
    "employer", "title", "description_text", "description_html", "department", "requisition_id", "location_raw",
    "locations", "remote_type", "remote_restriction", "employment_type", "employment_type_basis", "salary_min",
    "salary_max", "salary_currency", "salary_interval", "salary_basis", "posted_at", "posted_at_basis",
    "source_updated_at", "last_seen_at", "last_checked_at", "apply_url", "canonical_url", "consecutive_misses",
    "content_hash", "raw_payload", "updated_at",
)


def upsert_jobs(db: Session, source: JobSource, jobs: list[NormalizedJob], now: datetime, owner_user_id=None) -> tuple[list[uuid.UUID], int, int]:
    """Returns (ids needing enrichment, new_count, updated_count)."""
    if not jobs:
        return [], 0, 0
    prior = {
        r.source_job_id: (r.id, r.content_hash, r.status)
        for r in db.execute(
            select(Job.id, Job.source_job_id, Job.content_hash, Job.status).where(
                Job.source_id == source.id, Job.source_job_id.in_([j.source_job_id for j in jobs])
            )
        )
    }
    to_enrich: list[uuid.UUID] = []
    new = updated = 0
    seen_ids: set[str] = set()
    for nj in jobs:
        if nj.source_job_id in seen_ids:  # duplicate ids inside one payload: keep the first
            continue
        seen_ids.add(nj.source_job_id)
        vals = _values(source, nj, now, owner_user_id)
        stmt = insert(Job).values(**vals)
        set_ = {k: stmt.excluded[k] for k in UPDATABLE}
        if source.kind != "manual":
            # A job that reappears in a listing is active again; the timestamp moves only on a real change.
            set_["status"] = "active"
            set_["status_changed_at"] = case(
                (Job.status == "active", Job.status_changed_at), else_=stmt.excluded["status_changed_at"]
            )
        stmt = stmt.on_conflict_do_update(index_elements=[Job.source_id, Job.source_job_id], set_=set_).returning(Job.id)
        job_id = db.execute(stmt).scalar_one()
        p = prior.get(nj.source_job_id)
        if p is None:
            new += 1
            to_enrich.append(job_id)
        else:
            if p[1] != vals["content_hash"]:
                updated += 1
                to_enrich.append(job_id)
    return to_enrich, new, updated


def close_missing(db: Session, source: JobSource, seen: set[str], now: datetime) -> int:
    threshold = get_settings().close_after_consecutive_misses
    db.execute(
        update(Job)
        .where(Job.source_id == source.id, Job.status != "closed", Job.source_job_id.not_in(seen) if seen else True)
        .values(consecutive_misses=Job.consecutive_misses + 1, last_checked_at=now)
    )
    res = db.execute(
        update(Job)
        .where(Job.source_id == source.id, Job.status != "closed", Job.consecutive_misses >= threshold)
        .values(status="closed", status_changed_at=now)
        .returning(Job.id)
    )
    return len(res.all())


_WORD = re.compile(r"[a-z0-9]+")


def _norm(s: str | None) -> str:
    return " ".join(_WORD.findall((s or "").lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedupe(db: Session, job_ids: list[uuid.UUID]) -> int:
    """Mark cross-source duplicates of the given jobs. Returns number marked."""
    marked = 0
    for jid in job_ids:
        job = db.get(Job, jid)
        if job is None or job.duplicate_of_id:
            continue
        candidates = db.scalars(
            select(Job).where(
                Job.id != job.id,
                Job.source_id != job.source_id,
                Job.duplicate_of_id.is_(None),
                Job.first_seen_at <= job.first_seen_at,
                # Manual jobs are private: only compare against public jobs or the same owner's jobs.
                (Job.owner_user_id.is_(None)) | (Job.owner_user_id == job.owner_user_id),
            )
        ).all()
        if db.scalar(select(Job.id).where(Job.duplicate_of_id == job.id).limit(1)) is not None:
            continue  # already canonical for other rows
        same_source_lookalikes = [
            o for o in db.scalars(select(Job).where(Job.source_id == job.source_id, Job.id != job.id)).all()
            if _norm(o.title) == _norm(job.title) and _norm(o.location_raw) == _norm(job.location_raw)
            and _jaccard(o.description_text, job.description_text) >= 0.85
        ]
        url_match = next((o for o in candidates if job.canonical_url and o.canonical_url == job.canonical_url), None)
        signal_matches = [
            o for o in candidates
            if _norm(job.employer) == _norm(o.employer)
            and _norm(job.title) == _norm(o.title)
            and _norm(job.location_raw) == _norm(o.location_raw)
            and _jaccard(job.description_text, o.description_text) >= 0.85
        ]
        target, reason = None, None
        if url_match is not None:
            target, reason = url_match, "same canonical URL"
        elif len(signal_matches) == 1 and not same_source_lookalikes:
            # Exactly one look-alike and no look-alikes in the job's own source. Several look-alikes means the
            # employer has multiple similar openings, so the match is ambiguous and nothing is merged.
            target, reason = signal_matches[0], "same employer, title, location and near-identical description"
        if target is not None and job.requisition_id and target.requisition_id and job.requisition_id != target.requisition_id:
            target = None  # different requisitions are never merged
        if target is not None:
            job.duplicate_of_id = target.id
            job.duplicate_reason = reason
            marked += 1
    return marked


def run_source(db: Session, source: JobSource, run_key: str | None = None, adapter=None, enrich: bool = True) -> SourceRun:
    run_key = run_key or f"manual-{uuid.uuid4()}"
    existing = db.scalar(select(SourceRun).where(SourceRun.run_key == run_key))
    if existing is not None and existing.status != "running":
        log.info("run %s already completed; skipping re-delivered task", run_key)
        return existing
    now = datetime.now(UTC)
    run = existing or SourceRun(source_id=source.id, run_key=run_key, started_at=now, status="running")
    run.started_at, run.status = now, "running"
    if existing is None:
        db.add(run)
    source.last_attempt_at = now
    db.commit()

    adapter = adapter or adapter_for(source.kind)
    try:
        result: FetchResult = adapter.fetch_all(source.identifier, source.employer_name, source.region)
    except SourceError as exc:
        run.status, run.error_kind, run.error_detail = "failed", exc.kind, exc.detail[:500]
        run.finished_at = datetime.now(UTC)
        source.consecutive_failures += 1
        db.commit()
        return run
    except Exception as exc:  # unexpected adapter bug: record, never touch job statuses
        run.status, run.error_kind, run.error_detail = "failed", "adapter_error", type(exc).__name__
        run.finished_at = datetime.now(UTC)
        source.consecutive_failures += 1
        db.commit()
        log.exception("adapter error for source %s", source.id)
        return run

    to_enrich, new, updated = upsert_jobs(db, source, result.jobs, now)
    closed = 0
    if result.complete:
        closed = close_missing(db, source, {j.source_job_id for j in result.jobs}, now)
    run.complete_listing = result.complete
    run.pages_fetched, run.http_requests = result.pages, result.counter.requests
    run.jobs_seen, run.jobs_new, run.jobs_updated, run.jobs_closed = len(result.jobs), new, updated, closed
    run.status = "success" if result.complete else "partial"
    run.error_detail = "; ".join(result.notes)[:500] or None
    run.finished_at = datetime.now(UTC)
    source.consecutive_failures = 0
    source.last_success_at = now
    db.commit()

    if enrich and to_enrich:
        from app.services.matching.enrich import enrich_jobs

        enrich_jobs(db, to_enrich)
    if to_enrich:
        dedupe(db, to_enrich)
        db.commit()
    return run


def ensure_source(db: Session, kind: str, identifier: str, employer_name: str, region: str = "us", from_config: bool = False) -> JobSource:
    src = db.scalar(select(JobSource).where(and_(JobSource.kind == kind, JobSource.identifier == identifier)))
    if src is None:
        src = JobSource(kind=kind, identifier=identifier, employer_name=employer_name, region=region, from_config=from_config)
        db.add(src)
        db.flush()
    else:
        src.employer_name = employer_name or src.employer_name
        src.region = region
        src.from_config = src.from_config or from_config
    return src
