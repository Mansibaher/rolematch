from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, not_found
from app.core.config import get_settings
from app.models import Job, JobSource, SourceRun, UserWatch
from app.services.jobs.ingest import ensure_source, run_source
from app.services.url_safety import IDENT_RX

router = APIRouter(prefix="/api/sources", tags=["sources"])

COVERAGE_NOTE = (
    "RoleMatch only checks the employer boards listed here (public Greenhouse and Lever job boards). "
    "It does not search LinkedIn, Indeed or the wider job market. Add companies you care about, or add jobs manually."
)


def health(db, src: JobSource) -> dict:
    runs = db.scalars(select(SourceRun).where(SourceRun.source_id == src.id).order_by(SourceRun.started_at.desc()).limit(5)).all()
    counts = dict(db.execute(select(Job.status, func.count()).where(Job.source_id == src.id).group_by(Job.status)).all())
    now = datetime.now(UTC)
    stale_after = timedelta(hours=get_settings().stale_after_hours)
    if src.kind in ("manual", "fixture"):
        state = "static"
    elif src.last_success_at is None:
        state = "never_checked" if not runs else "failing"
    elif src.consecutive_failures >= 3:
        state = "failing"
    elif src.consecutive_failures > 0:
        state = "degraded"
    elif now - src.last_success_at > stale_after:
        state = "stale"
    else:
        state = "healthy"
    return {
        "state": state,
        "last_success_at": src.last_success_at.isoformat() if src.last_success_at else None,
        "last_attempt_at": src.last_attempt_at.isoformat() if src.last_attempt_at else None,
        "consecutive_failures": src.consecutive_failures,
        "jobs": {"active": counts.get("active", 0), "closed": counts.get("closed", 0), "unknown": counts.get("unknown", 0)},
        "recent_runs": [
            {"id": str(r.id), "started_at": r.started_at.isoformat(), "status": r.status, "complete_listing": r.complete_listing,
             "jobs_seen": r.jobs_seen, "jobs_new": r.jobs_new, "jobs_updated": r.jobs_updated, "jobs_closed": r.jobs_closed,
             "http_requests": r.http_requests, "error_kind": r.error_kind, "error_detail": r.error_detail}
            for r in runs
        ],
    }


def source_dict(db, src: JobSource, watching: bool) -> dict:
    return {"id": str(src.id), "kind": src.kind, "identifier": src.identifier, "employer_name": src.employer_name,
            "region": src.region, "enabled": src.enabled, "from_config": src.from_config, "watching": watching,
            "health": health(db, src)}


@router.get("")
def list_sources(db: DB, user: CurrentUser) -> dict:
    watched = set(db.scalars(select(UserWatch.source_id).where(UserWatch.user_id == user.id)))
    srcs = db.scalars(select(JobSource).where(JobSource.kind.in_(["greenhouse", "lever", "fixture"])).order_by(JobSource.kind, JobSource.employer_name)).all()
    return {"coverage_note": COVERAGE_NOTE, "sources": [source_dict(db, s, s.id in watched) for s in srcs]}


class AddSourceBody(BaseModel):
    kind: str = Field(pattern="^(greenhouse|lever)$")
    identifier: str = Field(min_length=1, max_length=100)
    employer_name: str = Field(min_length=1, max_length=200)
    region: str = Field(default="us", pattern="^(us|eu)$")
    check_now: bool = True


@router.post("", status_code=201)
def add_source(body: AddSourceBody, db: DB, user: CurrentUser) -> dict:
    if not IDENT_RX.match(body.identifier):
        raise HTTPException(422, "Board identifiers may only contain letters, digits, '-' and '_'.")
    src = ensure_source(db, body.kind, body.identifier.strip(), body.employer_name.strip(), body.region)
    src.enabled = True
    db.flush()
    if db.get(UserWatch, (user.id, src.id)) is None:
        db.add(UserWatch(user_id=user.id, source_id=src.id))
    db.commit()
    run = None
    if body.check_now:
        r = run_source(db, src)
        run = {"status": r.status, "jobs_seen": r.jobs_seen, "error_kind": r.error_kind, "error_detail": r.error_detail}
    return source_dict(db, src, True) | {"check": run}


@router.post("/{source_id}/watch")
def watch(source_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    src = db.get(JobSource, source_id)
    if src is None or src.kind == "manual":
        raise not_found("Source")
    if db.get(UserWatch, (user.id, src.id)) is None:
        db.add(UserWatch(user_id=user.id, source_id=src.id))
        db.commit()
    return {"ok": True}


@router.delete("/{source_id}/watch")
def unwatch(source_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    w = db.get(UserWatch, (user.id, source_id))
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


@router.post("/{source_id}/refresh")
def refresh(source_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    src = db.get(JobSource, source_id)
    if src is None or db.get(UserWatch, (user.id, source_id)) is None:
        raise not_found("Source")
    if src.kind not in ("greenhouse", "lever"):
        raise HTTPException(409, "Only Greenhouse and Lever sources can be refreshed.")
    if get_settings().celery_eager:
        r = run_source(db, src)
        return {"queued": False, "status": r.status, "error_kind": r.error_kind}
    from app.workers.tasks import ingest_source

    ingest_source.delay(str(src.id))
    return {"queued": True}
