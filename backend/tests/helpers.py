"""Shared setup for matching / tracking tests."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Job, JobSource, User
from app.services.jobs.adapters.fixtures import FixtureAdapter
from app.services.jobs.ingest import ensure_source, run_source
from tests.conftest import CSRF, demo_resume_text, register


def seed_fixture_jobs(db) -> JobSource:
    src = db.scalar(select(JobSource).where(JobSource.kind == "fixture"))
    if src is None:
        src = ensure_source(db, "fixture", "fixtures", "Demo fixtures")
        db.commit()
        run_source(db, src, "fixtures-test", adapter=FixtureAdapter())
    return src


def ready_user(client, db, email="user@example.com", resume_text: str | None = None, prefs: dict | None = None, watch_fixtures=True) -> dict:
    """Register, upload + review a resume, set preferences, follow the fixture source."""
    register(client, email)
    r = client.post("/api/resumes/upload", data={"pasted_text": resume_text or demo_resume_text()}, headers=CSRF)
    assert r.status_code == 201, r.text
    profile = r.json()
    assert client.post(f"/api/resumes/versions/{profile['version']['id']}/review", headers=CSRF).status_code == 200
    body = {"role_families": [], "locations": [], "remote_modes": [], "experience_levels": [], "employment_types": [],
            "excluded_employers": [], "excluded_keywords": [], "hard_filters": {}}
    body.update(prefs or {})
    assert client.put("/api/preferences", json=body, headers=CSRF).status_code == 200, "prefs"
    src = seed_fixture_jobs(db)
    if watch_fixtures:
        assert client.post(f"/api/sources/{src.id}/watch", headers=CSRF).status_code == 200
    return profile


def job_by_fixture(db, fid: str) -> Job:
    return db.scalar(select(Job).where(Job.source_job_id == fid))


def user_by_email(db, email: str) -> User:
    return db.scalar(select(User).where(User.email == email))
