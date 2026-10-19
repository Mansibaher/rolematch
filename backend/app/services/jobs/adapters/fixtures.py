"""Bundled synthetic jobs for a fully offline, reproducible demo.

These postings are fictional and are always labelled "Demo fixture" in the UI. Posting dates are stored as
"days before seeding" so the demo looks current whenever it is reset; `posted_days_ago: null` means the
fixture deliberately has no posting date (to exercise the "first seen" path).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.services.jobs.adapters.base import FetchResult
from app.services.jobs.normalize import NormalizedJob, Salary, finish


def load_fixture_file(path: Path | None = None) -> list[dict]:
    path = path or get_settings().fixtures_dir / "jobs" / "synthetic_jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["jobs"]


def normalize(item: dict, now: datetime | None = None) -> NormalizedJob:
    now = now or datetime.now(UTC)
    days = item.get("posted_days_ago")
    sal = item.get("salary")
    nj = NormalizedJob(
        source_job_id=item["id"],
        employer=item["employer"],
        title=item["title"],
        description_text=item["description"].strip(),
        requisition_id=item.get("requisition_id"),
        location_raw=item.get("location", ""),
        salary=Salary(sal["min"], sal["max"], sal.get("currency", "USD"), sal.get("interval", "year"), "fixture salary field") if sal else None,
        posted_at=(now - timedelta(days=days)) if days is not None else None,
        posted_at_basis="fixture posted_days_ago" if days is not None else None,
        apply_url=item.get("apply_url"),
        canonical_url=item.get("apply_url"),
        raw_payload={"fixture": True, "id": item["id"]},
    )
    return finish(nj, structured_workplace=item.get("workplace"), structured_commitment=item.get("commitment"))


class FixtureAdapter:
    kind = "fixture"

    def __init__(self, path: Path | None = None, now: datetime | None = None):
        self.path = path
        self.now = now

    def fetch_all(self, identifier: str, employer_name: str, region: str = "us") -> FetchResult:
        items = load_fixture_file(self.path)
        return FetchResult([normalize(i, self.now) for i in items], complete=True, pages=1)

    def fetch_one(self, identifier: str, job_id: str, employer_name: str, region: str = "us") -> NormalizedJob:
        for i in load_fixture_file(self.path):
            if i["id"] == job_id:
                return normalize(i, self.now)
        raise LookupError(job_id)
