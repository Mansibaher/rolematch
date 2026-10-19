from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("rolematch", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # re-deliver if a worker dies mid-task; tasks are idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_eager,
    task_eager_propagates=True,
    task_routes={"app.workers.tasks.ingest_source": {"queue": "ingest"}},
    timezone="UTC",
    beat_schedule={
        # Every 6 hours, at a minute offset so many installs don't all hit sources on the hour.
        "ingest-all-sources": {"task": "app.workers.tasks.ingest_all", "schedule": crontab(minute=17, hour="*/6")},
    },
)
