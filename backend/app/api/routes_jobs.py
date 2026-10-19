from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, not_found
from app.models import Application, Job, JobFeedback, JobSource, Preferences
from app.services.jobs.manual import ManualImportError, ManualInput, import_from_url, import_pasted
from app.services.matching import engine
from app.services.skills import get_taxonomy

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

SORTS = {"priority", "qualification", "posted", "first_seen"}


def job_card(job: Job, res: dict, app_status: str | None, source: JobSource | None) -> dict:
    tax = get_taxonomy()
    reqs = res["requirements"]
    top_strengths = []
    for r in reqs:
        if r["status"] == "supported" and r["kind"] in ("required", "preferred"):
            for s in r["matched_skills"]:
                n = tax.name(s)
                if n not in top_strengths:
                    top_strengths.append(n)
    gaps = [r["text"] for r in reqs if r["status"] == "not_evidenced" and r["kind"] in ("required", "unclear")]
    unknowns = [c["label"] for c in res["checks"] if c["status"] == "unknown"]
    return {
        "id": str(job.id),
        "employer": job.employer,
        "title": job.title,
        "location": job.location_raw,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "source": {"kind": source.kind if source else None, "label": _source_label(source), "is_fixture": job.is_fixture,
                   "is_manual": job.owner_user_id is not None},
        "freshness": res["freshness"],
        "apply_url": job.apply_url,
        "scores": res["scores"],
        "eligible": res["eligible"],
        "excluded_by": res["eligibility"]["excluded_by"],
        "uncertain_hard": res["eligibility"]["uncertain_hard"],
        "deprioritized_by": res["eligibility"]["soft_deprioritized_by"],
        # One entry per checkable required qualification, in posting order: drives the evidence strip.
        "required_statuses": [r["status"] for r in reqs if r["kind"] in ("required", "unclear") and r["category"] != "soft"],
        "strengths": top_strengths[:5],
        "gaps": gaps[:3],
        "unknowns": unknowns[:4],
        "role_families": (job.extraction or {}).get("role_families") or [],
        "seniority": res["seniority"]["level"],
        "application_status": app_status,
        "salary": None if job.salary_min is None else {"min": job.salary_min, "max": job.salary_max, "currency": job.salary_currency, "interval": job.salary_interval},
    }


def _source_label(source: JobSource | None) -> str:
    if source is None:
        return "Unknown source"
    return {"greenhouse": f"{source.employer_name} on Greenhouse", "lever": f"{source.employer_name} on Lever",
            "manual": "Added by you", "fixture": "Demo fixture (synthetic)"}.get(source.kind, source.kind)


@router.get("/feed")
def feed(
    db: DB,
    user: CurrentUser,
    sort: str = "priority",
    location: str | None = Query(default=None, max_length=100),
    family: str | None = Query(default=None, max_length=50),
    app_status: str | None = Query(default=None, description="none | saved | applied | any status name"),
    include_excluded: bool = False,
    include_closed: bool = False,
    min_qualification: float = Query(default=0, ge=0, le=100),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {sorted(SORTS)}")
    try:
        out = engine.rank_for_user(db, user, include_closed=include_closed)
    except engine.ProfileNotReady as exc:
        raise HTTPException(409, str(exc)) from exc
    apps = {a.job_id: a.status for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    sources = {s.id: s for s in db.scalars(select(JobSource))}
    rows = []
    for job, res in out["results"]:
        st = apps.get(job.id)
        if location and location.lower() not in (job.location_raw or "").lower() and not (location.lower() == "remote" and job.remote_type == "remote"):
            continue
        if family and family not in ((job.extraction or {}).get("role_families") or []):
            continue
        if app_status == "none" and st is not None:
            continue
        if app_status and app_status not in ("none",) and st != app_status:
            continue
        if res["scores"]["qualification_match"] < min_qualification:
            continue
        if q and q.lower() not in f"{job.title} {job.employer}".lower():
            continue
        rows.append(job_card(job, res, st, sources.get(job.source_id)))
    eligible = [r for r in rows if r["eligible"]]
    excluded = [r for r in rows if not r["eligible"]]

    def key(r):
        if sort == "qualification":
            return -r["scores"]["qualification_match"]
        if sort == "posted":
            return (r["freshness"]["posted_at"] is None, -(datetime.fromisoformat(r["freshness"]["posted_at"]).timestamp() if r["freshness"]["posted_at"] else 0))
        if sort == "first_seen":
            return -datetime.fromisoformat(r["freshness"]["first_seen_at"]).timestamp()
        return -r["scores"]["application_priority"]

    eligible.sort(key=key)
    excluded.sort(key=key)
    prefs = db.get(Preferences, user.id)
    explanation = None
    if not eligible:
        explanation = engine.no_results_explanation(out["results"], prefs)
        if out["stats"]["visible"] == 0:
            explanation["suggestions"].insert(0, "No jobs are visible yet. Follow companies on the Sources page, run ingestion, or add a job manually.")
    return {
        "jobs": eligible[:limit],
        "excluded": excluded[:limit] if include_excluded else [],
        "excluded_count": len(excluded),
        "stats": out["stats"],
        "profile_version_id": out["profile_version_id"],
        "no_results": explanation,
        "sort": sort,
        "disclaimer": "Scores show documented alignment with each posting, not the probability of an interview or offer.",
    }


def _visible_job(db: DB, user, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None or not engine.job_visible_to(db, user, job):
        raise not_found("Job")
    return job


@router.get("/{job_id}")
def job_detail(job_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    job = _visible_job(db, user, job_id)
    try:
        res = engine.match_one(db, user, job)
    except engine.ProfileNotReady as exc:
        raise HTTPException(409, str(exc)) from exc
    app_ = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    src = db.get(JobSource, job.source_id)
    fb = db.get(JobFeedback, (user.id, job.id))
    dupes = db.scalars(select(Job).where(Job.duplicate_of_id == job.id)).all()
    ex = job.extraction or {}
    return {
        "job": job_card(job, res, app_.status if app_ else None, src) | {
            "description_text": job.description_text,
            "department": job.department,
            "requisition_id": job.requisition_id,
            "salary_basis": job.salary_basis,
            "remote_restriction": job.remote_restriction,
            "employment_type_basis": job.employment_type_basis,
            "status": job.status,
            "status_changed_at": job.status_changed_at.isoformat() if job.status_changed_at else None,
            "source_updated_at": job.source_updated_at.isoformat() if job.source_updated_at else None,
            "also_listed_as": [{"id": str(d.id), "source": _source_label(db.get(JobSource, d.source_id)), "reason": d.duplicate_reason, "apply_url": d.apply_url} for d in dupes],
        },
        "match": res,
        "extraction": {"eligibility": ex.get("eligibility"), "structured": ex.get("structured"), "warnings": ex.get("warnings"),
                       "instruction_like_text": ex.get("instruction_like_text"), "role_families": ex.get("role_families")},
        "application": {"id": str(app_.id), "status": app_.status} if app_ else None,
        "feedback": fb.label if fb else None,
    }


class ManualJobBody(BaseModel):
    url: str | None = Field(default=None, max_length=1000)
    title: str = Field(default="", max_length=300)
    employer: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=60_000)


@router.post("/manual", status_code=201)
def add_manual(body: ManualJobBody, db: DB, user: CurrentUser) -> dict:
    try:
        if body.description.strip():
            job = import_pasted(db, user, ManualInput(body.title, body.employer, body.description, body.url, body.location))
            mode = "pasted"
        elif body.url:
            job = import_from_url(db, user, body.url)
            mode = "fetched_from_supported_source"
        else:
            raise ManualImportError("missing_input", "Paste a job description, or a Greenhouse/Lever posting link.")
    except ManualImportError as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc
    return {"id": str(job.id), "mode": mode, "duplicate_of": str(job.duplicate_of_id) if job.duplicate_of_id else None,
            "duplicate_reason": job.duplicate_reason}


class FeedbackBody(BaseModel):
    label: str = Field(pattern="^(relevant|not_relevant)$")
    reason: str = Field(default="", max_length=500)


@router.post("/{job_id}/feedback")
def feedback(job_id: uuid.UUID, body: FeedbackBody, db: DB, user: CurrentUser) -> dict:
    job = _visible_job(db, user, job_id)
    adj = engine.record_feedback(db, user, job, body.label, body.reason)
    return {"ok": True, "feedback_adjustments": adj,
            "note": "Feedback adjusts soft ranking only. It never changes eligibility filters or requirement evidence."}


@router.get("/stats/new-today")
def new_today(db: DB, user: CurrentUser) -> dict:
    since = datetime.now(UTC) - timedelta(hours=24)
    base = engine.visible_jobs_query(user)
    jobs = db.scalars(base.where(Job.first_seen_at >= since)).all()
    return {"first_seen_last_24h": len(jobs), "posted_last_24h": sum(1 for j in jobs if j.posted_at and j.posted_at >= since)}
