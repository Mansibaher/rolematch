import pytest

from app.services.tailoring import check_claims
from tests.conftest import CSRF
from tests.helpers import job_by_fixture, ready_user, user_by_email


@pytest.mark.parametrize(
    ("source", "suggestion", "ok"),
    [
        ("Built REST APIs in Python with FastAPI", "Built REST APIs in Python (FastAPI)", True),
        ("Built services with FastAPI", "Built services with FastAPI (Python)", True),  # implied by FastAPI
        ("Built services with FastAPI", "Built services with FastAPI and Kubernetes", False),  # new skill
        ("Reduced latency from 420 ms to 180 ms", "Reduced latency by 70%", False),  # new metric
        ("Reduced latency from 420 ms to 180 ms", "Cut latency from 420 ms to 180 ms", True),
        ("Built an internal dashboard", "Built an internal dashboard at Google", False),  # new employer name
        ("Worked with Postgres", "Worked with PostgreSQL", True),  # same skill, posting's term
    ],
)
def test_claim_checker(source, suggestion, ok):
    assert check_claims(source, suggestion).ok is ok


@pytest.fixture
def ready(client, db):
    ready_user(client, db)
    return client


def test_suggestions_are_grounded(ready, db):
    job = job_by_fixture(db, "fx-001")
    data = ready.get(f"/api/tailoring/{job.id}").json()
    assert data["suggestions"]
    raw = ready.get(f"/api/resumes/versions/{data['base_version_id']}").json()["raw_text"]
    for s in data["suggestions"]:
        assert s["source_text"] in raw, "every suggestion shows its literal source line"
        assert check_claims(s["source_text"], s["suggested_text"]).ok
    unsupported = {u["requirement"] for u in data["unsupported_requirements"]}
    assert "Familiarity with Kafka" in unsupported


def test_save_creates_tailored_copy_that_is_never_evidence(ready, db):
    job = job_by_fixture(db, "fx-001")
    before_versions = ready.get("/api/resumes").json()
    active = before_versions["active_version_id"]
    original = ready.get(f"/api/resumes/versions/{active}").json()["raw_text"]
    before_score = ready.get(f"/api/jobs/{job.id}").json()["match"]["scores"]["qualification_match"]
    sug = ready.get(f"/api/tailoring/{job.id}").json()["suggestions"]
    r = ready.post(f"/api/tailoring/{job.id}/save", json={"accepted": [{"id": s["id"]} for s in sug[:3]]}, headers=CSRF)
    assert r.status_code == 201 and r.json()["evidence_eligible"] is False
    # Original unchanged and still active; matching unchanged.
    assert ready.get("/api/resumes").json()["active_version_id"] == active
    assert ready.get(f"/api/resumes/versions/{active}").json()["raw_text"] == original
    assert ready.get(f"/api/jobs/{job.id}").json()["match"]["scores"]["qualification_match"] == before_score
    # The tailored copy cannot become the matching profile or be edited.
    vid = r.json()["version_id"]
    assert ready.post(f"/api/resumes/versions/{vid}/review", headers=CSRF).status_code == 409
    assert ready.post(f"/api/resumes/versions/{vid}/skills", json={"name": "Rust"}, headers=CSRF).status_code == 409


def test_user_edit_that_fabricates_is_rejected(ready, db):
    job = job_by_fixture(db, "fx-001")
    s = ready.get(f"/api/tailoring/{job.id}").json()["suggestions"][0]
    bad = s["source_text"] + " Led a team of 12 engineers using Kubernetes."
    r = ready.post(f"/api/tailoring/{job.id}/save", json={"accepted": [{"id": s["id"], "text": bad}]}, headers=CSRF)
    assert r.status_code == 422
    assert "adds" in r.json()["detail"]


def test_optional_llm_rewrites_are_checked(ready, db):
    from app.services import tailoring

    class FakeProvider:
        name = "fake"
        seen = ""

        def complete(self, system, user):
            FakeProvider.seen = user
            import json

            bullets = json.loads(user)["bullets"]
            return json.dumps({"rewrites": [
                {"id": bullets[0]["id"], "text": bullets[0]["text"] + " across 50 microservices on Kubernetes"},  # fabricated
                {"id": bullets[1]["id"], "text": bullets[1]["text"].rstrip(".")},  # harmless
            ]})

    user = user_by_email(db, "user@example.com")
    user.llm_opt_in = True
    db.commit()
    out = tailoring.suggest(db, user, job_by_fixture(db, "fx-001"), provider_override=FakeProvider())
    assert "discarded" in out["llm_note"] and "1 were discarded" in out["llm_note"]
    alts = [a for s in out["suggestions"] for a in s["alternatives"]]
    assert len(alts) == 1 and "Kubernetes" not in alts[0]["text"]
    # Only bullets and requirements are sent — no contact details.
    assert "example.com" not in FakeProvider.seen and "555" not in FakeProvider.seen


def test_llm_not_used_without_opt_in(ready, db):
    from app.services import tailoring

    class Boom:
        name = "boom"

        def complete(self, system, user):
            raise AssertionError("must not be called")

    user = user_by_email(db, "user@example.com")
    out = tailoring.suggest(db, user, job_by_fixture(db, "fx-001"), provider_override=Boom())
    assert out["llm_note"] is None
