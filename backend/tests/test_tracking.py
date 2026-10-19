"""Application tracking: history, snapshots, resume versions, timezone-aware deadlines, analytics."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.tracking.service import TrackingError, to_utc
from tests.conftest import CSRF
from tests.helpers import job_by_fixture, ready_user


@pytest.fixture
def ready(client, db):
    ready_user(client, db)
    return client


def _save(client, job_id):
    r = client.post("/api/applications", json={"job_id": str(job_id)}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()


def test_viewing_a_job_or_its_link_never_marks_applied(ready, db):
    job = job_by_fixture(db, "fx-001")
    ready.get(f"/api/jobs/{job.id}")
    assert ready.get("/api/applications").json()["applications"] == []


def test_stage_history_is_append_only(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    for st in ("applied", "oa_received", "oa_completed", "interview", "rejected"):
        r = ready.post(f"/api/applications/{a['id']}/status", json={"status": st}, headers=CSRF)
        assert r.status_code == 200
    events = r.json()["events"]
    assert [e["to"] for e in events] == ["saved", "applied", "oa_received", "oa_completed", "interview", "rejected"]
    assert [e["from"] for e in events][1:] == ["saved", "applied", "oa_received", "oa_completed", "interview"]


def test_apply_records_resume_version_and_snapshot(ready, db):
    from app.models import Job
    from app.services.matching.enrich import enrich_job

    job = job_by_fixture(db, "fx-001")
    a = _save(ready, job.id)
    r = ready.post(f"/api/applications/{a['id']}/status", json={"status": "applied"}, headers=CSRF).json()
    active = ready.get("/api/resumes").json()["active_version_id"]
    assert r["resume_version_id"] == active and r["applied_at"] and r["has_snapshot"]
    snap = ready.get(f"/api/applications/{a['id']}").json()["job_snapshot"]
    original = snap["description_text"]

    # The employer edits the posting later; the snapshot must not change.
    j = db.get(Job, job.id)
    j.description_text = j.description_text + "\nNEW: 10+ years of Rust required."
    j.content_hash = "edited"
    enrich_job(j)
    db.commit()
    after = ready.get(f"/api/applications/{a['id']}").json()
    assert after["job_snapshot"]["description_text"] == original
    assert after["job_changed_since_application"] is True

    # Uploading a new resume version later does not change what was submitted.
    ready.post("/api/resumes/upload", data={"pasted_text": "New resume text " * 20}, headers=CSRF)
    assert ready.get(f"/api/applications/{a['id']}").json()["resume_version_id"] == active


def test_status_independent_of_source_listing(ready, db):
    job = job_by_fixture(db, "fx-017")
    a = _save(ready, job.id)
    ready.post(f"/api/applications/{a['id']}/status", json={"status": "applied"}, headers=CSRF)
    job.status = "closed"
    db.commit()
    got = ready.get(f"/api/applications/{a['id']}").json()
    assert got["status"] == "applied" and got["source_listing_status"] == "closed"


def test_rejects_future_dated_stage_and_unknown_status(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert ready.post(f"/api/applications/{a['id']}/status", json={"status": "applied", "occurred_at": future}, headers=CSRF).status_code == 422
    assert ready.post(f"/api/applications/{a['id']}/status", json={"status": "ghosted"}, headers=CSRF).status_code == 422


def test_timezone_conversion_across_dst():
    # US DST ends Sunday 2026-11-01 at 02:00 local time.
    assert to_utc("2026-10-31T17:00", "America/Los_Angeles") == datetime(2026, 11, 1, 0, 0, tzinfo=UTC)   # PDT (UTC-7)
    assert to_utc("2026-11-02T17:00", "America/Los_Angeles") == datetime(2026, 11, 3, 1, 0, tzinfo=UTC)   # PST (UTC-8)
    assert to_utc("2026-11-02T17:00", "Asia/Kolkata") == datetime(2026, 11, 2, 11, 30, tzinfo=UTC)
    with pytest.raises(TrackingError):
        to_utc("2027-03-14T02:30", "America/Los_Angeles")  # does not exist (spring-forward gap)
    with pytest.raises(TrackingError):
        to_utc("2026-11-02T17:00", "Mars/Olympus_Mons")


def test_oa_deadline_reminders_and_display_timezone(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    ready.post(f"/api/applications/{a['id']}/status", json={"status": "oa_received"}, headers=CSRF)
    due = (datetime.now(UTC) + timedelta(days=2)).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    # Entered in New York time; the user's display timezone is Los Angeles.
    r = ready.post(f"/api/applications/{a['id']}/deadlines", json={"kind": "oa", "title": "HackerRank OA", "due_local": "2026-11-20T23:59", "tz": "America/New_York"}, headers=CSRF)
    assert r.status_code == 201
    d = r.json()["deadlines"][0]
    assert d["due_at_utc"] == "2026-11-21T04:59:00+00:00"
    assert d["due_local"].startswith("2026-11-20T20:59") and d["due_local"].endswith("-08:00")
    assert d["entered_tz"] == "America/New_York"
    # A deadline inside the reminder horizon shows up in reminders.
    ready.post(f"/api/applications/{a['id']}/deadlines", json={"kind": "interview", "title": "Onsite", "due_local": due, "tz": "UTC"}, headers=CSRF)
    rem = ready.get("/api/reminders").json()
    assert any(x["title"] == "Onsite" for x in rem["deadlines"]) and rem["timezone"] == "America/Los_Angeles"


def test_overdue_and_completion(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    past = (datetime.now(UTC) - timedelta(hours=3)).replace(tzinfo=None).isoformat(timespec="minutes")
    r = ready.post(f"/api/applications/{a['id']}/deadlines", json={"kind": "oa", "due_local": past, "tz": "UTC"}, headers=CSRF).json()
    d = r["deadlines"][0]
    assert d["overdue"] is True
    ready.post(f"/api/deadlines/{d['id']}/complete", headers=CSRF)
    assert ready.get("/api/reminders").json()["deadlines"] == []


def test_ics_export(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-023").id)
    ready.post(f"/api/applications/{a['id']}/deadlines", json={"kind": "oa", "title": "OA due", "due_local": "2026-11-20T17:00", "tz": "America/Los_Angeles"}, headers=CSRF)
    r = ready.get("/api/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VEVENT" in body and "DTSTART;TZID=America/Los_Angeles:20261120T170000" in body
    assert "Aster Institute" in body


def test_follow_up_dates(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    when = (datetime.now(UTC) + timedelta(days=1)).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    r = ready.patch(f"/api/applications/{a['id']}", json={"follow_up_local": when, "tz": "UTC", "notes": "Ping recruiter"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["notes"] == "Ping recruiter"
    assert ready.get("/api/reminders").json()["follow_ups"]


def test_analytics_treats_silence_as_pending(ready, db):
    ids = [job_by_fixture(db, f).id for f in ("fx-001", "fx-017", "fx-009")]
    apps = [_save(ready, i) for i in ids]
    for a in apps:
        ready.post(f"/api/applications/{a['id']}/status", json={"status": "applied"}, headers=CSRF)
    ready.post(f"/api/applications/{apps[0]['id']}/status", json={"status": "oa_received"}, headers=CSRF)
    ready.post(f"/api/applications/{apps[1]['id']}/status", json={"status": "rejected"}, headers=CSRF)
    an = ready.get("/api/analytics").json()
    c = an["counts"]
    assert c["submitted"] == 3 and c["reached_oa"] == 1 and c["rejected"] == 1 and c["pending_no_response"] == 1
    assert an["rates"]["denominator"] == 3
    assert any("Only 3" in w for w in an["warnings"]) and any("Selection bias" in w for w in an["warnings"])
    assert "probability" in an["note"]


def test_application_ownership(client, db):
    ready_user(client, db, email="a@example.com")
    a = _save(client, job_by_fixture(db, "fx-001").id)
    client.post("/api/auth/logout", headers=CSRF)
    ready_user(client, db, email="b@example.com")
    assert client.get(f"/api/applications/{a['id']}").status_code == 404
    assert client.post(f"/api/applications/{a['id']}/status", json={"status": "applied"}, headers=CSRF).status_code == 404
    assert client.delete(f"/api/applications/{a['id']}", headers=CSRF).status_code == 404
    assert client.get("/api/applications").json()["applications"] == []


def test_dashboard(ready, db):
    a = _save(ready, job_by_fixture(db, "fx-001").id)
    ready.post(f"/api/applications/{a['id']}/status", json={"status": "oa_received"}, headers=CSRF)
    d = ready.get("/api/dashboard").json()
    assert d["profile_ready"] and d["pipeline"]["oa_received"] == 1 and d["top_new_jobs"]


def test_account_deletion_removes_everything(client, db):
    from sqlalchemy import func, select

    from app.models import Application, ProfileSkill, ResumeVersion, User

    ready_user(client, db, email="gone@example.com")
    _save(client, job_by_fixture(db, "fx-001").id)
    assert client.post("/api/account/delete", json={"password": "wrong-password", "confirm": "DELETE"}, headers=CSRF).status_code == 403
    r = client.post("/api/account/delete", json={"password": "correct-horse-battery", "confirm": "DELETE"}, headers=CSRF)
    assert r.status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User).where(User.email == "gone@example.com")) == 0
    for model in (Application, ResumeVersion, ProfileSkill):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert client.get("/api/resumes").status_code == 401
