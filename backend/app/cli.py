"""RoleMatch command line.

    python -m app.cli migrate          # alembic upgrade head
    python -m app.cli seed             # watchlist + demo fixtures + demo account (idempotent)
    python -m app.cli demo-reset       # delete and recreate the demo account and refresh fixture dates
    python -m app.cli sync-watchlist   # load config/watchlist.yaml into the database
    python -m app.cli ingest --all     # fetch every enabled watchlist source now (synchronously)
    python -m app.cli rank --email demo@rolematch.local
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

import yaml
from sqlalchemy import select

from app.core.config import REPO_ROOT, get_settings
from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import (
    Application,
    ApplicationEvent,
    Deadline,
    JobSource,
    Preferences,
    User,
    UserWatch,
    WorkEligibility,
)

DEMO_EMAIL = "demo@rolematch.local"
DEMO_PASSWORD = "demo-password-123"  # local demo only; documented in README


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    command.upgrade(cfg, "head")


def sync_watchlist(db) -> list[JobSource]:
    from app.services.jobs.ingest import ensure_source

    data = yaml.safe_load((get_settings().config_dir / "watchlist.yaml").read_text()) or {}
    out = []
    for entry in data.get("sources") or []:
        src = ensure_source(db, entry["kind"], str(entry["identifier"]), entry.get("employer_name") or entry["identifier"],
                            entry.get("region", "us"), from_config=True)
        src.enabled = bool(entry.get("enabled", True))
        out.append(src)
    db.commit()
    return out


def ensure_fixture_source(db, ingest: bool = True) -> JobSource:
    from app.services.jobs.adapters.fixtures import FixtureAdapter
    from app.services.jobs.ingest import ensure_source, run_source

    src = ensure_source(db, "fixture", "fixtures", "Demo fixtures (synthetic)")
    db.commit()
    if ingest:
        run_source(db, src, f"fixtures-{datetime.now(UTC).isoformat()}", adapter=FixtureAdapter())
    return src


def create_demo_user(db, fixture_source: JobSource) -> User:
    from app.services.resume.service import create_version, mark_reviewed

    user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), display_name="Demo (synthetic) user",
                timezone="America/Los_Angeles", is_demo=True)
    db.add(user)
    db.flush()
    db.add(Preferences(
        user_id=user.id, hard_filters={"sponsorship": True, "citizenship": True},
        role_families=["software_engineer", "backend_engineer", "ml_engineer", "data_engineer"],
        locations=["Los Angeles, CA", "United States"], remote_modes=["remote", "hybrid", "onsite"],
        experience_levels=["entry", "mid"], employment_types=["full_time"], excluded_employers=[], excluded_keywords=[],
        feedback_adjustments={},
    ))
    db.add(WorkEligibility(user_id=user.id, needs_sponsorship=True, authorization_note="Synthetic demo value"))
    db.add(UserWatch(user_id=user.id, source_id=fixture_source.id))
    db.commit()
    text = (get_settings().fixtures_dir / "resumes" / "demo_jordan_lee.txt").read_text()
    v = create_version(db, user, filename=None, content_type=None, data=None, pasted_text=text, label="Demo resume (synthetic)")
    mark_reviewed(db, user, v)
    _demo_applications(db, user, v.id)
    return user


def _demo_applications(db, user: User, version_id) -> None:
    """A few synthetic pipeline records so the tracker and dashboard are not empty in the demo."""
    from app.models import Job
    from app.services.tracking.service import job_snapshot

    now = datetime.now(UTC)
    plan = [("fx-023", "oa_received", 6), ("fx-001", "applied", 3), ("fx-009", "saved", None)]
    for source_job_id, status, days_ago in plan:
        job = db.scalar(select(Job).where(Job.source_job_id == source_job_id, Job.is_fixture.is_(True)))
        if job is None:
            continue
        applied = now - timedelta(days=days_ago) if days_ago is not None else None
        app_ = Application(user_id=user.id, job_id=job.id, status=status, employer=job.employer, title=job.title,
                           external_url=job.apply_url, applied_at=applied, resume_version_id=version_id if applied else None,
                           job_snapshot=job_snapshot(job, applied) if applied else None, notes="Synthetic demo record.")
        db.add(app_)
        db.flush()
        db.add(ApplicationEvent(application_id=app_.id, from_status=None, to_status="saved", occurred_at=(applied or now) - timedelta(days=1)))
        if applied:
            db.add(ApplicationEvent(application_id=app_.id, from_status="saved", to_status="applied", occurred_at=applied))
        if status == "oa_received":
            db.add(ApplicationEvent(application_id=app_.id, from_status="applied", to_status="oa_received", occurred_at=now - timedelta(days=1)))
            db.add(Deadline(application_id=app_.id, user_id=user.id, kind="oa", title="Online assessment due",
                            due_at=now + timedelta(days=2), tz_name="America/Los_Angeles"))
    db.commit()


def seed() -> None:
    db = get_sessionmaker()()
    try:
        sync_watchlist(db)
        src = ensure_fixture_source(db, ingest=True)
        if db.scalar(select(User).where(User.email == DEMO_EMAIL)) is None:
            create_demo_user(db, src)
        print(f"Seeded. Demo account: {DEMO_EMAIL} / {DEMO_PASSWORD} (local demo only)")
    finally:
        db.close()


def demo_reset() -> None:
    from app.services.account import delete_user_data

    db = get_sessionmaker()()
    try:
        u = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if u is not None:
            if not u.is_demo:
                sys.exit("Refusing to reset: that account is not flagged as a demo account.")
            delete_user_data(db, u)
        src = ensure_fixture_source(db, ingest=True)
        create_demo_user(db, src)
        print("Demo account reset.")
    finally:
        db.close()


def ingest(all_sources: bool, source_id: str | None) -> None:
    from app.services.jobs.ingest import run_source

    db = get_sessionmaker()()
    try:
        q = select(JobSource).where(JobSource.enabled.is_(True), JobSource.kind.in_(["greenhouse", "lever"]))
        if source_id:
            q = q.where(JobSource.id == source_id)
        elif not all_sources:
            sys.exit("Pass --all or --source-id")
        for src in db.scalars(q).all():
            run = run_source(db, src)
            print(f"{src.kind}:{src.identifier} -> {run.status} seen={run.jobs_seen} new={run.jobs_new} "
                  f"updated={run.jobs_updated} closed={run.jobs_closed} {run.error_kind or ''} {run.error_detail or ''}")
    finally:
        db.close()


def rank(email: str, limit: int) -> None:
    from app.services.matching.engine import rank_for_user

    db = get_sessionmaker()()
    try:
        user = db.scalar(select(User).where(User.email == email))
        out = rank_for_user(db, user)
        rows = sorted(out["results"], key=lambda x: (not x[1]["eligible"], -x[1]["scores"]["application_priority"]))
        for job, res in rows[:limit]:
            s = res["scores"]
            flag = "ok " if res["eligible"] else "EXCLUDED " + ",".join(res["eligibility"]["excluded_by"])
            print(f"{s['application_priority']:5.1f} prio | {s['qualification_match']:5.1f} qual | {job.employer[:22]:22} | {job.title[:44]:44} | {flag}")
        print(out["stats"])
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rolematch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("seed")
    sub.add_parser("demo-reset")
    sub.add_parser("sync-watchlist")
    pi = sub.add_parser("ingest")
    pi.add_argument("--all", action="store_true")
    pi.add_argument("--source-id")
    pr = sub.add_parser("rank")
    pr.add_argument("--email", default=DEMO_EMAIL)
    pr.add_argument("--limit", type=int, default=40)
    a = p.parse_args(argv)
    if a.cmd == "migrate":
        migrate()
    elif a.cmd == "seed":
        seed()
    elif a.cmd == "demo-reset":
        demo_reset()
    elif a.cmd == "sync-watchlist":
        db = get_sessionmaker()()
        print(f"{len(sync_watchlist(db))} watchlist entries synced")
        db.close()
    elif a.cmd == "ingest":
        ingest(a.all, a.source_id)
    elif a.cmd == "rank":
        rank(a.email, a.limit)


if __name__ == "__main__":
    main()
