"""Requirement-by-requirement evidence evaluation.

Statuses
  supported            – the resume (or a user-verified addition) directly evidences the requirement
  partially_supported  – some evidence: listed but not demonstrated, implied by a related skill, only some of
                         several required skills, or experience that meets the bar only under some readings
  not_evidenced        – a checkable criterion with no evidence in the resume. This does NOT mean the user
                         lacks the skill; it means the resume does not show it.
  unclear              – the requirement is vague or not checkable from resume text (e.g. "strong
                         communication"), or extraction was uncertain

Every evidence excerpt is the literal resume line containing the matched span (or a labelled user-verified
note), so the UI can always show where a claim came from. Semantic similarity is used only to surface a
"related experience" hint for unevidenced items; it never upgrades a status to supported.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import numpy as np

from app.services.embeddings.store import unpack
from app.services.matching.profile import Evidence, ProfileContext
from app.services.matching.requirements import classify_role_families
from app.services.skills import get_taxonomy

ENROLLMENT_RX = re.compile(r"(?i)currently (?:enrolled|pursuing)|enrolled in|pursuing an? |returning to school|rising (?:junior|senior)")
GRAD_YEAR_RX = re.compile(r"(?i)graduat\w*\s+(?:in\s+|by\s+|between\s+)?(?:\w+\s+)?(20\d{2})(?:\s*(?:or|-|–|and|to)\s*(?:\w+\s+)?(20\d{2}))?")


@dataclass
class ReqResult:
    id: str
    kind: str
    text: str
    category: str
    status: str
    confidence: str
    basis: str
    evidence: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    related: dict | None = None
    strength: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ev(e: Evidence) -> dict:
    return {"text": e.text, "context": e.context, "start": e.start, "end": e.end, "location": e.location,
            "entry_title": e.entry_title, "entry_id": e.entry_id}


def _skill_status(skill: str, prof: ProfileContext, implied_factor: float) -> tuple[str, float, list[Evidence], str | None]:
    """(level, strength, evidence, note). level: demonstrated | listed | implied | none."""
    tax = get_taxonomy()
    se = prof.skills.get(skill)
    if se is not None:
        demonstrated = se.contexts & {"employment", "project", "user_added"}
        level = "demonstrated" if demonstrated else "listed"
        return level, se.strength, se.evidence[:2], None
    # Implied by a skill the user has (PostgreSQL -> SQL). Partial only.
    best: tuple[float, str] | None = None
    for have, se2 in prof.skills.items():
        if skill in tax.implied_by(have) and (best is None or se2.strength > best[0]):
            best = (se2.strength, have)
    if best:
        se2 = prof.skills[best[1]]
        return "implied", best[0] * implied_factor, se2.evidence[:1], f"{tax.name(skill)} is implied by your {se2.name} experience"
    return "none", 0.0, [], None


def _related(req_vec: np.ndarray | None, prof: ProfileContext, threshold: float) -> dict | None:
    if req_vec is None or prof.item_vectors.shape[0] == 0:
        return None
    sims = prof.item_vectors @ req_vec
    i = int(np.argmax(sims))
    if float(sims[i]) < threshold:
        return None
    it = prof.items[i]
    return {"text": it.text, "similarity": round(float(sims[i]), 3), "entry_title": it.entry.title,
            "context": it.entry.kind, "start": it.start, "end": it.end}


def _employment_months_with_skill(skill: str, prof: ProfileContext) -> float:
    se = prof.skills.get(skill)
    if not se:
        return 0.0
    ids = {e.entry_id for e in se.evidence if e.context == "employment" and e.entry_id}
    from app.services.resume.experience import EntryDates, summarize

    ents = [e for e in prof.entries if str(e.id) in ids]
    s = summarize([EntryDates(str(e.id), e.kind, e.employment_type, e.title, e.start_date, e.end_date, e.is_current, e.date_precision) for e in ents], prof.as_of)
    return s["groups"]["all_professional"]["point_months"]


GENERIC_DOMAIN_RX = re.compile(
    r"(?i)^(?:professional|industry|work|relevant|full[- ]time|hands[- ]on|technical|post[- ]graduate|related|"
    r"(?:professional |industry )?software (?:engineering|development)|engineering|programming|coding|software|development|"
    r"building (?:software|production systems|backend systems|machine learning systems)|backend(?: engineering| development)?)"
    r"(?: (?:engineering|development|software))?$"
)
DOMAIN_RX = re.compile(r"(?i)(?:years?|yrs?)\s+(?:of\s+|in\s+|as\s+an?\s+)?(?:professional\s+|industry\s+)?(.{2,60}?)\s+(?:experience|roles?|development|work|engineering)\b")
EXTRA_DOMAINS = {
    "data science": ["data_scientist"], "product management": ["product_manager"], "ios development": ["mobile_engineer"],
    "ios": ["mobile_engineer"], "android": ["mobile_engineer"], "sre or devops": ["devops_sre"], "devops": ["devops_sre"],
    "sre": ["devops_sre"], "embedded software": ["embedded_engineer"], "embedded": ["embedded_engineer"],
    "data engineering": ["data_engineer"], "analytics engineering or data engineering": ["data_engineer"],
    "analytics engineering": ["data_engineer"], "machine learning": ["ml_engineer"], "computer vision": ["ml_engineer"],
    "frontend": ["frontend_engineer"], "front-end": ["frontend_engineer"],
}


EXPERIENCE_IN_RX = re.compile(r"(?i)\bexperience\s+(?:in|with|building|developing|designing|as|on|working on)\s+(?:an?\s+|the\s+)?([^,.;:()]{2,80})")
TRAILING_NOISE_RX = re.compile(r"(?i)\s+(?:systems?|roles?|environments?|teams?|positions?|products?|applications?|services?|or equivalent.*|or (?:a )?similar.*|or related.*)$")


def _classify_phrase(phrase: str) -> dict:
    if not phrase or GENERIC_DOMAIN_RX.match(phrase):
        return {"kind": "generic", "phrase": phrase}
    if get_taxonomy().find_ids(phrase):
        return {"kind": "skill", "phrase": phrase}
    low = phrase.lower()
    if low in EXTRA_DOMAINS:
        return {"kind": "family", "phrase": phrase, "families": EXTRA_DOMAINS[low]}
    fams = classify_role_families(phrase if re.search(r"(?i)manag|scien|engineer|developer", phrase) else phrase + " engineer")
    if fams:
        return {"kind": "family", "phrase": phrase, "families": fams}
    return {"kind": "unrecognized", "phrase": phrase}


def experience_domain(text: str) -> dict:
    """Classify the domain of an experience requirement: generic | skill | family | unrecognized."""
    m = EXPERIENCE_IN_RX.search(text)
    phrase = m.group(1) if m else ""
    if not m:
        m2 = DOMAIN_RX.search(text)
        phrase = m2.group(1) if m2 else ""
    phrase = TRAILING_NOISE_RX.sub("", phrase.strip()).strip()
    parts = [p.strip() for p in re.split(r"(?i)\s+or\s+|\s+and/or\s+|,|/", phrase) if p.strip()] or [phrase]
    classified = [_classify_phrase(p) for p in parts]
    for kind in ("generic", "skill"):
        if any(c["kind"] == kind for c in classified):
            return {"kind": kind, "phrase": phrase}
    fams = sorted({f for c in classified if c["kind"] == "family" for f in c["families"]})
    if fams:
        return {"kind": "family", "phrase": phrase, "families": fams}
    return {"kind": "unrecognized", "phrase": phrase}


FAMILY_SKILLS = {
    "data_engineer": {"etl", "airflow", "spark", "data_warehousing", "kafka", "dbt", "stream_processing", "hadoop", "data_modeling"},
    "ml_engineer": {"machine_learning", "deep_learning", "pytorch", "tensorflow", "scikit_learn", "nlp", "computer_vision", "llm", "mlops"},
    "devops_sre": {"kubernetes", "terraform", "observability", "ci_cd", "infrastructure_as_code", "linux"},
    "mobile_engineer": {"swift", "kotlin", "react_native"},
    "data_scientist": {"statistics", "machine_learning", "data_analysis"},
    "embedded_engineer": {"c", "cpp"},
    "frontend_engineer": {"react", "javascript", "typescript", "html_css", "vue", "angular"},
    "backend_engineer": {"rest_api", "api_design", "microservices", "postgresql", "distributed_systems"},
}


def _entries_with_family_skills(fams: set[str], prof: ProfileContext) -> list:
    wanted = set().union(*(FAMILY_SKILLS.get(f, set()) for f in fams)) if fams else set()
    ids = {ev.entry_id for k, se in prof.skills.items() if k in wanted for ev in se.evidence if ev.context == "employment" and ev.entry_id}
    return [e for e in prof.entries if str(e.id) in ids]


def _months(entries, prof: ProfileContext) -> float:
    from app.services.resume.experience import EntryDates, summarize

    s = summarize([EntryDates(str(e.id), e.kind, e.employment_type, e.title, e.start_date, e.end_date, e.is_current, e.date_precision) for e in entries], prof.as_of)
    return s["groups"]["all_professional"]["point_months"]


def evaluate_requirement(req: dict, prof: ProfileContext, req_vec: np.ndarray | None, cfg: dict) -> ReqResult:
    tax = get_taxonomy()
    q = cfg["qualification"]
    implied_factor = q["implied_factor"]
    threshold = cfg["semantic"]["related_threshold"]
    r = ReqResult(req["id"], req["kind"], req["text"], req["category"], "unclear", req["confidence"], req["basis"])
    if req["confidence"] == "low":
        r.notes.append("Required vs. preferred is uncertain for this posting.")
    text = req["text"]

    # Enrollment / graduation-window requirements (internships, new-grad roles).
    grad = GRAD_YEAR_RX.search(text)
    if ENROLLMENT_RX.search(text) or grad:
        if ENROLLMENT_RX.search(text):
            current = [d for d in prof.degrees if d["in_progress"]]
            if current:
                d = current[0]
                r.status = "supported"
                r.evidence = [{"text": d["text"], "context": "education", "start": d["start"], "end": d["end"], "entry_title": d["organization"]}]
            elif prof.degrees:
                r.status = "not_evidenced"
                r.notes.append("Requires current enrollment; the degrees on your resume appear to be completed.")
            else:
                r.status = "unclear"
                r.notes.append("No education entries were found to check enrollment.")
            return r
        years = {int(y) for y in grad.groups() if y}
        if len(years) == 2:
            years = set(range(min(years), max(years) + 1))
        ends = [d for d in prof.degrees if d["end_date"]]
        hit = next((d for d in ends if d["end_date"].year in years), None)
        if hit:
            r.status = "supported"
            r.evidence = [{"text": hit["text"], "context": "education", "start": hit["start"], "end": hit["end"], "entry_title": hit["organization"]}]
            r.notes.append(f"Graduation {hit['end_date'].year} is within {', '.join(map(str, sorted(years)))}.")
        elif ends:
            r.status = "not_evidenced"
            r.notes.append(f"Your graduation dates ({', '.join(str(d['end_date'].year) for d in ends)}) are outside {', '.join(map(str, sorted(years)))}.")
        else:
            r.status = "unclear"
        return r

    skills = req.get("skills") or []
    years_min = req.get("years_min")

    # Years of experience (optionally tied to a skill).
    if req["category"] == "experience" or (years_min is not None and not skills):
        exp = prof.experience["groups"]
        ex_int = exp["excluding_internships"]
        allp = exp["all_professional"]
        need = years_min * 12 if years_min is not None else None
        if need is None:
            r.status = "unclear"
            return r
        if allp["entries"] == 0:
            r.status = "not_evidenced"
            r.notes.append("No employment entries on the resume, so professional experience cannot be shown.")
            return r
        skill_months = None
        if skills:
            skill_months = max((_employment_months_with_skill(s, prof) for s in skills), default=0.0)
        yrs = lambda m: f"{m / 12:.1f}"  # noqa: E731
        r.evidence = [
            {"text": f"{e.title} — {e.organization} ({e.date_text or 'dates unknown'})", "context": "employment",
             "start": (e.evidence or {}).get("start"), "end": (e.evidence or {}).get("end"), "entry_title": e.title}
            for e in prof.entries if e.kind == "employment"
        ][:4]
        basis_months = ex_int["min_months"]
        domain = experience_domain(text)
        if not skills and domain["kind"] == "family":
            fam_ids = set(domain["families"])
            matching = [e for e in prof.entries if e.kind == "employment" and fam_ids & set(classify_role_families(e.title))]
            months = _months(matching, prof)
            label = domain["phrase"]
            if not matching:
                related = _entries_with_family_skills(fam_ids, prof)
                if related:
                    r.status = "partially_supported"
                    r.notes.append(f"No role titles look like {label}, but these roles show related skills; check whether they count.")
                    r.evidence = [{"text": f"{e.title} — {e.organization} ({e.date_text or 'dates unknown'})", "context": "employment",
                                   "start": (e.evidence or {}).get("start"), "end": (e.evidence or {}).get("end"), "entry_title": e.title} for e in related][:4]
                    return r
                r.status = "not_evidenced"
                r.notes.append(f"No roles on the resume look like {label} roles.")
            elif months >= need:
                r.status = "supported"
                r.notes.append(f"About {yrs(months)} years in roles matching {label}.")
            else:
                r.status = "partially_supported"
                r.notes.append(f"About {yrs(months)} years in roles matching {label}; the posting asks for {years_min:g}+.")
            r.evidence = [{"text": f"{e.title} — {e.organization} ({e.date_text or 'dates unknown'})", "context": "employment",
                           "start": (e.evidence or {}).get("start"), "end": (e.evidence or {}).get("end"), "entry_title": e.title} for e in matching][:4]
            return r
        if not skills and domain["kind"] == "unrecognized":
            r.notes.append(f"Checked total professional experience; whether it counts as “{domain['phrase']}” experience could not be verified.")
        if skill_months is not None and skills:
            levels = {sk: _skill_status(sk, prof, implied_factor) for sk in skills}
            r.matched_skills = [sk for sk, v in levels.items() if v[0] != "none"]
            r.missing_skills = [sk for sk, v in levels.items() if v[0] == "none"]
            if not r.matched_skills:
                r.status = "not_evidenced"
                r.evidence = []
                r.notes.append(f"No evidence of {', '.join(tax.name(sk) for sk in skills)} on the resume.")
                r.related = _related(req_vec, prof, threshold)
                if r.related:
                    r.status = "unclear"
                    r.notes.append("A related resume line was found by semantic similarity. Review it — similarity is not proof.")
                return r
            if skill_months >= need:
                r.status = "supported"
                r.notes.append(f"About {yrs(skill_months)} years in roles that mention {', '.join(tax.name(s) for s in skills)}.")
            elif allp["point_months"] >= need:
                r.status = "partially_supported"
                r.notes.append(f"{yrs(allp['point_months'])} years of professional experience overall, but only {yrs(skill_months)} in roles that mention {', '.join(tax.name(s) for s in skills)}.")
            else:
                r.status = "not_evidenced"
                r.notes.append(f"Resume documents about {yrs(allp['point_months'])} years of professional experience; the posting asks for {years_min:g}+.")
            return r
        if basis_months >= need and domain["kind"] == "unrecognized":
            r.status = "partially_supported"
        elif basis_months >= need:
            r.status = "supported"
            r.notes.append(f"At least {yrs(basis_months)} years of non-internship professional experience (overlaps counted once).")
        elif ex_int["max_months"] >= need:
            r.status = "partially_supported"
            r.notes.append(f"Between {yrs(ex_int['min_months'])} and {yrs(ex_int['max_months'])} years depending on imprecise dates; the posting asks for {years_min:g}+.")
        elif allp["point_months"] >= need:
            r.status = "partially_supported"
            r.notes.append(f"Meets {years_min:g}+ years only when internships are counted ({yrs(allp['point_months'])} years including internships).")
        else:
            r.status = "not_evidenced"
            r.notes.append(f"Resume documents about {yrs(allp['point_months'])} years of professional experience; the posting asks for {years_min:g}+.")
        if prof.experience.get("uncertain"):
            r.notes.append("Experience duration is uncertain: " + " ".join(prof.experience.get("notes", [])))
        return r

    # Degree requirements.
    if req["category"] == "degree" or (req.get("degree_level") and not skills):
        need = req.get("degree_level") or 1
        have = sorted(prof.degrees, key=lambda d: -d["level"])
        best = next((d for d in have if not d["in_progress"]), None)
        if best and best["level"] >= need:
            r.status = "supported" if best["relevant_field"] else "partially_supported"
            if not best["relevant_field"]:
                r.notes.append("Degree level meets the bar; check whether your field counts as related.")
            r.evidence = [{"text": best["text"], "context": "education", "start": best["start"], "end": best["end"], "entry_title": best["organization"]}]
        elif any(d["level"] >= need and d["in_progress"] for d in have):
            d = next(d for d in have if d["level"] >= need and d["in_progress"])
            r.status = "partially_supported"
            r.notes.append("Degree is in progress.")
            r.evidence = [{"text": d["text"], "context": "education", "start": d["start"], "end": d["end"], "entry_title": d["organization"]}]
        elif req.get("degree_equivalent_ok") and prof.experience["groups"]["excluding_internships"]["min_months"] >= 24:
            r.status = "partially_supported"
            r.notes.append("The posting accepts equivalent experience; your professional experience may qualify.")
        elif not prof.degrees:
            r.status = "not_evidenced"
            r.notes.append("No degree was found on the resume.")
        else:
            r.status = "not_evidenced"
            r.notes.append("The degrees on your resume are below the level this posting asks for.")
        return r

    # Skill requirements.
    if skills:
        levels = {s: _skill_status(s, prof, implied_factor) for s in skills}
        demonstrated = [s for s, v in levels.items() if v[0] == "demonstrated"]
        weaker = [s for s, v in levels.items() if v[0] in ("listed", "implied")]
        r.matched_skills = [s for s, v in levels.items() if v[0] != "none"]
        r.missing_skills = [s for s, v in levels.items() if v[0] == "none"]
        evid: list[dict] = []
        for s in skills:
            lvl, strength, evs, note = levels[s]
            evid.extend(_ev(e) | {"skill": tax.name(s)} for e in evs)
            if note:
                r.notes.append(note)
            if lvl == "listed":
                r.notes.append(f"{tax.name(s)} is listed in your skills but not shown in a role or project.")
        # One line of evidence per distinct resume line (a bullet can mention several requested skills).
        seen: dict[str, dict] = {}
        for ev in evid:
            key = ev["text"]
            if key in seen:
                if ev.get("skill") and ev["skill"] not in seen[key]["skill"]:
                    seen[key]["skill"] += f", {ev['skill']}"
            else:
                seen[key] = dict(ev)
        r.evidence = list(seen.values())[:4]
        strengths = [levels[s][1] for s in skills]
        if req.get("any_of"):
            r.strength = max(strengths)
            if demonstrated:
                r.status = "supported"
            elif weaker:
                r.status = "partially_supported"
            else:
                r.status = "not_evidenced"
            if r.status != "not_evidenced" and r.missing_skills:
                r.notes.append("Any one of the listed options satisfies this requirement.")
        else:
            r.strength = float(np.mean(strengths))
            if len(demonstrated) == len(skills):
                r.status = "supported"
            elif demonstrated or weaker:
                r.status = "partially_supported"
                if r.missing_skills:
                    r.notes.append("No evidence for: " + ", ".join(tax.name(s) for s in r.missing_skills) + ".")
            else:
                r.status = "not_evidenced"
        if r.status == "not_evidenced":
            r.related = _related(req_vec, prof, threshold)
            if r.related:
                r.status = "unclear"
                r.notes.append("No explicit mention, but a related resume line was found by semantic similarity. Review it — similarity is not proof.")
        # Years attached to a skill requirement ("3+ years of Python") are reported, not silently ignored.
        if years_min is not None and r.status in ("supported", "partially_supported"):
            months = max((_employment_months_with_skill(s, prof) for s in r.matched_skills), default=0.0)
            if months < years_min * 12:
                r.status = "partially_supported"
                r.notes.append(f"The posting asks for {years_min:g}+ years; roles mentioning this skill span about {months / 12:.1f} years.")
        return r

    # Soft / non-checkable requirements and responsibilities.
    r.status = "unclear"
    r.related = _related(req_vec, prof, threshold)
    if r.related:
        r.notes.append("Possibly related resume line (semantic similarity, not proof).")
    else:
        r.notes.append("Not checkable from resume text.")
    return r


def evaluate_all(extraction: dict, prof: ProfileContext, cfg: dict) -> list[ReqResult]:
    embs = extraction.get("req_embeddings") or {}
    out = []
    for req in extraction.get("requirements", []):
        vec = unpack(embs[req["id"]]) if req["id"] in embs else None
        out.append(evaluate_requirement(req, prof, vec, cfg))
    return out
