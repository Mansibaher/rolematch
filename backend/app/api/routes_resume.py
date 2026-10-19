from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, not_found
from app.core.config import get_settings
from app.models import Preferences, ProfileEntry, ProfileSkill, Resume, ResumeVersion
from app.services.resume import service
from app.services.resume.extract import UploadRejected

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


def _version_summary(v: ResumeVersion) -> dict:
    return {
        "id": str(v.id),
        "version_number": v.version_number,
        "origin": v.origin,
        "created_at": v.created_at.isoformat(),
        "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None,
        "extraction_status": v.extraction_status,
        "evidence_eligible": v.evidence_eligible,
        "tailored_for_job_id": str(v.tailored_for_job_id) if v.tailored_for_job_id else None,
        "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
    }


@router.get("")
def list_resumes(db: DB, user: CurrentUser) -> dict:
    resumes = db.scalars(select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at)).all()
    prefs = db.get(Preferences, user.id)
    return {
        "active_version_id": str(prefs.active_resume_version_id) if prefs and prefs.active_resume_version_id else None,
        "resumes": [
            {"id": str(r.id), "label": r.label, "created_at": r.created_at.isoformat(), "versions": [_version_summary(v) for v in r.versions]}
            for r in resumes
        ],
    }


@router.post("/upload", status_code=201)
async def upload_resume(
    db: DB,
    user: CurrentUser,
    file: UploadFile | None = File(default=None),
    pasted_text: str | None = Form(default=None, max_length=60_000),
    label: str = Form(default="", max_length=120),
    resume_id: uuid.UUID | None = Form(default=None),
) -> dict:
    if file is None and not (pasted_text and pasted_text.strip()):
        raise HTTPException(422, "Upload a PDF/DOCX file or paste your resume text.")
    data = None
    if file is not None:
        limit = get_settings().max_upload_bytes
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
    try:
        version = service.create_version(
            db, user, filename=file.filename if file else None, content_type=file.content_type if file else None,
            data=data, pasted_text=None if file else pasted_text, label=label, resume_id=resume_id,
        )
    except UploadRejected as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc
    except LookupError as exc:
        raise not_found("Resume") from exc
    return service.profile_payload(version)


def _version(db: DB, user, version_id: uuid.UUID) -> ResumeVersion:
    v = service.owned_version(db, user, version_id)
    if v is None:
        raise not_found("Resume version")
    return v


@router.get("/versions/{version_id}")
def get_profile(version_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    return service.profile_payload(_version(db, user, version_id))


class EntryPatch(BaseModel):
    kind: str | None = None
    title: str | None = Field(default=None, max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    date_precision: str | None = None
    degree: str | None = Field(default=None, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    excluded: bool | None = None


def _check_editable(v: ResumeVersion) -> None:
    if not v.evidence_eligible:
        raise HTTPException(409, "Tailored versions are read-only; edit the original profile instead.")


@router.patch("/versions/{version_id}/entries/{entry_id}")
def patch_entry(version_id: uuid.UUID, entry_id: uuid.UUID, body: EntryPatch, db: DB, user: CurrentUser) -> dict:
    v = _version(db, user, version_id)
    _check_editable(v)
    entry = db.get(ProfileEntry, entry_id)
    if entry is None or entry.version_id != v.id:
        raise not_found("Entry")
    try:
        service.update_entry(db, v, entry, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.refresh(v)
    return service.profile_payload(v)


class EntryCreate(BaseModel):
    kind: str = "employment"
    title: str = Field(default="", max_length=300)
    organization: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=200)
    employment_type: str = "unknown"
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    date_precision: str = "month"
    degree: str = ""
    field_of_study: str = ""
    bullets: list[str] = Field(default_factory=list, max_length=20)


@router.post("/versions/{version_id}/entries", status_code=201)
def create_entry(version_id: uuid.UUID, body: EntryCreate, db: DB, user: CurrentUser) -> dict:
    v = _version(db, user, version_id)
    _check_editable(v)
    try:
        service.add_entry(db, v, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.refresh(v)
    return service.profile_payload(v)


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=500)


@router.post("/versions/{version_id}/skills", status_code=201)
def create_skill(version_id: uuid.UUID, body: SkillCreate, db: DB, user: CurrentUser) -> dict:
    v = _version(db, user, version_id)
    _check_editable(v)
    service.add_skill(db, v, body.name, body.note)
    db.refresh(v)
    return service.profile_payload(v)


class SkillPatch(BaseModel):
    removed: bool


@router.patch("/versions/{version_id}/skills/{skill_id}")
def patch_skill(version_id: uuid.UUID, skill_id: uuid.UUID, body: SkillPatch, db: DB, user: CurrentUser) -> dict:
    v = _version(db, user, version_id)
    _check_editable(v)
    s = db.get(ProfileSkill, skill_id)
    if s is None or s.version_id != v.id:
        raise not_found("Skill")
    service.set_skill_removed(db, v, s, body.removed)
    db.refresh(v)
    return service.profile_payload(v)


@router.post("/versions/{version_id}/review")
def review_version(version_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    v = _version(db, user, version_id)
    try:
        service.mark_reviewed(db, user, v)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "active_version_id": str(v.id)}


@router.get("/versions/{version_id}/edits")
def edit_log(version_id: uuid.UUID, db: DB, user: CurrentUser) -> list[dict]:
    from app.models import ProfileEditLog

    v = _version(db, user, version_id)
    rows = db.scalars(select(ProfileEditLog).where(ProfileEditLog.version_id == v.id).order_by(ProfileEditLog.created_at)).all()
    return [{"id": str(r.id), "target_kind": r.target_kind, "target_id": str(r.target_id) if r.target_id else None,
             "action": r.action, "before": r.before, "after": r.after, "created_at": r.created_at.isoformat()} for r in rows]


@router.delete("/{resume_id}")
def delete_resume(resume_id: uuid.UUID, db: DB, user: CurrentUser) -> dict:
    r = db.get(Resume, resume_id)
    if r is None or r.user_id != user.id:
        raise not_found("Resume")
    files = service.delete_resume(db, user, r)
    return {"ok": True, "files_deleted": files}
