"""Celery tasks run eagerly in tests; repeated delivery of the same task id must not double-ingest."""

from sqlalchemy import func, select

from app.models import Job, SourceRun
from app.services.jobs.ingest import ensure_source


def test_ingest_task_is_idempotent_under_redelivery(db):
    from app.workers.tasks import ingest_source

    src = ensure_source(db, "fixture", "fixtures", "Demo fixtures")
    db.commit()
    r1 = ingest_source.apply(args=[str(src.id)], task_id="task-123").get()
    r2 = ingest_source.apply(args=[str(src.id)], task_id="task-123").get()  # re-delivered
    assert r1["status"] == "success" and r1["new"] == 36
    assert r2["status"] == "success" and r2["new"] == 36  # same recorded run returned, not re-executed
    assert db.scalar(select(func.count()).select_from(SourceRun)) == 1
    assert db.scalar(select(func.count()).select_from(Job)) == 36


def test_disabled_source_is_skipped(db):
    from app.workers.tasks import ingest_source

    src = ensure_source(db, "fixture", "fixtures", "Demo fixtures")
    src.enabled = False
    db.commit()
    assert "skipped" in ingest_source.apply(args=[str(src.id)]).get()
