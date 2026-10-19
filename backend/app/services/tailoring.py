"""Evidence-grounded resume tailoring.

Produces suggestions for one job from the user's *verified* profile:
  - selects the resume bullets most relevant to the posting's requirements
  - suggests conservative rewording (align terminology with the posting, surface an explicitly implied skill,
    tidy filler) — never new employers, skills, dates, qualifications or metrics
  - shows the source bullet for every suggestion
  - lists requirements that cannot be supported from verified experience

Every suggestion (rule-based, user-edited, or from the optional LLM) must pass `check_claims` against its
source bullet. Saving creates a new resume version with origin "tailored" and evidence_eligible=False, so
tailored wording never feeds back into matching. The original version is never modified.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job, ResumeVersion, User
from app.services.embeddings.store import unpack
from app.services.matching.engine import active_version, scoring_config
from app.services.matching.evaluate import evaluate_all
from app.services.matching.profile import build_profile
from app.services.skills import get_taxonomy

NUMBER_RX = re.compile(r"\d+(?:[.,]\d+)?\s*[%xX+]?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b", re.I)
PROPER_RX = re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\.[a-zA-Z]+)?\b")
FILLER = [
    (re.compile(r"^(?:was\s+)?responsible\s+for\s+", re.I), ""),
    (re.compile(r"^(?:helped|assisted)\s+(?:to\s+|in\s+)?", re.I), "Contributed to "),
    (re.compile(r"\s{2,}"), " "),
    (re.compile(r"\s+([,.;:])"), r"\1"),
]
COMMON_CAPS = {"I", "A", "An", "The", "And", "Or", "For", "With", "To", "Of", "In", "On", "At", "By", "Built", "Designed",
               "Developed", "Contributed", "Added", "Applied", "Wrote", "Led", "Created", "Implemented", "Migrated", "Containerized",
               "Benchmarked", "Evaluated", "Participated", "Guided", "Improved", "Reduced", "Shipped", "Owned", "Used"}


@dataclass
class ClaimCheck:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s+", "", m.group(0).lower()) for m in NUMBER_RX.finditer(text)}


def check_claims(source: str, suggestion: str) -> ClaimCheck:
    """Reject any suggestion that adds a claim not present in (or implied by) the source line."""
    tax = get_taxonomy()
    problems: list[str] = []
    src_skills = set(tax.find_ids(source))
    allowed = src_skills | {i for s in src_skills for i in tax.implied_by(s)}
    new_skills = set(tax.find_ids(suggestion)) - allowed
    if new_skills:
        problems.append("adds skills not shown in the source line: " + ", ".join(sorted(tax.name(s) for s in new_skills)))
    new_numbers = _numbers(suggestion) - _numbers(source)
    if new_numbers:
        problems.append("adds numbers or metrics not in the source line: " + ", ".join(sorted(new_numbers)))
    skill_words = {w.lower() for s in allowed for w in re.findall(r"[A-Za-z0-9.]+", tax.name(s))}
    src_words = {w.lower() for w in re.findall(r"[A-Za-z0-9.]+", source)}
    # Capitalised words that don't start a sentence are treated as names (employers, products, places).
    new_names = [m.group(0) for m in PROPER_RX.finditer(suggestion)
                 if not re.search(r"(^|[.!?;:(]\s*)$", suggestion[: m.start()])
                 and m.group(0) not in COMMON_CAPS and m.group(0).lower() not in src_words and m.group(0).lower() not in skill_words]
    if new_names:
        problems.append("adds names not in the source line: " + ", ".join(sorted(set(new_names))))
    return ClaimCheck(not problems, problems)


@dataclass
class Suggestion:
    id: str
    entry_id: str
    entry_title: str
    entry_kind: str
    bullet_id: str
    source_text: str
    source_start: int | None
    source_end: int | None
    suggested_text: str
    changes: list[str]
    requirements: list[str]
    relevance: float
    check: dict
    alternatives: list[dict] = field(default_factory=list)


def _align_terms(text: str, wanted: dict[str, str]) -> tuple[str, list[str]]:
    """Replace an alias of a skill with the posting's own wording for the same skill (Postgres -> PostgreSQL)."""
    tax = get_taxonomy()
    changes: list[str] = []
    out = text
    for m in reversed(tax.find(text)):
        target = wanted.get(m.skill_id)
        if target and m.text.lower() != target.lower() and m.skill_id not in ("c", "go", "r_lang"):
            out = out[: m.start] + target + out[m.end :]
            changes.append(f"Used the posting's term “{target}” instead of “{m.text}” (same skill).")
    return out, changes


def _posting_terms(job: Job) -> dict[str, str]:
    tax = get_taxonomy()
    terms: dict[str, str] = {}
    for m in tax.find(job.description_text):
        terms.setdefault(m.skill_id, m.text)
    return terms


def suggest(db: Session, user: User, job: Job, provider_override=None) -> dict:
    cfg = scoring_config()
    version = active_version(db, user)
    prof = build_profile(db, version, cfg["qualification"]["evidence_strength"])
    extraction = job.extraction or {}
    results = evaluate_all(extraction, prof, cfg)
    tax = get_taxonomy()
    wanted_terms = _posting_terms(job)
    req_vecs = {k: unpack(v) for k, v in (extraction.get("req_embeddings") or {}).items()}
    target_reqs = [r for r in results if r.kind in ("required", "preferred", "unclear")]
    req_skill = {r.id: set(r.matched_skills) | set(r.missing_skills) for r in target_reqs}

    suggestions: list[Suggestion] = []
    for idx, item in enumerate(prof.items):
        e = item.entry
        if e.kind not in ("employment", "project") or item.key.endswith(":h"):
            continue
        bullet_id = item.key.split(":", 1)[1]
        skills = set(tax.find_ids(item.text))
        expanded = skills | {i for s in skills for i in tax.implied_by(s)}
        hits = [r for r in target_reqs if req_skill[r.id] & expanded]
        sem = 0.0
        if req_vecs and prof.item_vectors.shape[0] > idx:
            sims = [float(prof.item_vectors[idx] @ v) for v in req_vecs.values()]
            sem = max(sims) if sims else 0.0
        relevance = len(hits) + 0.5 * (1.0 if hits else 0.0) * len({r.kind for r in hits if r.kind == "required"}) + sem
        if not hits and sem < cfg["semantic"]["related_threshold"]:
            continue
        text = item.text
        changes: list[str] = []
        for rx, rep in FILLER:
            new = rx.sub(rep, text)
            if new != text and rx.pattern.startswith("^"):
                changes.append("Removed filler wording at the start.")
            text = new
        text = text[:1].upper() + text[1:]
        text, c2 = _align_terms(text, wanted_terms)
        changes += c2
        # Surface an implied skill the posting asks for, e.g. "(Python)" after a FastAPI mention.
        missing_but_implied = sorted({
            s for r in hits for s in req_skill[r.id]
            if s not in skills and any(s in tax.implied_by(have) for have in skills) and tax.name(s).lower() not in text.lower()
        })
        if missing_but_implied:
            names = [wanted_terms.get(s, tax.name(s)) for s in missing_but_implied][:2]
            text = text.rstrip(".") + f" ({', '.join(names)})"
            changes.append(f"Named {', '.join(names)} explicitly; it is implied by the tools in this line.")
        check = check_claims(item.text, text)
        if not check.ok:  # never offer a suggestion that fails the checker; fall back to the original wording
            text, changes = item.text, []
            check = check_claims(item.text, text)
        suggestions.append(Suggestion(
            id=f"{e.id}:{bullet_id}", entry_id=str(e.id), entry_title=f"{e.title} — {e.organization}".strip(" —"), entry_kind=e.kind,
            bullet_id=bullet_id, source_text=item.text, source_start=item.start, source_end=item.end, suggested_text=text,
            changes=changes or ["No wording change needed; selected because it supports this posting."],
            requirements=[r.text for r in hits][:4], relevance=round(relevance, 3), check=asdict(check),
        ))
    suggestions.sort(key=lambda s: -s.relevance)
    suggestions = suggestions[:8]
    llm_note = None
    if user.llm_opt_in:
        from app.services.llm.provider import get_provider, request_rewrites

        provider = provider_override or get_provider()
        if provider is not None:
            rewrites = request_rewrites(provider, [{"id": s.id, "text": s.source_text} for s in suggestions], [r.text for r in target_reqs])
            rejected = 0
            for rw in rewrites:
                s = next(x for x in suggestions if x.id == rw.id)
                chk = check_claims(s.source_text, rw.text)
                if chk.ok:
                    s.alternatives.append({"text": rw.text, "source": f"external AI ({provider.name})", "check": asdict(chk)})
                else:
                    rejected += 1
            llm_note = f"External AI proposed {len(rewrites)} rewrites; {rejected} were discarded for adding unsupported claims."
    unsupported = [{"requirement": r.text, "kind": r.kind, "status": r.status} for r in target_reqs if r.status in ("not_evidenced", "unclear") and r.category != "soft"]
    return {
        "job": {"id": str(job.id), "title": job.title, "employer": job.employer},
        "base_version_id": str(version.id),
        "suggestions": [asdict(s) for s in suggestions],
        "llm_note": llm_note,
        "unsupported_requirements": unsupported,
        "note": "Suggestions only reword lines from your verified profile. They never add employers, skills, dates or metrics. Review each one before saving; the original resume is not changed.",
    }


def save_tailored(db: Session, user: User, job: Job, accepted: list[dict]) -> ResumeVersion:
    """accepted: [{"id": suggestion id, "text": final wording}] — each is re-checked against its source line."""
    version = active_version(db, user)
    data = suggest(db, user, job)
    by_id = {s["id"]: s for s in data["suggestions"]}
    text = version.raw_text
    record = []
    replacements = []
    for a in accepted:
        s = by_id.get(a["id"])
        if s is None:
            raise ValueError(f"Unknown suggestion {a['id']}")
        final = (a.get("text") or s["suggested_text"]).strip()
        chk = check_claims(s["source_text"], final)
        if not chk.ok:
            raise ValueError(f"“{final[:60]}…” {'; '.join(chk.problems)}")
        if s["source_start"] is not None and s["source_end"] is not None:
            replacements.append((s["source_start"], s["source_end"], final))
        record.append({"suggestion_id": s["id"], "source_text": s["source_text"], "final_text": final, "changes": s["changes"]})
    for start, end, final in sorted(replacements, reverse=True):
        text = text[:start] + final + text[end:]
    num = (db.scalar(select(func.max(ResumeVersion.version_number)).where(ResumeVersion.resume_id == version.resume_id)) or 0) + 1
    v = ResumeVersion(
        id=uuid.uuid4(), resume_id=version.resume_id, user_id=user.id, version_number=num, origin="tailored",
        parent_version_id=version.id, raw_text=text, segments=[], extraction_status="ok", extraction_warnings=[],
        evidence_eligible=False, tailored_for_job_id=job.id, reviewed_at=None,
        tailoring_record={"job_id": str(job.id), "created_at": datetime.now(UTC).isoformat(), "accepted": record,
                          "unsupported_requirements": data["unsupported_requirements"]},
    )
    db.add(v)
    db.commit()
    return v

