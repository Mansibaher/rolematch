from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser, not_found
from app.models import Job
from app.services import tailoring
from app.services.matching import engine

router = APIRouter(prefix="/api/tailoring", tags=["tailoring"])


def _job(db, user, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None or not engine.job_visible_to(db, user, job):
        raise not_found("Job")
    return job


@router.get("/{job_id}")
def get_suggestions(job_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    try:
        return tailoring.suggest(db, user, _job(db, user, job_id))
    except engine.ProfileNotReady as exc:
        raise HTTPException(409, str(exc)) from exc


class CheckBody(BaseModel):
    source_text: str = Field(max_length=1000)
    text: str = Field(max_length=1000)


@router.post("/check")
def check(body: CheckBody, user: CurrentUser) -> dict:
    c = tailoring.check_claims(body.source_text, body.text)
    return {"ok": c.ok, "problems": c.problems}


class Accepted(BaseModel):
    id: str = Field(max_length=200)
    text: str | None = Field(default=None, max_length=1000)


class SaveBody(BaseModel):
    accepted: list[Accepted] = Field(min_length=1, max_length=30)


@router.post("/{job_id}/save", status_code=201)
def save(job_id: uuid.UUID, body: SaveBody, db: DB, user: CurrentUser) -> dict:
    job = _job(db, user, job_id)
    try:
        v = tailoring.save_tailored(db, user, job, [a.model_dump() for a in body.accepted])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except engine.ProfileNotReady as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"version_id": str(v.id), "version_number": v.version_number, "evidence_eligible": v.evidence_eligible,
            "note": "Saved as a tailored copy. It is not used as matching evidence, and your original version is unchanged."}
