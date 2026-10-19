"""Matching pipeline: eligibility -> lexical + semantic retrieval -> evidence reranking -> explanations.

Outputs two separate 0–100 indices plus a combined priority:
  qualification_match  – documented alignment with the posting's requirements (no freshness, no preferences)
  preference_fit       – soft preferences (location, work mode, role family, ...) and bounded feedback nudges
  application_priority – documented mix of qualification match, preference fit and posting freshness
None of these is a probability of an interview, OA or offer.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import numpy as np
import yaml
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job, JobFeedback, JobSource, MatchResult, Preferences, ResumeVersion, User, UserWatch, WorkEligibility
from app.services.matching import eligibility as elig_mod
from app.services.matching.enrich import enrich_job, needs_enrichment
from app.services.matching.evaluate import evaluate_all
from app.services.matching.profile import ProfileContext, build_profile
from app.services.skills import get_taxonomy

ENGINE_VERSION = "5"


class ProfileNotReady(Exception):
    pass


@lru_cache
def scoring_config() -> dict:
    return yaml.safe_load((get_settings().config_dir / "scoring.yaml").read_text())


def config_hash() -> str:
    return hashlib.sha256(json.dumps(scoring_config(), sort_keys=True).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ components


def seniority_component(level: str, prof: ProfileContext) -> tuple[float | None, str]:
    g = prof.experience["groups"]["excluding_internships"]
    lo, hi = g["min_months"] / 12, g["max_months"] / 12
    incl = prof.experience["groups"]["all_professional"]["point_months"] / 12
    has_jobs = prof.experience["groups"]["all_professional"]["entries"] > 0
    in_school = any(d["in_progress"] for d in prof.degrees)
    rng = f"{lo:.1f}" if abs(hi - lo) < 0.05 else f"{lo:.1f}–{hi:.1f}"

    def band(years: float) -> float:
        if level == "intern":
            return 1.0 if in_school else 0.3
        if level == "entry":
            return 1.0 if years < 4 else 0.7
        if level == "mid":
            return 1.0 if 2 <= years <= 7 else 0.6 if years >= 1 else 0.3
        if level == "senior":
            return 1.0 if years >= 5 else 0.5 if years >= 3 else 0.15
        if level == "staff":
            return 1.0 if years >= 8 else 0.4 if years >= 5 else 0.0
        return 0.6

    if level == "unknown":
        return 0.6, "The posting does not state a level; neutral score."
    if not has_jobs and level not in ("intern", "entry"):
        return 0.2, f"Role level looks {level}; the resume has no employment entries."
    best = max(band(lo), band(hi))
    note = f"Role level looks {level}; resume shows {rng} years of non-internship experience ({incl:.1f} including internships)."
    if band(lo) != band(hi):
        note += " Score uses the more favourable end of the uncertain range."
    return best, note


def freshness(job: Job, now: datetime, cfg: dict) -> dict:
    p = cfg["priority"]
    basis_date = job.posted_at or job.first_seen_at
    basis = "posted" if job.posted_at else "first_seen"
    age_days = max(0.0, (now - basis_date).total_seconds() / 86400)
    score = 0.5 ** (age_days / p["freshness_half_life_days"])
    stale_after = timedelta(hours=get_settings().stale_after_hours)
    verified = job.last_checked_at or job.last_seen_at
    unverified = job.owner_user_id is not None or (now - verified) > stale_after
    if unverified:
        score *= p["unverified_freshness_factor"]
    if job.status == "closed":
        score = 0.0
    return {
        "score": round(score, 4),
        "basis": basis,
        "age_days": round(age_days, 1),
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "posted_at_basis": job.posted_at_basis,
        "first_seen_at": job.first_seen_at.isoformat(),
        "last_verified_at": verified.isoformat() if verified else None,
        "unverified": unverified,
        "status": job.status,
    }


def _coverage(results, kinds: tuple[str, ...], credit: dict) -> tuple[float | None, int]:
    rel = [r for r in results if r.kind in kinds and r.category != "soft"]
    if not rel:
        return None, 0
    return sum(credit[r.status] for r in rel) / len(rel), len(rel)


def _feedback_adjustment(job: Job, extraction: dict, prefs: Preferences, cfg: dict) -> float:
    adj = prefs.feedback_adjustments or {}
    total = 0.0
    for fam in extraction.get("role_families") or []:
        total += adj.get(f"family:{fam}", 0.0)
    total += adj.get(f"employer:{job.employer.lower()}", 0.0)
    cap = cfg["feedback"]["max_abs_adjustment"]
    return max(-cap, min(cap, total))


def score_job(job: Job, prof: ProfileContext, prefs: Preferences, elig: WorkEligibility | None, now: datetime) -> dict:
    cfg = scoring_config()
    q = cfg["qualification"]
    extraction = job.extraction or {}
    checks = elig_mod.check_job(job, prefs, elig, extraction)
    elig_summary = elig_mod.summarize(checks)
    results = evaluate_all(extraction, prof, cfg)

    credit = q["status_credit"]
    req_cov, n_req = _coverage(results, ("required", "unclear"), credit)
    pref_cov, n_pref = _coverage(results, ("preferred",), credit)
    skill_strengths = [r.strength for r in results if r.strength is not None and r.kind in ("required", "preferred", "unclear")]
    lexical_depth = float(np.mean(skill_strengths)) if skill_strengths else None
    sim = None
    if job.embedding is not None and prof.profile_vector is not None and np.linalg.norm(prof.profile_vector) > 0:
        sim = float(np.dot(np.asarray(job.embedding, dtype=np.float32), prof.profile_vector))
    s = cfg["semantic"]
    sim01 = None if sim is None else min(1.0, max(0.0, (sim - s["profile_sim_floor"]) / (s["profile_sim_ceiling"] - s["profile_sim_floor"])))
    if lexical_depth is None and sim01 is None:
        depth = None
    elif lexical_depth is None:
        depth = sim01
    elif sim01 is None:
        depth = lexical_depth
    else:
        depth = (1 - q["depth_semantic_share"]) * lexical_depth + q["depth_semantic_share"] * sim01
    sen, sen_note = seniority_component(extraction.get("seniority", "unknown"), prof)

    comps = {"required_coverage": req_cov, "preferred_coverage": pref_cov, "evidence_depth": depth, "seniority": sen}
    w = q["weights"]
    avail = {k: v for k, v in comps.items() if v is not None}
    wsum = sum(w[k] for k in avail)
    qual = sum(w[k] * v for k, v in avail.items()) / wsum if wsum else 0.0

    soft = elig_summary["soft_score"]
    fb = _feedback_adjustment(job, extraction, prefs, cfg)
    pref_fit = min(1.0, max(0.0, (soft if soft is not None else 0.5) + fb))
    fresh = freshness(job, now, cfg)
    pw = cfg["priority"]["weights"]
    priority = pw["qualification"] * qual + pw["preference_fit"] * pref_fit + pw["freshness"] * fresh["score"]

    tax = get_taxonomy()
    strengths = []
    for r in results:
        if r.status == "supported" and r.kind in ("required", "preferred") and r.matched_skills:
            strengths.append({"requirement": r.text, "skills": [tax.name(x) for x in r.matched_skills], "evidence": r.evidence[:1]})
    gaps = [{"requirement": r.text, "kind": r.kind, "notes": r.notes} for r in results if r.status == "not_evidenced" and r.kind in ("required", "unclear")]
    pref_gaps = [{"requirement": r.text, "kind": r.kind} for r in results if r.status == "not_evidenced" and r.kind == "preferred"]
    unknowns = [{"requirement": r.text, "kind": r.kind, "notes": r.notes} for r in results if r.status == "unclear" and r.kind in ("required", "preferred", "unclear")]
    unknowns += [{"requirement": c.label, "kind": "eligibility", "notes": [c.reason]} for c in checks if c.status == "unknown"]
    warnings = list(extraction.get("warnings") or [])
    if n_req == 0:
        warnings.append("No checkable required qualifications; the qualification index relies on other components.")

    return {
        "engine_version": ENGINE_VERSION,
        "eligible": elig_summary["eligible"],
        "eligibility": elig_summary,
        "checks": [c.to_dict() for c in checks],
        "requirements": [r.to_dict() for r in results],
        "scores": {
            "qualification_match": round(100 * qual, 1),
            "preference_fit": round(100 * pref_fit, 1),
            "freshness": round(100 * fresh["score"], 1),
            "application_priority": round(100 * priority, 1),
        },
        "components": {
            "required_coverage": None if req_cov is None else round(req_cov, 3),
            "preferred_coverage": None if pref_cov is None else round(pref_cov, 3),
            "evidence_depth": None if depth is None else round(depth, 3),
            "seniority": round(sen, 3) if sen is not None else None,
            "semantic_similarity": None if sim is None else round(sim, 3),
            "lexical_evidence_strength": None if lexical_depth is None else round(lexical_depth, 3),
            "feedback_adjustment": round(fb, 3),
            "counts": {"required": n_req, "preferred": n_pref},
        },
        "weights": {"qualification": w, "priority": pw},
        "seniority": {"level": extraction.get("seniority", "unknown"), "basis": extraction.get("seniority_basis"), "note": sen_note},
        "freshness": fresh,
        "explanation": {"strengths": strengths[:5], "gaps": gaps[:6], "preferred_gaps": pref_gaps[:6], "unknowns": unknowns[:8], "warnings": warnings},
        "disclaimer": "Scores describe documented alignment between your resume and the posting. They are not a prediction of interviews, assessments or offers.",
    }


# ------------------------------------------------------------------ user context


def active_version(db: Session, user: User) -> ResumeVersion:
    prefs = db.get(Preferences, user.id)
    if prefs is None or prefs.active_resume_version_id is None:
        raise ProfileNotReady("Upload a resume and review your profile before matching.")
    v = db.get(ResumeVersion, prefs.active_resume_version_id)
    if v is None or v.user_id != user.id or not v.evidence_eligible:
        raise ProfileNotReady("Your active profile is unavailable. Review a resume version again.")
    if v.reviewed_at is None:
        raise ProfileNotReady("Review your extracted profile before matching.")
    return v


def visible_jobs_query(user: User, include_closed: bool = False):
    watched = select(UserWatch.source_id).where(UserWatch.user_id == user.id)
    q = select(Job).where(
        or_(and_(Job.owner_user_id.is_(None), Job.source_id.in_(watched)), Job.owner_user_id == user.id),
        Job.duplicate_of_id.is_(None),
    )
    if not include_closed:
        q = q.where(Job.status != "closed")
    return q


def _prefs_key(prefs: Preferences, elig: WorkEligibility | None) -> str:
    payload = {
        k: getattr(prefs, k)
        for k in ("role_families", "locations", "remote_modes", "experience_levels", "employment_types", "min_salary",
                  "salary_currency", "excluded_employers", "excluded_keywords", "hard_filters", "feedback_adjustments")
    }
    payload["elig"] = None if elig is None else [elig.needs_sponsorship, elig.exclude_citizenship_required]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _cache_key(job: Job, prof: ProfileContext, pkey: str, now: datetime) -> str:
    # Freshness changes over time; bucket by hour so cached results stay accurate enough.
    parts = [ENGINE_VERSION, config_hash(), prof.content_key, pkey, job.content_hash, job.extraction_version or "", job.status,
             str(job.last_checked_at), now.strftime("%Y%m%d%H")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def profile_query_text(prof: ProfileContext, limit: int = 25) -> str:
    tax = get_taxonomy()
    ranked = sorted(prof.skills.values(), key=lambda s: -s.strength)[:limit]
    words = []
    for s in ranked:
        name = tax.name(s.canonical) if not s.canonical.startswith("custom:") else s.name
        words.append(" ".join(w for w in name.replace("/", " ").replace("-", " ").split() if w.isalnum()))
    return " OR ".join(f'"{w}"' if " " in w else w for w in words if w)


def retrieve_candidates(db: Session, user: User, prof: ProfileContext, include_closed: bool) -> tuple[list[Job], dict]:
    """Lexical (Postgres FTS) + semantic (pgvector) candidate generation, unioned."""
    cfg = scoring_config()["retrieval"]
    base = visible_jobs_query(user, include_closed).subquery()
    total = db.scalar(select(func.count()).select_from(base)) or 0
    lex_ids: list = []
    query_text = profile_query_text(prof)
    if query_text:
        tsq = func.websearch_to_tsquery("english", query_text)
        rank = func.ts_rank_cd(Job.search_tsv, tsq)
        lex_ids = list(db.scalars(
            select(Job.id).where(Job.id.in_(select(base.c.id)), Job.search_tsv.op("@@")(tsq)).order_by(rank.desc()).limit(cfg["lexical_top_n"])
        ))
    sem_ids: list = []
    if prof.profile_vector is not None and np.linalg.norm(prof.profile_vector) > 0:
        vec = prof.profile_vector.tolist()
        sem_ids = list(db.scalars(
            select(Job.id).where(Job.id.in_(select(base.c.id)), Job.embedding.is_not(None))
            .order_by(Job.embedding.cosine_distance(vec)).limit(cfg["semantic_top_n"])
        ))
    # Manual jobs are always candidates: the user added them on purpose.
    manual_ids = list(db.scalars(select(Job.id).where(Job.id.in_(select(base.c.id)), Job.owner_user_id == user.id)))
    ids = list(dict.fromkeys(lex_ids + sem_ids + manual_ids))
    jobs = list(db.scalars(select(Job).where(Job.id.in_(ids)))) if ids else []
    stats = {"visible": total, "lexical_candidates": len(lex_ids), "semantic_candidates": len(sem_ids),
             "manual": len(manual_ids), "candidates": len(jobs), "lexical_query": query_text}
    return jobs, stats


def rank_for_user(db: Session, user: User, *, include_closed: bool = False, now: datetime | None = None, use_cache: bool = True) -> dict:
    t0 = time.perf_counter()
    now = now or datetime.now(UTC)
    version = active_version(db, user)
    prefs = db.get(Preferences, user.id)
    elig = db.get(WorkEligibility, user.id)
    prof = build_profile(db, version, scoring_config()["qualification"]["evidence_strength"], now.date())
    pkey = _prefs_key(prefs, elig)
    jobs, stats = retrieve_candidates(db, user, prof, include_closed)
    t_retrieval = time.perf_counter()

    results = []
    cached = computed = 0
    existing = {}
    if use_cache and jobs:
        existing = {(m.job_id, m.cache_key): m for m in db.scalars(select(MatchResult).where(MatchResult.user_id == user.id, MatchResult.job_id.in_([j.id for j in jobs])))}
    for job in jobs:
        if needs_enrichment(job):
            enrich_job(job)
        key = _cache_key(job, prof, pkey, now)
        hit = existing.get((job.id, key))
        if hit is not None:
            res = hit.result
            cached += 1
        else:
            res = score_job(job, prof, prefs, elig, now)
            computed += 1
            if use_cache:
                db.execute(
                    MatchResult.__table__.delete().where(MatchResult.user_id == user.id, MatchResult.job_id == job.id)
                )
                db.add(MatchResult(user_id=user.id, job_id=job.id, cache_key=key, eligible=res["eligible"],
                                   qualification_score=res["scores"]["qualification_match"], priority_score=res["scores"]["application_priority"], result=res))
        results.append((job, res))
    db.commit()
    t_end = time.perf_counter()
    stats.update({"computed": computed, "cached": cached,
                  "timing_ms": {"retrieval": round(1000 * (t_retrieval - t0), 1), "rerank": round(1000 * (t_end - t_retrieval), 1), "total": round(1000 * (t_end - t0), 1)}})
    return {"results": results, "stats": stats, "profile_version_id": str(version.id)}


def match_one(db: Session, user: User, job: Job, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    version = active_version(db, user)
    prefs = db.get(Preferences, user.id)
    elig = db.get(WorkEligibility, user.id)
    if needs_enrichment(job):
        enrich_job(job)
        db.commit()
    prof = build_profile(db, version, scoring_config()["qualification"]["evidence_strength"], now.date())
    return score_job(job, prof, prefs, elig, now)


def job_visible_to(db: Session, user: User, job: Job) -> bool:
    if job.owner_user_id is not None:
        return job.owner_user_id == user.id
    return db.get(UserWatch, (user.id, job.source_id)) is not None


def record_feedback(db: Session, user: User, job: Job, label: str, reason: str = "") -> dict:
    """Store feedback and nudge soft preferences (bounded). Never touches eligibility or evidence."""
    cfg = scoring_config()["feedback"]
    prefs = db.get(Preferences, user.id)
    prev = db.get(JobFeedback, (user.id, job.id))
    sign = {"relevant": 1, "not_relevant": -1}[label]
    adj = dict(prefs.feedback_adjustments or {})
    fams = (job.extraction or {}).get("role_families") or []

    def bump(key: str, delta: float) -> None:
        adj[key] = round(max(-cfg["max_abs_adjustment"], min(cfg["max_abs_adjustment"], adj.get(key, 0.0) + delta)), 4)

    if prev is not None:  # undo the previous vote before applying the new one
        old = {"relevant": 1, "not_relevant": -1}[prev.label]
        for f in fams:
            bump(f"family:{f}", -old * cfg["step"])
        bump(f"employer:{job.employer.lower()}", -old * cfg["step"])
        prev.label, prev.reason = label, reason
    else:
        db.add(JobFeedback(user_id=user.id, job_id=job.id, label=label, reason=reason))
    for f in fams:
        bump(f"family:{f}", sign * cfg["step"])
    bump(f"employer:{job.employer.lower()}", sign * cfg["step"])
    prefs.feedback_adjustments = adj
    db.commit()
    return adj


def no_results_explanation(results: list[tuple[Job, dict]], prefs: Preferences) -> dict:
    """Why nothing matched, with explicit (never automatic) filter-change suggestions."""
    excluded = [r for _, r in results if not r["eligible"]]
    counts: dict[str, int] = {}
    for r in excluded:
        for name in r["eligibility"]["excluded_by"]:
            counts[name] = counts.get(name, 0) + 1
    suggestions = []
    labels = elig_mod.CHECK_LABELS
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if name in ("excluded_employers", "excluded_keywords"):
            suggestions.append(f"{n} job(s) were removed by your exclusion list ({labels[name]}). Review it if that is too broad.")
        else:
            suggestions.append(f"{n} job(s) failed your hard filter “{labels.get(name, name)}”. You could make it a soft preference or widen it.")
    return {"excluded_total": len(excluded), "by_filter": counts, "suggestions": suggestions,
            "note": "Hard filters were not relaxed automatically. Change them in Preferences if you want to see these jobs."}


def sources_for_user(db: Session, user: User) -> list[JobSource]:
    return list(db.scalars(select(JobSource).join(UserWatch, UserWatch.source_id == JobSource.id).where(UserWatch.user_id == user.id)))

