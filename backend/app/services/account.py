"""Account-level data deletion."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job, JobSource, StoredFile, User


def delete_user_data(db: Session, user: User) -> dict:
    """Delete the user and everything they own: files on disk, resumes, profile, applications, manual jobs.

    Public jobs fetched from employer boards are shared data and are not deleted.
    """
    settings = get_settings()
    files = db.scalars(select(StoredFile).where(StoredFile.user_id == user.id)).all()
    removed = 0
    for f in files:
        p = settings.upload_dir / f.storage_key
        if p.exists():
            p.unlink()
            removed += 1
    user_dir = settings.upload_dir / str(user.id)
    if user_dir.exists() and not any(user_dir.iterdir()):
        user_dir.rmdir()
    manual_jobs = db.execute(delete(Job).where(Job.owner_user_id == user.id).returning(Job.id)).all()
    db.execute(delete(JobSource).where(JobSource.kind == "manual", JobSource.identifier == f"user-{user.id}"))
    db.delete(user)  # cascades: sessions, preferences, eligibility, resumes, versions, profile, applications, feedback, matches
    db.commit()
    return {"files_deleted": removed, "manual_jobs_deleted": len(manual_jobs)}
