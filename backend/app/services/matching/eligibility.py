"""Explicit eligibility and user-filter checks.

Each check returns pass / fail / unknown with a human-readable reason and, when relevant, the source text.
- Hard checks exclude a job only on an explicit `fail`. An `unknown` on a hard check never excludes; the job is
  kept and flagged so the user can inspect it. Hard constraints are never relaxed silently.
- Soft checks only influence the preference-fit score.
- Missing information is `unknown`: a posting that does not mention sponsorship is not treated as "no sponsorship".
- Protected characteristics are never inferred or used.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.models import Job, Preferences, WorkEligibility
from app.services.jobs.normalize import COUNTRY_ALIASES, parse_location

ALWAYS_HARD = {"excluded_employers", "excluded_keywords"}
DEFAULT_HARD = {"sponsorship": True, "citizenship": True}
CHECK_LABELS = {
    "excluded_employers": "Excluded employers",
    "excluded_keywords": "Excluded keywords",
    "role_family": "Role family",
    "location": "Location",
    "work_mode": "Remote / hybrid / on-site",
    "employment_type": "Employment type",
    "experience_level": "Experience level",
    "salary": "Salary",
    "sponsorship": "Visa sponsorship",
    "citizenship": "Citizenship / clearance requirement",
}

LEVEL_ORDER = ["intern", "entry", "mid", "senior", "staff"]


@dataclass
class Check:
    name: str
    label: str
    status: str  # pass | fail | unknown | not_applicable
    hard: bool
    reason: str
    evidence: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_hard(prefs: Preferences, name: str) -> bool:
    if name in ALWAYS_HARD:
        return True
    hf = prefs.hard_filters or {}
    return bool(hf.get(name, DEFAULT_HARD.get(name, False)))


def _user_places(prefs: Preferences) -> list[dict]:
    out = []
    for raw in prefs.locations or []:
        low = raw.strip().lower()
        if low in ("remote", "anywhere"):
            continue
        if low in COUNTRY_ALIASES:
            out.append({"raw": raw, "city": None, "region": None, "country": COUNTRY_ALIASES[low]})
        else:
            out.append(parse_location(raw).as_dict())
    return out


def _place_match(job_loc: dict, user: dict) -> bool:
    if user.get("city"):
        if job_loc.get("city") and job_loc["city"].lower() == user["city"].lower():
            return True
        # "San Francisco Bay Area" style metros contain the city name.
        return bool(job_loc.get("city") and user["city"].lower() in job_loc["city"].lower())
    if user.get("region"):
        return job_loc.get("region") == user["region"]
    if user.get("country"):
        return job_loc.get("country") == user["country"]
    return False


def _user_countries(places: list[dict]) -> set[str]:
    return {p["country"] for p in places if p.get("country")}


def check_job(job: Job, prefs: Preferences, elig: WorkEligibility | None, extraction: dict) -> list[Check]:
    checks: list[Check] = []
    text = f"{job.title}\n{job.description_text}"

    # Excluded employers / keywords (always hard).
    emp = job.employer.lower()
    hit = next((e for e in prefs.excluded_employers or [] if e.strip() and e.strip().lower() in emp), None)
    checks.append(Check("excluded_employers", CHECK_LABELS["excluded_employers"], "fail" if hit else "pass", True,
                        f"Employer matches your exclusion “{hit}”." if hit else "Employer is not excluded."))
    kw = None
    for k in prefs.excluded_keywords or []:
        k = k.strip()
        if k and re.search(rf"(?i)(?<![\w]){re.escape(k)}(?![\w])", text):
            kw = k
            break
    if kw:
        m = re.search(rf"(?i)(?<![\w]){re.escape(kw)}(?![\w])", text)
        snippet = text[max(0, m.start() - 60) : m.end() + 60].replace("\n", " ")
        checks.append(Check("excluded_keywords", CHECK_LABELS["excluded_keywords"], "fail", True, f"Posting mentions excluded keyword “{kw}”.", snippet))
    else:
        checks.append(Check("excluded_keywords", CHECK_LABELS["excluded_keywords"], "pass", True, "No excluded keywords found."))

    # Role family
    fams = extraction.get("role_families") or []
    if prefs.role_families:
        if not fams:
            checks.append(Check("role_family", CHECK_LABELS["role_family"], "unknown", is_hard(prefs, "role_family"),
                                "The title did not match any configured role family."))
        elif set(fams) & set(prefs.role_families):
            checks.append(Check("role_family", CHECK_LABELS["role_family"], "pass", is_hard(prefs, "role_family"),
                                f"Title matches {', '.join(sorted(set(fams) & set(prefs.role_families)))}."))
        else:
            checks.append(Check("role_family", CHECK_LABELS["role_family"], "fail", is_hard(prefs, "role_family"),
                                f"Title is classified as {', '.join(fams)}, which is not in your role families."))

    # Work mode
    modes = set(prefs.remote_modes or [])
    if modes:
        if job.remote_type == "unknown":
            checks.append(Check("work_mode", CHECK_LABELS["work_mode"], "unknown", is_hard(prefs, "work_mode"), "The posting does not state remote/hybrid/on-site."))
        else:
            ok = job.remote_type in modes
            checks.append(Check("work_mode", CHECK_LABELS["work_mode"], "pass" if ok else "fail", is_hard(prefs, "work_mode"),
                                f"Posting is {job.remote_type}; you accept {', '.join(sorted(modes))}."))

    # Location
    places = _user_places(prefs)
    accepts_remote = (not modes) or ("remote" in modes)
    if places or prefs.locations:
        hard = is_hard(prefs, "location")
        if job.remote_type == "remote" and accepts_remote:
            countries = _user_countries(places)
            restr = (job.remote_restriction or "").strip()
            restr_country = COUNTRY_ALIASES.get(restr.lower().rstrip("."), restr) if restr else None
            if restr_country and countries and restr_country not in countries and restr_country not in ("EU", "Europe"):
                checks.append(Check("location", CHECK_LABELS["location"], "fail", hard,
                                    f"Remote role restricted to {restr_country}; your locations are in {', '.join(sorted(countries))}.", job.remote_restriction))
            elif restr_country:
                checks.append(Check("location", CHECK_LABELS["location"], "pass", hard, f"Remote role (restricted to {restr_country})."))
            else:
                checks.append(Check("location", CHECK_LABELS["location"], "pass" if not places else "unknown", hard,
                                    "Remote role; the posting does not say which countries are eligible." if places else "Remote role."))
        else:
            job_locs = job.locations or []
            if not job_locs:
                checks.append(Check("location", CHECK_LABELS["location"], "unknown", hard, "The posting does not state a location."))
            else:
                match = next((jl for jl in job_locs for up in places if _place_match(jl, up)), None)
                if match:
                    checks.append(Check("location", CHECK_LABELS["location"], "pass", hard, f"{match['raw']} matches your preferred locations."))
                else:
                    checks.append(Check("location", CHECK_LABELS["location"], "fail", hard,
                                        f"{job.location_raw or 'Location'} is outside your preferred locations ({', '.join(prefs.locations)})."))

    # Employment type
    if prefs.employment_types:
        hard = is_hard(prefs, "employment_type")
        if job.employment_type == "unknown":
            checks.append(Check("employment_type", CHECK_LABELS["employment_type"], "unknown", hard, "Employment type is not stated."))
        else:
            ok = job.employment_type in prefs.employment_types
            checks.append(Check("employment_type", CHECK_LABELS["employment_type"], "pass" if ok else "fail", hard,
                                f"Posting is {job.employment_type.replace('_', '-')} ({job.employment_type_basis})."))

    # Experience level
    if prefs.experience_levels:
        hard = is_hard(prefs, "experience_level")
        lvl = extraction.get("seniority", "unknown")
        if lvl == "unknown":
            checks.append(Check("experience_level", CHECK_LABELS["experience_level"], "unknown", hard, "The posting does not state a level."))
        else:
            ok = lvl in prefs.experience_levels
            checks.append(Check("experience_level", CHECK_LABELS["experience_level"], "pass" if ok else "fail", hard,
                                f"Level looks like {lvl} ({extraction.get('seniority_basis', '')})."))

    # Salary (only when the source supplies one)
    if prefs.min_salary:
        hard = is_hard(prefs, "salary")
        if job.salary_max is None or job.salary_interval != "year" or (prefs.salary_currency and job.salary_currency != prefs.salary_currency):
            checks.append(Check("salary", CHECK_LABELS["salary"], "unknown", hard, "No comparable annual salary is listed."))
        else:
            ok = job.salary_max >= prefs.min_salary
            checks.append(Check("salary", CHECK_LABELS["salary"], "pass" if ok else "fail", hard,
                                f"Listed range {job.salary_currency} {job.salary_min:,.0f}–{job.salary_max:,.0f}; your minimum is {prefs.min_salary:,}.",
                                job.salary_basis))

    # Sponsorship and citizenship (private inputs; reasons are shown only to the owner)
    el = extraction.get("eligibility") or {}
    if elig is not None and elig.needs_sponsorship:
        hard = is_hard(prefs, "sponsorship")
        sp = el.get("sponsorship", "unknown")
        if sp == "not_available":
            checks.append(Check("sponsorship", CHECK_LABELS["sponsorship"], "fail", hard, "The posting says sponsorship is not available.", el.get("sponsorship_evidence")))
        elif sp == "available":
            checks.append(Check("sponsorship", CHECK_LABELS["sponsorship"], "pass", hard, "The posting says sponsorship is available.", el.get("sponsorship_evidence")))
        else:
            checks.append(Check("sponsorship", CHECK_LABELS["sponsorship"], "unknown", hard,
                                "The posting does not mention sponsorship. Unknown — not the same as unavailable."))
    if el.get("citizenship_or_clearance"):
        user_cares = elig is not None and (elig.exclude_citizenship_required or elig.needs_sponsorship)
        if user_cares:
            hard = elig.exclude_citizenship_required or is_hard(prefs, "citizenship")
            checks.append(Check("citizenship", CHECK_LABELS["citizenship"], "fail", hard,
                                "The posting requires citizenship or a security clearance.", el.get("citizenship_evidence")))
    return checks


def summarize(checks: list[Check]) -> dict:
    hard_fail = [c for c in checks if c.hard and c.status == "fail"]
    hard_unknown = [c for c in checks if c.hard and c.status == "unknown"]
    soft = [c for c in checks if not c.hard and c.status in ("pass", "fail", "unknown")]
    soft_score = None
    if soft:
        soft_score = sum({"pass": 1.0, "unknown": 0.5, "fail": 0.0}[c.status] for c in soft) / len(soft)
    return {
        "eligible": not hard_fail,
        "excluded_by": [c.name for c in hard_fail],
        "uncertain_hard": [c.name for c in hard_unknown],
        "soft_score": soft_score,
        "soft_deprioritized_by": [c.name for c in soft if c.status == "fail"],
    }
