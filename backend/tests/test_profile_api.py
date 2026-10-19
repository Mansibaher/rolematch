from tests.conftest import CSRF, REPO, register

RES = REPO / "fixtures" / "resumes"


def upload_demo(client, name="demo_jordan_lee.docx"):
    ctype = "application/pdf" if name.endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = client.post("/api/resumes/upload", files={"file": (name, (RES / name).read_bytes(), ctype)}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()


def test_requires_auth_and_csrf(client):
    assert client.get("/api/resumes").status_code == 401
    register(client)
    r = client.post("/api/resumes/upload", data={"pasted_text": "x" * 100})
    assert r.status_code == 403  # missing CSRF header


def test_upload_review_and_versioning(user_client):
    p1 = upload_demo(user_client)
    assert p1["version"]["version_number"] == 1
    assert p1["version"]["extraction_status"] == "ok"
    assert p1["entries"] and p1["skills"]
    assert p1["experience"]["groups"]["all_professional"]["entries"] == 3
    # Second upload into the same resume creates version 2; version 1 is unchanged.
    r = user_client.post("/api/resumes/upload", data={"pasted_text": (RES / "demo_jordan_lee.txt").read_text(),
                                                      "resume_id": p1["version"]["resume_id"]}, headers=CSRF)
    assert r.status_code == 201
    assert r.json()["version"]["version_number"] == 2
    again = user_client.get(f"/api/resumes/versions/{p1['version']['id']}").json()
    assert again["raw_text"] == p1["raw_text"]
    listing = user_client.get("/api/resumes").json()
    assert len(listing["resumes"][0]["versions"]) == 2


def test_scanned_upload_reports_needs_text(user_client):
    r = user_client.post("/api/resumes/upload", files={"file": ("scan.pdf", (RES / "scanned_no_text.pdf").read_bytes(), "application/pdf")}, headers=CSRF)
    assert r.status_code == 201
    body = r.json()
    assert body["version"]["extraction_status"] == "needs_text"
    assert body["entries"] == []
    rv = user_client.post(f"/api/resumes/versions/{body['version']['id']}/review", headers=CSRF)
    assert rv.status_code == 409


def test_bad_file_is_rejected_with_reason(user_client):
    r = user_client.post("/api/resumes/upload", files={"file": ("cv.pdf", (RES / "not_really_a.pdf").read_bytes(), "application/pdf")}, headers=CSRF)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "type_mismatch"


def test_corrections_preserve_provenance(user_client):
    p = upload_demo(user_client)
    vid = p["version"]["id"]
    ra = next(e for e in p["entries"] if e["title"] == "Graduate Research Assistant")
    # User reclassifies the research role as an activity (e.g. it was unpaid).
    r = user_client.patch(f"/api/resumes/versions/{vid}/entries/{ra['id']}", json={"kind": "activity"}, headers=CSRF)
    assert r.status_code == 200
    edited = next(e for e in r.json()["entries"] if e["id"] == ra["id"])
    assert edited["kind"] == "activity" and edited["user_edited"]
    assert edited["extracted"]["kind"] == "employment"  # original parser output kept
    assert r.json()["experience"]["groups"]["all_professional"]["entries"] == 2
    log = user_client.get(f"/api/resumes/versions/{vid}/edits").json()
    assert log[-1]["before"] == {"kind": "employment"} and log[-1]["after"] == {"kind": "activity"}


def test_user_added_skill_is_labelled_not_resume_evidence(user_client):
    p = upload_demo(user_client)
    vid = p["version"]["id"]
    r = user_client.post(f"/api/resumes/versions/{vid}/skills", json={"name": "k8s", "note": "Used at work, not on resume"}, headers=CSRF)
    assert r.status_code == 201
    k8s = next(s for s in r.json()["skills"] if s["canonical"] == "kubernetes")
    assert k8s["source"] == "user_added" and k8s["evidence"] == [] and k8s["verified_at"]
    # Removing an extracted skill keeps the record (soft remove) and logs it.
    py = next(s for s in r.json()["skills"] if s["canonical"] == "python")
    r2 = user_client.patch(f"/api/resumes/versions/{vid}/skills/{py['id']}", json={"removed": True}, headers=CSRF)
    assert next(s for s in r2.json()["skills"] if s["canonical"] == "python")["removed"] is True


def test_ownership_isolation(client):
    register(client, "alice@example.com")
    p = upload_demo(client)
    vid, rid = p["version"]["id"], p["version"]["resume_id"]
    entry_id = p["entries"][0]["id"]
    client.post("/api/auth/logout", headers=CSRF)
    register(client, "mallory@example.com")
    assert client.get(f"/api/resumes/versions/{vid}").status_code == 404
    assert client.patch(f"/api/resumes/versions/{vid}/entries/{entry_id}", json={"title": "x"}, headers=CSRF).status_code == 404
    assert client.post(f"/api/resumes/versions/{vid}/review", headers=CSRF).status_code == 404
    assert client.delete(f"/api/resumes/{rid}", headers=CSRF).status_code == 404
    assert client.get("/api/resumes").json()["resumes"] == []


def test_delete_removes_files_and_records(user_client):
    from app.core.config import get_settings

    existing = set(get_settings().upload_dir.rglob("*"))
    p = upload_demo(user_client)
    files_before = [f for f in get_settings().upload_dir.rglob("*") if f.is_file() and f not in existing]
    assert len(files_before) == 1
    r = user_client.delete(f"/api/resumes/{p['version']['resume_id']}", headers=CSRF)
    assert r.status_code == 200 and r.json()["files_deleted"] == 1
    assert not any(f.exists() for f in files_before)
    assert user_client.get(f"/api/resumes/versions/{p['version']['id']}").status_code == 404
