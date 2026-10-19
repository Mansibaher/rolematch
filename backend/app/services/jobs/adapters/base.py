from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.services.jobs.http import Counter
from app.services.jobs.normalize import NormalizedJob


@dataclass
class FetchResult:
    jobs: list[NormalizedJob]
    # True only when we are confident the listing is the source's complete set of open jobs.
    # Closure detection runs only for complete listings, so outages and truncated pages never close jobs.
    complete: bool
    pages: int = 0
    counter: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    kind: str

    def fetch_all(self, identifier: str, employer_name: str, region: str = "us") -> FetchResult: ...

    def fetch_one(self, identifier: str, job_id: str, employer_name: str, region: str = "us") -> NormalizedJob: ...


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_epoch_ms(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
