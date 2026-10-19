from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, not_found
from app.models import Application, ApplicationEvent, Deadline, Job
from app.models.application import APPLICATION_STATUSES
from app.services.matching import engine
from app.services.tracking import analytics
from app.services.tracking.service import (
    TERMINAL,
    TrackingError,
    add_deadline,
    application_dict,
    get_or_create,
    ics_for,
    reminders,
    set_status,
    to_utc,
)

router = APIRouter(prefix="/api", tags=["applications"])


def _app(db: DB, user, app_id: uuid.UUID) -> Application:
    a = db.get(Application, app_id)
    if a is None or a.user_id != user.id:
        raise not_found("Application")
    return a


@router.get("/applications")
def list_apps(db: DB, user: CurrentUser, status: str | None = None) -> dict:
    q = select(Application).where(Application.user_id == user.id).order_by(Application.updated_at.desc().nullslast(), Application.created_at.desc())
    if status:
        q = q.where(Application.status == status)
    apps = db.scalars(q).all()
    return {"statuses": list(APPLICATION_STATUSES), "applications": [application_dict(db, a, user) for a in apps]}


class CreateBody(BaseModel):
    job_id: uuid.UUID | None = None
    # For applications to jobs RoleMatch does not list:
    employer: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=300)
    external_url: str | None = Field(default=None, max_length=1000)


@router.post("/applications", status_code=201)
def create_app(body: CreateBody, db: DB, user: CurrentUser) -> dict:
    """Save a job to the tracker (status "saved"). Never submits anything anywhere."""
    if body.job_id:
        job = db.get(Job, body.job_id)
        if job is None or not engine.job_visible_to(db, user, job):
            raise not_found("Job")
        a, _ = get_or_create(db, user, job)
    else:
        if not body.employer.strip() or not body.title.strip():
            raise HTTPException(422, "Employer and title are required for applications without a listed job.")
        now = datetime.now(UTC)
        a = Application(user_id=user.id, job_id=None, status="saved", employer=body.employer.strip(), title=body.title.strip(),
                        external_url=body.external_url, updated_at=now)
        db.add(a)
        db.flush()
        db.add(ApplicationEvent(application_id=a.id, from_status=None, to_status="saved", occurred_at=now))
        db.commit()
    return application_dict(db, a, user)


@router.get("/applications/{app_id}")
def get_app(app_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    return application_dict(db, _app(db, user, app_id), user, include_snapshot=True)


class StatusBody(BaseModel):
    status: str
    occurred_at: datetime | None = None
    note: str = Field(default="", max_length=2000)
    resume_version_id: uuid.UUID | None = None


@router.post("/applications/{app_id}/status")
def change_status(app_id: uuid.UUID, body: StatusBody, db: DB, user: CurrentUser) -> dict:
    a = _app(db, user, app_id)
    try:
        set_status(db, user, a, body.status, occurred_at=body.occurred_at, note=body.note, resume_version_id=body.resume_version_id)
    except TrackingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return application_dict(db, a, user)


class PatchBody(BaseModel):
    notes: str | None = Field(default=None, max_length=20_000)
    follow_up_local: str | None = None  # "2026-11-02T09:00" in tz
    tz: str | None = None
    clear_follow_up: bool = False
    external_url: str | None = Field(default=None, max_length=1000)


@router.patch("/applications/{app_id}")
def patch_app(app_id: uuid.UUID, body: PatchBody, db: DB, user: CurrentUser) -> dict:
    a = _app(db, user, app_id)
    if body.notes is not None:
        a.notes = body.notes
    if body.external_url is not None:
        a.external_url = body.external_url or None
    if body.clear_follow_up:
        a.follow_up_at = None
    elif body.follow_up_local:
        try:
            a.follow_up_at = to_utc(body.follow_up_local, body.tz or user.timezone)
        except (TrackingError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
    a.updated_at = datetime.now(UTC)
    db.commit()
    return application_dict(db, a, user)


@router.delete("/applications/{app_id}")
def delete_app(app_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    a = _app(db, user, app_id)
    db.delete(a)
    db.commit()
    return {"ok": True}


class DeadlineBody(BaseModel):
    kind: str
    title: str = Field(default="", max_length=200)
    due_local: str = Field(description="Wall-clock time in `tz`, e.g. 2026-11-02T17:00")
    tz: str | None = None
    notes: str = Field(default="", max_length=2000)


@router.post("/applications/{app_id}/deadlines", status_code=201)
def create_deadline(app_id: uuid.UUID, body: DeadlineBody, db: DB, user: CurrentUser) -> dict:
    a = _app(db, user, app_id)
    try:
        add_deadline(db, user, a, body.kind, body.title, body.due_local, body.tz or user.timezone, body.notes)
    except (TrackingError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.refresh(a)
    return application_dict(db, a, user)


@router.post("/deadlines/{deadline_id}/complete")
def complete_deadline(deadline_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    d = db.get(Deadline, deadline_id)
    if d is None or d.user_id != user.id:
        raise not_found("Deadline")
    d.completed_at = None if d.completed_at else datetime.now(UTC)
    db.commit()
    return {"ok": True, "completed_at": d.completed_at.isoformat() if d.completed_at else None}


@router.delete("/deadlines/{deadline_id}")
def delete_deadline(deadline_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    d = db.get(Deadline, deadline_id)
    if d is None or d.user_id != user.id:
        raise not_found("Deadline")
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.get("/reminders")
def get_reminders(db: DB, user: CurrentUser, horizon_days: int = 7) -> dict:
    return reminders(db, user, horizon_days=max(1, min(horizon_days, 60)))


@router.get("/calendar.ics")
def calendar(db: DB, user: CurrentUser, application_id: uuid.UUID | None = None) -> Response:
    q = select(Deadline).where(Deadline.user_id == user.id, Deadline.completed_at.is_(None))
    if application_id:
        q = q.where(Deadline.application_id == application_id)
    deadlines = db.scalars(q).all()
    apps = {a.id: a for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    return Response(ics_for(user, deadlines, apps), media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="rolematch-deadlines.ics"'})


@router.get("/analytics")
def get_analytics(db: DB, user: CurrentUser) -> dict:
    return analytics.compute(db, user)


@router.get("/dashboard")
def dashboard(db: DB, user: CurrentUser) -> dict:
    now = datetime.now(UTC)
    rem = reminders(db, user, now, horizon_days=7)
    apps = db.scalars(select(Application).where(Application.user_id == user.id)).all()
    stale_saved = []
    for a in apps:
        if a.status != "saved":
            continue
        job = db.get(Job, a.job_id) if a.job_id else None
        if job is not None and job.status == "closed":
            stale_saved.append({"application_id": str(a.id), "employer": a.employer, "title": a.title, "reason": "The employer no longer lists this job."})
        elif (now - a.created_at) > timedelta(days=7):
            stale_saved.append({"application_id": str(a.id), "employer": a.employer, "title": a.title, "reason": f"Saved {(now - a.created_at).days} days ago and not applied yet."})
    awaiting = [{"application_id": str(a.id), "employer": a.employer, "title": a.title, "days": (now - a.applied_at).days}
                for a in apps if a.status == "applied" and a.applied_at and (now - a.applied_at).days >= 14]
    top_new: list[dict] = []
    profile_ready = True
    try:
        out = engine.rank_for_user(db, user)
        tracked = {a.job_id for a in apps}
        fresh = [(j, r) for j, r in out["results"] if r["eligible"] and j.id not in tracked and (now - j.first_seen_at) <= timedelta(days=3)]
        fresh.sort(key=lambda x: -x[1]["scores"]["application_priority"])
        top_new = [{"id": str(j.id), "employer": j.employer, "title": j.title, "scores": r["scores"], "freshness": r["freshness"]} for j, r in fresh[:5]]
    except engine.ProfileNotReady:
        profile_ready = False
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1
    from app.models import JobSource, UserWatch

    failing = [s.employer_name for s in db.scalars(select(JobSource).join(UserWatch, UserWatch.source_id == JobSource.id)
                                                   .where(UserWatch.user_id == user.id, JobSource.consecutive_failures > 0))]
    return {
        "reminders": rem,
        "stale_saved": stale_saved,
        "awaiting_response": awaiting,
        "top_new_jobs": top_new,
        "pipeline": by_status,
        "active_pipeline": sum(v for k, v in by_status.items() if k not in TERMINAL),
        "sources_with_errors": failing,
        "profile_ready": profile_ready,
    }
