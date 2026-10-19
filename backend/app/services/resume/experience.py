"""Professional-experience duration with overlap handling and explicit uncertainty.

Rules
- Only entries of kind "employment" count. Projects, education and activities never count as employment.
- Overlapping employment intervals are merged before summing, so concurrent jobs are not double counted.
- Each interval has lower/upper bounds that depend on date precision:
    month precision  -> exact month boundaries (the end month is counted inclusively)
    year precision   -> start anywhere in the start year, end anywhere in the end year
    approximate      -> ± 1 month (seasons such as "Summer 2023")
  The result reports min / point / max months; anything other than all-month precision is flagged uncertain.
- Entries with no start date are excluded from totals and reported as unknown duration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta

from app.services.resume.dates import months_between


@dataclass
class EntryDates:
    id: str
    kind: str
    employment_type: str
    title: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    date_precision: str
    excluded: bool = False


def _bounds(e: EntryDates, as_of: date) -> tuple[date, date, date, date] | None:
    if e.start_date is None:
        return None
    one = relativedelta(months=1)
    if e.date_precision == "year":
        s_lo = date(e.start_date.year, 1, 1)
        s_hi = date(e.start_date.year, 12, 1)
    else:
        s_lo = s_hi = e.start_date
    if e.is_current or e.end_date is None:
        if not e.is_current:
            return None  # no end and not current -> unknown duration
        e_lo = e_hi = as_of
    elif e.date_precision == "year":
        e_lo = date(e.end_date.year, 1, 1) + one
        e_hi = date(e.end_date.year, 12, 1) + one
    else:
        e_lo = e_hi = e.end_date + one
    e_lo, e_hi = min(e_lo, as_of), min(e_hi, as_of)
    return s_lo, s_hi, e_lo, e_hi


def _union_months(intervals: list[tuple[date, date]]) -> float:
    ivs = sorted((a, b) for a, b in intervals if b > a)
    total = 0.0
    cur_s: date | None = None
    cur_e: date | None = None
    for a, b in ivs:
        if cur_s is None:
            cur_s, cur_e = a, b
        elif a <= cur_e:  # overlap or touching
            cur_e = max(cur_e, b)
        else:
            total += months_between(cur_s, cur_e)
            cur_s, cur_e = a, b
    if cur_s is not None and cur_e is not None:
        total += months_between(cur_s, cur_e)
    return total


def _mid(a: date, b: date) -> date:
    return a + (b - a) / 2


def summarize(entries: list[EntryDates], as_of: date) -> dict:
    jobs = [e for e in entries if e.kind == "employment" and not e.excluded]
    groups = {
        "all_professional": jobs,
        "excluding_internships": [e for e in jobs if e.employment_type not in ("internship",)],
        "internships": [e for e in jobs if e.employment_type == "internship"],
    }
    unknown = [e for e in jobs if _bounds(e, as_of) is None]
    out: dict = {"as_of": as_of.isoformat(), "unknown_duration_entries": [e.id for e in unknown], "groups": {}}
    for name, items in groups.items():
        b = [(e, _bounds(e, as_of)) for e in items]
        b = [(e, x) for e, x in b if x]
        lo = _union_months([(s_hi, e_lo) for _, (s_lo, s_hi, e_lo, e_hi) in b])
        hi = _union_months([(s_lo, e_hi) for _, (s_lo, s_hi, e_lo, e_hi) in b])
        pt = _union_months([(_mid(s_lo, s_hi), _mid(e_lo, e_hi)) for _, (s_lo, s_hi, e_lo, e_hi) in b])
        naive = sum(months_between(_mid(s_lo, s_hi), _mid(e_lo, e_hi)) for _, (s_lo, s_hi, e_lo, e_hi) in b)
        out["groups"][name] = {
            "min_months": round(lo, 1),
            "point_months": round(pt, 1),
            "max_months": round(hi, 1),
            "overlap_months_removed": round(max(0.0, naive - pt), 1),
            "entries": len(items),
        }
    precise = all(e.date_precision == "month" for e in jobs) and not unknown
    ambiguous_types = [e.id for e in jobs if e.employment_type in ("research", "part_time", "unknown")]
    notes = []
    if not jobs:
        notes.append("No employment entries: professional experience is unknown (projects and education are not counted).")
    if not precise:
        notes.append("Some dates are year-only, approximate, or missing, so the duration is a range.")
    if any(e.employment_type in ("research", "part_time") for e in jobs):
        notes.append("Research and part-time roles are included as employment; their full-time equivalence is unknown.")
    out["uncertain"] = (not precise) or bool(unknown) or any(e.employment_type in ("research", "part_time") for e in jobs)
    out["ambiguous_type_entries"] = ambiguous_types
    out["notes"] = notes
    return out
