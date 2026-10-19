"""Application tracking: stage history, snapshots, deadlines, reminders and calendar export.

- Status changes append ApplicationEvent rows; history is never overwritten.
- Marking a job applied freezes a snapshot of the posting and records the submitted resume version.
- Application status is the user's own pipeline state and is independent of whether the source still lists the job.
- Nothing here submits applications, contacts recruiters or sends e-mail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from icalendar import Calendar, Event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationEvent, Deadline, Job, Preferences, ResumeVersion, User
from app.models.application import APPLICATION_STATUSES, DEADLINE_KINDS

STAGE_ORDER = ["saved", "applied", "oa_received", "oa_completed", "recruiter_screen", "interview", "offer"]
TERMINAL = {"offer", "rejected", "withdrawn", "closed_without_application"}
SUBMITTED_STATUSES = {"applied", "oa_received", "oa_completed", "recruiter_screen", "interview", "offer", "rejected", "withdrawn"}


class TrackingError(ValueError):
    pass


def job_snapshot(job: Job, at: datetime | None) -> dict:
    return {
        "captured_at": (at or datetime.now(UTC)).isoformat(),
        "job_id": str(job.id),
        "source_job_id": job.source_job_id,
        "employer": job.employer,
        "title": job.title,
        "location": job.location_raw,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "salary": None if job.salary_min is None else {"min": job.salary_min, "max": job.salary_max, "currency": job.salary_currency, "interval": job.salary_interval},
        "description_text": job.description_text,
        "apply_url": job.apply_url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "source_status_at_capture": job.status,
        "content_hash": job.content_hash,
    }


def get_or_create(db: Session, user: User, job: Job) -> tuple[Application, bool]:
    app_ = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if app_:
        return app_, False
    now = datetime.now(UTC)
    app_ = Application(user_id=user.id, job_id=job.id, status="saved", employer=job.employer, title=job.title,
                       external_url=job.apply_url, updated_at=now)
    db.add(app_)
    db.flush()
    db.add(ApplicationEvent(application_id=app_.id, from_status=None, to_status="saved", occurred_at=now))
    db.commit()
    return app_, True


def set_status(
    db: Session,
    user: User,
    app_: Application,
    status: str,
    *,
    occurred_at: datetime | None = None,
    note: str = "",
    resume_version_id: uuid.UUID | None = None,
) -> Application:
    if status not in APPLICATION_STATUSES:
        raise TrackingError(f"Unknown status {status!r}")
    now = datetime.now(UTC)
    occurred_at = occurred_at or now
    if occurred_at.tzinfo is None:
        raise TrackingError("occurred_at must include a timezone offset")
    if occurred_at > now + timedelta(minutes=5):
        raise TrackingError("Stage changes cannot be dated in the future")
    if status in SUBMITTED_STATUSES and app_.applied_at is None:
        # First time this application counts as submitted: record what was submitted.
        version_id = resume_version_id
        if version_id is None:
            prefs = db.get(Preferences, user.id)
            version_id = prefs.active_resume_version_id if prefs else None
        if version_id is not None:
            v = db.get(ResumeVersion, version_id)
            if v is None or v.user_id != user.id:
                raise TrackingError("Resume version not found")
        app_.resume_version_id = version_id
        app_.applied_at = occurred_at
        if app_.job_id and app_.job_snapshot is None:
            job = db.get(Job, app_.job_id)
            if job is not None:
                app_.job_snapshot = job_snapshot(job, occurred_at)
    elif resume_version_id is not None:
        v = db.get(ResumeVersion, resume_version_id)
        if v is None or v.user_id != user.id:
            raise TrackingError("Resume version not found")
        app_.resume_version_id = resume_version_id
    if status == app_.status and not note:
        return app_
    db.add(ApplicationEvent(application_id=app_.id, from_status=app_.status, to_status=status, occurred_at=occurred_at, note=note[:2000]))
    app_.status = status
    app_.updated_at = now
    db.commit()
    db.refresh(app_)
    return app_


def to_utc(local_iso: str, tz_name: str) -> datetime:
    """Interpret a wall-clock time ("2026-11-02T17:00") in the given IANA zone and return the UTC instant."""
    if tz_name not in available_timezones():
        raise TrackingError(f"Unknown timezone {tz_name!r}")
    dt = datetime.fromisoformat(local_iso)
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    zone = ZoneInfo(tz_name)
    aware = dt.replace(tzinfo=zone)
    # Wall times that do not exist (spring-forward gap) are rejected rather than silently shifted.
    if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != dt:
        raise TrackingError(f"{local_iso} does not exist in {tz_name} (daylight-saving gap). Choose another time.")
    return aware.astimezone(UTC)


def add_deadline(db: Session, user: User, app_: Application, kind: str, title: str, due_local: str, tz_name: str, notes: str = "") -> Deadline:
    if kind not in DEADLINE_KINDS:
        raise TrackingError(f"kind must be one of {DEADLINE_KINDS}")
    d = Deadline(application_id=app_.id, user_id=user.id, kind=kind, title=title[:200] or kind.replace("_", " "),
                 due_at=to_utc(due_local, tz_name), tz_name=tz_name, notes=notes[:2000])
    db.add(d)
    db.commit()
    return d


def deadline_dict(d: Deadline, user_tz: str, now: datetime) -> dict:
    local = d.due_at.astimezone(ZoneInfo(user_tz))
    entered = d.due_at.astimezone(ZoneInfo(d.tz_name))
    delta = d.due_at - now
    return {
        "id": str(d.id),
        "application_id": str(d.application_id),
        "kind": d.kind,
        "title": d.title,
        "due_at_utc": d.due_at.isoformat(),
        "due_local": local.isoformat(),
        "due_local_display": local.strftime("%a %b %-d, %-I:%M %p ") + (local.tzname() or ""),
        "entered_tz": d.tz_name,
        "entered_local_display": entered.strftime("%a %b %-d, %-I:%M %p ") + (entered.tzname() or ""),
        "completed_at": d.completed_at.isoformat() if d.completed_at else None,
        "overdue": d.completed_at is None and delta.total_seconds() < 0,
        "hours_left": round(delta.total_seconds() / 3600, 1),
        "notes": d.notes,
    }


def reminders(db: Session, user: User, now: datetime | None = None, horizon_days: int = 7) -> dict:
    now = now or datetime.now(UTC)
    tz = user.timezone or "UTC"
    upcoming = db.scalars(
        select(Deadline).where(Deadline.user_id == user.id, Deadline.completed_at.is_(None), Deadline.due_at <= now + timedelta(days=horizon_days))
        .order_by(Deadline.due_at)
    ).all()
    apps = {a.id: a for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    items = []
    for d in upcoming:
        a = apps.get(d.application_id)
        items.append(deadline_dict(d, tz, now) | {"employer": a.employer if a else "", "job_title": a.title if a else ""})
    follow = [
        {"application_id": str(a.id), "employer": a.employer, "title": a.title, "follow_up_at": a.follow_up_at.isoformat(),
         "follow_up_local_display": a.follow_up_at.astimezone(ZoneInfo(tz)).strftime("%a %b %-d, %-I:%M %p %Z"),
         "overdue": a.follow_up_at < now}
        for a in apps.values() if a.follow_up_at and a.follow_up_at <= now + timedelta(days=horizon_days) and a.status not in TERMINAL
    ]
    follow.sort(key=lambda x: x["follow_up_at"])
    return {"timezone": tz, "now_local": now.astimezone(ZoneInfo(tz)).isoformat(), "deadlines": items, "follow_ups": follow}


def ics_for(user: User, deadlines: list[Deadline], apps: dict) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//RoleMatch//Deadlines//EN")
    cal.add("version", "2.0")
    for d in deadlines:
        a = apps.get(d.application_id)
        ev = Event()
        ev.add("uid", f"{d.id}@rolematch.local")
        ev.add("summary", f"{d.title} — {a.employer if a else ''}".strip(" —"))
        ev.add("dtstart", d.due_at.astimezone(ZoneInfo(d.tz_name)))
        ev.add("dtend", d.due_at.astimezone(ZoneInfo(d.tz_name)) + timedelta(minutes=30))
        ev.add("dtstamp", datetime.now(UTC))
        desc = f"{a.title} at {a.employer}" if a else d.title
        if d.notes:
            desc += f"\n\n{d.notes}"
        ev.add("description", desc)
        cal.add_component(ev)
    return cal.to_ical()


def application_dict(db: Session, a: Application, user: User, now: datetime | None = None, include_snapshot: bool = False) -> dict:
    now = now or datetime.now(UTC)
    job = db.get(Job, a.job_id) if a.job_id else None
    out = {
        "id": str(a.id),
        "job_id": str(a.job_id) if a.job_id else None,
        "status": a.status,
        "employer": a.employer,
        "title": a.title,
        "applied_at": a.applied_at.isoformat() if a.applied_at else None,
        "resume_version_id": str(a.resume_version_id) if a.resume_version_id else None,
        "external_url": a.external_url,
        "notes": a.notes,
        "follow_up_at": a.follow_up_at.isoformat() if a.follow_up_at else None,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        "source_listing_status": job.status if job else "unknown",
        "job_changed_since_application": bool(job and a.job_snapshot and job.content_hash != a.job_snapshot.get("content_hash")),
        "events": [{"id": str(e.id), "from": e.from_status, "to": e.to_status, "occurred_at": e.occurred_at.isoformat(), "note": e.note} for e in a.events],
        "deadlines": [deadline_dict(d, user.timezone or "UTC", now) for d in a.deadlines],
        "has_snapshot": a.job_snapshot is not None,
    }
    if a.resume_version_id:
        v = db.get(ResumeVersion, a.resume_version_id)
        if v:
            out["resume_version"] = {"id": str(v.id), "version_number": v.version_number, "origin": v.origin, "created_at": v.created_at.isoformat()}
    if include_snapshot:
        out["job_snapshot"] = a.job_snapshot
    return out
