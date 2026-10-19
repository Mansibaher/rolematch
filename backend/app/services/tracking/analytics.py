"""Descriptive outcome analytics with explicit sample sizes and caveats.

No predictive model is trained. No response is treated as pending/unknown, never as a rejection.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, User
from app.services.tracking.service import SUBMITTED_STATUSES

REACHED = {
    "oa": {"oa_received", "oa_completed"},
    "screen": {"recruiter_screen"},
    "interview": {"interview"},
    "offer": {"offer"},
}
SMALL_SAMPLE = 30


def compute(db: Session, user: User, now: datetime | None = None, no_response_days: int = 21) -> dict:
    now = now or datetime.now(UTC)
    apps = db.scalars(select(Application).where(Application.user_id == user.id)).all()
    submitted = [a for a in apps if a.applied_at is not None]
    n = len(submitted)

    def reached(a: Application, stage: str) -> bool:
        statuses = {e.to_status for e in a.events}
        return bool(statuses & REACHED[stage])

    funnel = {stage: sum(1 for a in submitted if reached(a, stage)) for stage in REACHED}
    rejected = sum(1 for a in submitted if a.status == "rejected")
    withdrawn = sum(1 for a in submitted if a.status == "withdrawn")
    responded = [a for a in submitted if any(e.to_status not in ("saved", "applied") for e in a.events)]
    awaiting = [a for a in submitted if a not in responded and a.status == "applied"]
    long_wait = [a for a in awaiting if (now - a.applied_at).days >= no_response_days]

    def days_between(a: Application, frm: set[str], to: set[str]) -> float | None:
        t_from = min((e.occurred_at for e in a.events if e.to_status in frm), default=None)
        t_to = min((e.occurred_at for e in a.events if e.to_status in to), default=None)
        if t_from and t_to and t_to >= t_from:
            return (t_to - t_from).total_seconds() / 86400
        return None

    def med(values: list[float]) -> dict:
        vals = [v for v in values if v is not None]
        return {"median_days": round(statistics.median(vals), 1) if vals else None, "n": len(vals)}

    stage_times = {
        "applied_to_first_response": med([days_between(a, {"applied"}, {"oa_received", "recruiter_screen", "interview", "rejected", "offer"}) for a in submitted]),
        "applied_to_oa": med([days_between(a, {"applied"}, REACHED["oa"]) for a in submitted]),
        "oa_to_interview": med([days_between(a, REACHED["oa"], REACHED["interview"]) for a in submitted]),
        "interview_to_offer": med([days_between(a, REACHED["interview"], REACHED["offer"]) for a in submitted]),
    }
    first = min((a.applied_at for a in submitted), default=None)
    rate = lambda k: None if n == 0 else round(k / n, 3)  # noqa: E731
    warnings = []
    if n < SMALL_SAMPLE:
        warnings.append(f"Only {n} submitted application(s). Rates from samples this small are unstable; treat them as descriptive counts, not estimates.")
    warnings.append("Selection bias: you choose which jobs to apply to, so these outcomes do not show how other jobs or other candidates would fare.")
    warnings.append(f"Applications without a reply are counted as pending ({len(awaiting)} now), not as rejections.")
    return {
        "observation_window": {"from": first.isoformat() if first else None, "to": now.isoformat(),
                               "days": (now - first).days if first else 0},
        "counts": {
            "tracked": len(apps),
            "saved_only": sum(1 for a in apps if a.status == "saved"),
            "submitted": n,
            "reached_oa": funnel["oa"],
            "reached_recruiter_screen": funnel["screen"],
            "reached_interview": funnel["interview"],
            "offers": funnel["offer"],
            "rejected": rejected,
            "withdrawn": withdrawn,
            "pending_no_response": len(awaiting),
            f"pending_over_{no_response_days}_days": len(long_wait),
            "closed_without_application": sum(1 for a in apps if a.status == "closed_without_application"),
        },
        "rates": {"oa_per_submitted": rate(funnel["oa"]), "interview_per_submitted": rate(funnel["interview"]),
                  "offer_per_submitted": rate(funnel["offer"]), "denominator": n},
        "stage_times": stage_times,
        "by_status": {s: sum(1 for a in apps if a.status == s) for s in sorted({a.status for a in apps})},
        "warnings": warnings,
        "note": "Descriptive only. RoleMatch does not train or report a probability of getting an interview or offer.",
        "submitted_statuses": sorted(SUBMITTED_STATUSES),
    }
