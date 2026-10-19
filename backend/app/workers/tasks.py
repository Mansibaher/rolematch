"""Background tasks. Each is safe to run more than once."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import JobSource
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@contextmanager
def source_lock(source_id: str, ttl: int = 900):
    """Redis lock so two workers never ingest the same source concurrently. No-op in eager/test mode."""
    if get_settings().celery_eager:
        yield True
        return
    import redis

    r = redis.Redis.from_url(get_settings().redis_url)
    key = f"rolematch:lock:source:{source_id}"
    token = uuid.uuid4().hex
    acquired = bool(r.set(key, token, nx=True, ex=ttl))
    try:
        yield acquired
    finally:
        if acquired and r.get(key) == token.encode():
            r.delete(key)


@celery_app.task(bind=True, name="app.workers.tasks.ingest_source")
def ingest_source(self, source_id: str) -> dict:
    from app.services.jobs.ingest import run_source

    run_key = self.request.id or f"inline-{uuid.uuid4()}"
    with source_lock(source_id) as acquired:
        if not acquired:
            return {"skipped": "another ingestion of this source is running"}
        db = get_sessionmaker()()
        try:
            src = db.get(JobSource, uuid.UUID(source_id))
            if src is None or not src.enabled:
                return {"skipped": "source missing or disabled"}
            run = run_source(db, src, run_key=run_key)
            return {"status": run.status, "new": run.jobs_new, "updated": run.jobs_updated, "closed": run.jobs_closed}
        finally:
            db.close()


@celery_app.task(name="app.workers.tasks.ingest_all")
def ingest_all() -> int:
    db = get_sessionmaker()()
    try:
        ids = [str(i) for i in db.scalars(select(JobSource.id).where(JobSource.enabled.is_(True), JobSource.kind.in_(["greenhouse", "lever"])))]
    finally:
        db.close()
    for sid in ids:
        ingest_source.delay(sid)
    return len(ids)


@celery_app.task(name="app.workers.tasks.reenrich_stale")
def reenrich_stale(limit: int = 500) -> int:
    """Re-extract requirements / embeddings for jobs processed by an older extractor or embedding model."""
    from app.models import Job
    from app.services.matching.enrich import enrich_jobs, needs_enrichment

    db = get_sessionmaker()()
    try:
        jobs = db.scalars(select(Job).where(Job.status != "closed").limit(limit * 4)).all()
        ids = [j.id for j in jobs if needs_enrichment(j)][:limit]
        enrich_jobs(db, ids)
        return len(ids)
    finally:
        db.close()
