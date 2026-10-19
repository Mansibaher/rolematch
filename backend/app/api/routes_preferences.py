from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser
from app.models import Preferences, WorkEligibility
from app.services.matching.eligibility import ALWAYS_HARD, CHECK_LABELS, DEFAULT_HARD
from app.services.matching.requirements import role_families

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

REMOTE_MODES = {"remote", "hybrid", "onsite"}
LEVELS = {"intern", "entry", "mid", "senior", "staff"}
EMP_TYPES = {"full_time", "part_time", "contract", "internship"}
HARD_CAPABLE = {"role_family", "location", "work_mode", "employment_type", "experience_level", "salary", "sponsorship", "citizenship"}


def _payload(p: Preferences, e: WorkEligibility | None) -> dict:
    hard = {k: bool((p.hard_filters or {}).get(k, DEFAULT_HARD.get(k, False))) for k in HARD_CAPABLE}
    return {
        "role_families": p.role_families, "locations": p.locations, "remote_modes": p.remote_modes,
        "experience_levels": p.experience_levels, "employment_types": p.employment_types,
        "min_salary": p.min_salary, "salary_currency": p.salary_currency,
        "excluded_employers": p.excluded_employers, "excluded_keywords": p.excluded_keywords,
        "hard_filters": hard, "always_hard": sorted(ALWAYS_HARD),
        "active_resume_version_id": str(p.active_resume_version_id) if p.active_resume_version_id else None,
        "feedback_adjustments": p.feedback_adjustments,
        "eligibility": {
            "needs_sponsorship": e.needs_sponsorship if e else None,
            "authorization_note": e.authorization_note if e else "",
            "exclude_citizenship_required": e.exclude_citizenship_required if e else False,
        },
        "options": {
            "role_families": [{"id": f["id"], "name": f["name"]} for f in role_families()],
            "remote_modes": sorted(REMOTE_MODES), "experience_levels": ["intern", "entry", "mid", "senior", "staff"],
            "employment_types": sorted(EMP_TYPES), "check_labels": CHECK_LABELS,
        },
    }


@router.get("")
def get_prefs(db: DB, user: CurrentUser) -> dict:
    p = db.get(Preferences, user.id)
    if p is None:
        p = Preferences(user_id=user.id, hard_filters={}, feedback_adjustments={})
        db.add(p)
        db.commit()
    return _payload(p, db.get(WorkEligibility, user.id))


class EligibilityBody(BaseModel):
    needs_sponsorship: bool | None = None
    authorization_note: str = Field(default="", max_length=500)
    exclude_citizenship_required: bool = False


class PrefsBody(BaseModel):
    role_families: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=20)
    remote_modes: list[str] = Field(default_factory=list)
    experience_levels: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    min_salary: int | None = Field(default=None, ge=0, le=10_000_000)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    excluded_employers: list[str] = Field(default_factory=list, max_length=100)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=100)
    hard_filters: dict[str, bool] = Field(default_factory=dict)
    eligibility: EligibilityBody | None = None
    reset_feedback: bool = False


@router.put("")
def put_prefs(body: PrefsBody, db: DB, user: CurrentUser) -> dict:
    known = {f["id"] for f in role_families()}
    for name, allowed, values in (("role_families", known, body.role_families), ("remote_modes", REMOTE_MODES, body.remote_modes),
                                  ("experience_levels", LEVELS, body.experience_levels), ("employment_types", EMP_TYPES, body.employment_types)):
        bad = set(values) - allowed
        if bad:
            raise HTTPException(422, f"Unknown {name}: {sorted(bad)}")
    bad = set(body.hard_filters) - HARD_CAPABLE
    if bad:
        raise HTTPException(422, f"These filters cannot be set: {sorted(bad)}")
    p = db.get(Preferences, user.id) or Preferences(user_id=user.id, hard_filters={}, feedback_adjustments={})
    db.add(p)
    clean = lambda xs: [x.strip() for x in xs if x and x.strip()]  # noqa: E731
    p.role_families, p.locations = body.role_families, clean(body.locations)
    p.remote_modes, p.experience_levels, p.employment_types = body.remote_modes, body.experience_levels, body.employment_types
    p.min_salary, p.salary_currency = body.min_salary, (body.salary_currency.upper() if body.salary_currency else None)
    p.excluded_employers, p.excluded_keywords = clean(body.excluded_employers), clean(body.excluded_keywords)
    p.hard_filters = body.hard_filters
    if body.reset_feedback:
        p.feedback_adjustments = {}
    p.updated_at = datetime.now(UTC)
    if body.eligibility is not None:
        e = db.get(WorkEligibility, user.id) or WorkEligibility(user_id=user.id)
        db.add(e)
        e.needs_sponsorship = body.eligibility.needs_sponsorship
        e.authorization_note = body.eligibility.authorization_note
        e.exclude_citizenship_required = body.eligibility.exclude_citizenship_required
        e.updated_at = datetime.now(UTC)
    db.commit()
    return _payload(p, db.get(WorkEligibility, user.id))
