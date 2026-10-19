"""Source adapters and ingestion reliability, using schema-faithful API fixtures (see fixtures/recorded/README.md)."""

import json
import socket
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Job, SourceRun
from app.services.jobs import http as http_mod
from app.services.jobs.adapters.fixtures import FixtureAdapter
from app.services.jobs.adapters.greenhouse import GreenhouseAdapter
from app.services.jobs.adapters.lever import LeverAdapter
from app.services.jobs.http import SourceError
from app.services.jobs.ingest import dedupe, ensure_source, run_source, upsert_jobs
from app.services.jobs.normalize import NormalizedJob, finish
from app.services.url_safety import UnsafeURL, assert_public_http_url, canonicalize_url, parse_supported_posting
from tests.conftest import REPO

REC = REPO / "fixtures" / "recorded"
GH_LIST = "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs"
LV_LIST = "https://api.lever.co/v0/postings/exampleco"


def load(name):
    return json.loads((REC / name).read_text())


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(get_settings(), "http_min_interval_seconds", 0.0)
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)


# ---------------------------------------------------------------- adapters


@respx.mock
def test_greenhouse_list_normalizes_documented_fields():
    respx.get(GH_LIST).mock(return_value=httpx.Response(200, json=load("greenhouse_list_examplecorp.json")))
    res = GreenhouseAdapter().fetch_all("examplecorp", "ExampleCorp")
    assert res.complete and len(res.jobs) == 3
    j = res.jobs[0]
    assert j.source_job_id == "4001001" and j.title == "Backend Engineer"
    assert "• 3+ years of experience with Python" in j.description_text  # escaped HTML decoded, lists kept
    assert j.posted_at is not None and j.posted_at_basis == "Greenhouse first_published"
    assert j.source_updated_at is not None
    assert j.salary and j.salary.min == 140000 and j.salary.basis.startswith("posting text")
    # Job without first_published: posting date unknown, never substituted with updated_at.
    j2 = res.jobs[1]
    assert j2.posted_at is None and j2.source_updated_at is not None
    assert j2.remote_type == "remote" and j2.remote_restriction == "US"


@respx.mock
def test_greenhouse_total_mismatch_marks_listing_incomplete():
    data = load("greenhouse_list_examplecorp.json")
    data["meta"]["total"] = 10
    respx.get(GH_LIST).mock(return_value=httpx.Response(200, json=data))
    assert GreenhouseAdapter().fetch_all("examplecorp", "ExampleCorp").complete is False


@respx.mock
def test_greenhouse_single_uses_pay_transparency():
    route = respx.get(f"{GH_LIST}/4001001").mock(return_value=httpx.Response(200, json=load("greenhouse_single_examplecorp.json")))
    j = GreenhouseAdapter().fetch_one("examplecorp", "4001001", "ExampleCorp")
    assert route.calls[0].request.url.params["pay_transparency"] == "true"
    assert j.salary.min == 140000 and j.salary.basis.startswith("Greenhouse pay range")
    assert "questions" not in (j.raw_payload or {})


@respx.mock
def test_lever_paginates_and_maps_fields():
    route = respx.get(LV_LIST)
    route.side_effect = [
        httpx.Response(200, json=load("lever_page1_exampleco.json")),
        httpx.Response(200, json=load("lever_page2_exampleco.json")),
    ]
    res = LeverAdapter().fetch_all("exampleco", "Exampleco")
    assert res.complete and res.pages == 2 and len(res.jobs) == 107
    assert [c.request.url.params["skip"] for c in route.calls] == ["0", "100"]
    j = res.jobs[0]
    assert j.employment_type == "full_time" and j.employment_type_basis == "source field"
    assert j.remote_type == "hybrid"
    assert j.salary.interval == "year" and j.salary.basis == "Lever salaryRange"
    assert "Requirements\n• 2+ years of Go or Java" in j.description_text
    assert j.posted_at_basis.startswith("Lever createdAt")
    assert res.jobs[1].salary is None  # no salary when the source doesn't supply one


@respx.mock
def test_lever_page_cap_marks_incomplete(monkeypatch):
    monkeypatch.setattr(get_settings(), "lever_max_pages", 1)
    respx.get(LV_LIST).mock(return_value=httpx.Response(200, json=load("lever_page1_exampleco.json")))
    res = LeverAdapter().fetch_all("exampleco", "Exampleco")
    assert res.complete is False and res.notes


@respx.mock
def test_retries_transient_errors_then_succeeds():
    route = respx.get(GH_LIST)
    route.side_effect = [httpx.Response(503), httpx.ConnectTimeout("t"), httpx.Response(200, json=load("greenhouse_list_examplecorp.json"))]
    res = GreenhouseAdapter().fetch_all("examplecorp", "ExampleCorp")
    assert len(res.jobs) == 3 and res.counter.requests == 3


@respx.mock
def test_retries_are_bounded():
    route = respx.get(GH_LIST).mock(return_value=httpx.Response(500))
    with pytest.raises(SourceError) as e:
        GreenhouseAdapter().fetch_all("examplecorp", "ExampleCorp")
    assert e.value.kind == "server_error"
    assert route.call_count == get_settings().http_max_retries + 1


@respx.mock
def test_html_instead_of_json_is_retried():
    route = respx.get(LV_LIST)
    route.side_effect = [httpx.Response(200, text="<html>oops</html>", headers={"content-type": "text/html"}),
                         httpx.Response(200, json=load("lever_page2_exampleco.json"))]
    assert len(LeverAdapter().fetch_all("exampleco", "Exampleco").jobs) == 7


@respx.mock
def test_404_fails_fast_without_retry():
    route = respx.get(GH_LIST).mock(return_value=httpx.Response(404))
    with pytest.raises(SourceError) as e:
        GreenhouseAdapter().fetch_all("examplecorp", "ExampleCorp")
    assert e.value.kind == "not_found" and route.call_count == 1


def test_invalid_identifiers_rejected_before_any_request():
    with pytest.raises(SourceError):
        GreenhouseAdapter().fetch_all("../../etc", "x")
    with pytest.raises(SourceError):
        LeverAdapter().fetch_all("a b", "x")


# ---------------------------------------------------------------- ingestion


def _gh_source(db):
    s = ensure_source(db, "greenhouse", "examplecorp", "ExampleCorp")
    db.commit()
    return s


@respx.mock
def test_ingestion_is_idempotent(db):
    respx.get(GH_LIST).mock(return_value=httpx.Response(200, json=load("greenhouse_list_examplecorp.json")))
    src = _gh_source(db)
    r1 = run_source(db, src, "run-1")
    first_seen = {j.source_job_id: j.first_seen_at for j in db.scalars(select(Job))}
    r2 = run_source(db, src, "run-2")
    assert (r1.jobs_new, r2.jobs_new, r2.jobs_updated) == (3, 0, 0)
    assert db.scalar(select(func.count()).select_from(Job)) == 3
    db.expire_all()
    assert {j.source_job_id: j.first_seen_at for j in db.scalars(select(Job))} == first_seen


@respx.mock
def test_redelivered_task_does_not_run_twice(db):
    route = respx.get(GH_LIST).mock(return_value=httpx.Response(200, json=load("greenhouse_list_examplecorp.json")))
    src = _gh_source(db)
    run_source(db, src, "celery-task-abc")
    run_source(db, src, "celery-task-abc")
    assert route.call_count == 1
    assert db.scalar(select(func.count()).select_from(SourceRun)) == 1


@respx.mock
def test_outage_never_closes_jobs(db):
    route = respx.get(GH_LIST)
    route.side_effect = [httpx.Response(200, json=load("greenhouse_list_examplecorp.json"))] + [httpx.Response(503)] * 20
    src = _gh_source(db)
    run_source(db, src, "ok")
    for i in range(3):
        r = run_source(db, src, f"down-{i}")
        assert r.status == "failed" and r.error_kind == "server_error"
    db.expire_all()
    assert {j.status for j in db.scalars(select(Job))} == {"active"}
    assert all(j.consecutive_misses == 0 for j in db.scalars(select(Job)))
    assert src.consecutive_failures == 3


@respx.mock
def test_empty_but_successful_listing_closes_after_two_misses_and_reopens(db):
    data = load("greenhouse_list_examplecorp.json")
    shrunk = {"jobs": data["jobs"][:1], "meta": {"total": 1}}
    route = respx.get(GH_LIST)
    route.side_effect = [httpx.Response(200, json=data), httpx.Response(200, json=shrunk), httpx.Response(200, json=shrunk), httpx.Response(200, json=data)]
    src = _gh_source(db)
    run_source(db, src, "a")
    run_source(db, src, "b")
    db.expire_all()
    assert sorted(j.status for j in db.scalars(select(Job))) == ["active", "active", "active"]  # one miss: not yet closed
    r = run_source(db, src, "c")
    assert r.jobs_closed == 2
    db.expire_all()
    assert sorted(j.status for j in db.scalars(select(Job))) == ["active", "closed", "closed"]
    run_source(db, src, "d")  # the jobs reappear
    db.expire_all()
    assert {j.status for j in db.scalars(select(Job))} == {"active"}


@respx.mock
def test_partial_listing_does_not_close(db):
    data = load("greenhouse_list_examplecorp.json")
    partial = {"jobs": data["jobs"][:1], "meta": {"total": 3}}
    route = respx.get(GH_LIST)
    route.side_effect = [httpx.Response(200, json=data)] + [httpx.Response(200, json=partial)] * 3
    src = _gh_source(db)
    for k in "abcd":
        run_source(db, src, k)
    db.expire_all()
    assert {j.status for j in db.scalars(select(Job))} == {"active"}


def test_fixture_source_marks_jobs_as_fixtures(db):
    src = ensure_source(db, "fixture", "fixtures", "Demo fixtures")
    db.commit()
    run = run_source(db, src, "fx", adapter=FixtureAdapter())
    assert run.status == "success" and run.jobs_new == 36
    jobs = db.scalars(select(Job)).all()
    assert all(j.is_fixture for j in jobs)
    nodate = next(j for j in jobs if j.source_job_id == "fx-014")
    assert nodate.posted_at is None and nodate.first_seen_at is not None
    j = next(j for j in jobs if j.source_job_id == "fx-001")
    assert j.extraction and j.embedding is not None


def _nj(sid, title="Backend Engineer", loc="Austin, TX", url=None, req=None, desc="Build services in Python. " * 20):
    return finish(NormalizedJob(source_job_id=sid, employer="ExampleCorp", title=title, description_text=desc,
                                location_raw=loc, apply_url=url, canonical_url=canonicalize_url(url), requisition_id=req))


def test_dedup_is_conservative(db):
    a = ensure_source(db, "greenhouse", "examplecorp", "ExampleCorp")
    b = ensure_source(db, "lever", "examplecorp", "ExampleCorp")
    db.flush()
    now = datetime.now(UTC)
    ids_a, _, _ = upsert_jobs(db, a, [_nj("1", url="https://Example.com/jobs/1?utm_source=x"), _nj("2", loc="Denver, CO"), _nj("3", req="R-1")], now)
    db.commit()
    ids_b, _, _ = upsert_jobs(db, b, [
        _nj("x1", url="https://example.com/jobs/1/"),            # same canonical URL -> duplicate
        _nj("x2", loc="Denver, CO"),                              # same employer/title/location/description -> duplicate
        _nj("x3", req="R-2"),                                     # different requisition id -> never merged
        _nj("x4", title="Backend Engineer", loc="Boston, MA"),    # different location -> distinct
    ], now)
    db.commit()
    assert dedupe(db, ids_b) == 2
    db.commit()
    by_id = {j.source_job_id: j for j in db.scalars(select(Job))}
    assert by_id["x1"].duplicate_of_id == by_id["1"].id and by_id["x1"].duplicate_reason == "same canonical URL"
    assert by_id["x2"].duplicate_of_id == by_id["2"].id
    assert by_id["x3"].duplicate_of_id is None and by_id["x4"].duplicate_of_id is None
    # Same-source rows are never merged with each other.
    assert dedupe(db, ids_a) == 0


# ---------------------------------------------------------------- URL safety


def test_supported_posting_urls():
    assert parse_supported_posting("https://boards.greenhouse.io/examplecorp/jobs/4001001?gh_src=x").job_id == "4001001"
    assert parse_supported_posting("https://job-boards.greenhouse.io/examplecorp/jobs/4001001").kind == "greenhouse"
    lv = parse_supported_posting("https://jobs.eu.lever.co/exampleco/0000aaaa-bbbb-4ccc-8ddd-000000000000")
    assert lv.kind == "lever" and lv.region == "eu"
    assert parse_supported_posting("https://www.linkedin.com/jobs/view/123") is None
    assert parse_supported_posting("https://boards.greenhouse.io/../jobs/1") is None
    assert parse_supported_posting("file:///etc/passwd") is None


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "100.64.0.1", "0.0.0.0", "::ffff:127.0.0.1"])
def test_ssrf_guard_blocks_private_addresses(ip):
    fake = lambda host, port, proto=0: [(socket.AF_INET6 if ":" in ip else socket.AF_INET, 1, 6, "", (ip, port))]  # noqa: E731
    with pytest.raises(UnsafeURL):
        assert_public_http_url("https://internal.example.com/", resolver=fake)


def test_ssrf_guard_rules():
    public = lambda host, port, proto=0: [(socket.AF_INET, 1, 6, "", ("93.184.215.14", port))]  # noqa: E731
    assert assert_public_http_url("https://example.com/x", resolver=public)
    for bad in ("ftp://example.com", "https://user:pw@example.com/", "gopher://x"):
        with pytest.raises(UnsafeURL):
            assert_public_http_url(bad, resolver=public)
