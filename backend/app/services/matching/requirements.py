"""Structured requirement extraction from job descriptions (rule-based, deterministic).

Output separates required qualifications, preferred qualifications, responsibilities and explicit
eligibility restrictions. Every item keeps its source text and character offsets in the description, plus a
confidence level describing how the classification was made:

  high   – the item sits under an explicit heading ("Minimum qualifications", "Nice to have", ...)
  medium – classification came from wording inside the item ("... is a plus", "must have ...")
  low    – the posting has no recognisable structure; the item's kind is a best guess and shown as uncertain

Job-description text is untrusted data: nothing in it is executed or followed as an instruction.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import yaml

from app.core.config import get_settings
from app.services.skills import Taxonomy, get_taxonomy
from app.services.text_safety import find_instruction_like_text

EXTRACTION_VERSION = "5"

HEADINGS: list[tuple[str, str]] = [
    # Order matters: preferred before generic "qualifications".
    ("preferred", r"preferred|you (?:might|may) also have|nice[- ]to[- ]haves?|bonus(?: points)?|pluses?|it'?s a plus|desired|additional qualifications|ideally|extra credit|good to have"),
    ("responsibility", r"about (?:the|this) (?:role|job|position|opportunity)"),
    ("other", r"about (?!you\b|the role|this role|the job|the position|the opportunity)\w+|benefits|perks|compensation|salary|pay range|equal (?:employment )?opportunity|who we are|why join|our values|what we offer|life at|accommodations?|eeo|privacy"),
    ("responsibility", r"responsibilit|what you'?ll do|what you will do|in this role|the role|day[- ]to[- ]day|your impact|you will|what you'?ll work on|the opportunity|key duties|your mission|job duties"),
    ("required", r"minimum|basic qualifications|required|requirements|qualifications|must[- ]haves?|what you'?ll (?:need|bring)|what we'?re looking for|you (?:have|bring|are|might be)|who you are|about you|skills (?:and|&) experience|experience you'?ll need|what you need|you should have|your background|we'?re looking for"),
]
# Up to two leading words are allowed ("Key qualifications", "Our requirements").
_HEADING_RX = [(k, re.compile(rf"(?i)^\W*(?:[a-z]+\s+){{0,2}}(?:{p})")) for k, p in HEADINGS]
INLINE_HEADING_RX = re.compile(r"^\W*([A-Za-z' &/]{3,40}?)\s*:\s+(\S.{14,})$")
LIST_MARKER_RX = re.compile(r"^(\s*)(?:[-*–]|\d{1,2}[.)])\s+", re.M)
SENTENCE_RX = re.compile(r".+?(?:[.!?](?=\s+[A-Z0-9“\"(])|$)", re.S)

PREFERRED_CUE = re.compile(r"(?i)\b(preferred|nice to have|nice-to-have|is a plus|are a plus|a plus\b|bonus|ideally|desirable|desired|not required|optional)\b")
REQUIRED_CUE = re.compile(r"(?i)\b(required|must have|must be|must possess|minimum of|at least|you have|you should|you'?ll need|requires?)\b")
ANY_OF_CUE = re.compile(r"(?i)\b(or|such as|e\.g\.|for example|like|one of|one or more of|at least one|any of|similar)\b|/")
YEARS_RX = re.compile(r"(?i)(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years?|yrs?)\b(?!\s+old)")
DEGREE_RX = re.compile(r"(?i)\b(ph\.?\s?d|doctorate|master'?s?|m\.?s\.?(?=[\s,/)])|msc|bachelor'?s?|b\.?s\.?(?=[\s,/)])|b\.?a\.?(?=[\s,/)])|bsc|undergraduate|graduate degree|degree)\b")
EQUIVALENT_RX = re.compile(r"(?i)or (?:equivalent|comparable|related) (?:practical |professional |work )?(?:experience|training)|or equivalent")

SPONSOR_NO = re.compile(
    r"(?i)(?:(?:unable|not able|cannot|can ?not|will not|won'?t|do(?:es)? not|are not able to|is not able to)\s+(?:to\s+)?(?:offer|provide|support|sponsor)\w*\s+(?:\w+\s+){0,3}(?:visa|sponsorship|work authorization)"
    r"|no (?:visa )?sponsorship|sponsorship (?:is )?not (?:available|offered|provided)"
    r"|without (?:the need for )?(?:current or future )?(?:visa )?sponsorship"
    r"|not eligible for (?:visa )?sponsorship)"
)
SPONSOR_YES = re.compile(
    r"(?i)(?:(?:visa|immigration|h-?1b) sponsorship (?:is )?(?:available|offered|provided|supported)"
    r"|(?:we|will|can|do|are able to) (?:offer|provide|support) (?:visa |immigration )?sponsorship"
    r"|(?:we )?sponsor (?:visas|h-?1b|work visas)|sponsorship available)"
)
CITIZENSHIP_RX = re.compile(r"(?i)(u\.?s\.? citizenship (?:is )?required|must be a u\.?s\.? citizen|security clearance|active (?:secret|top secret|ts/sci) clearance|itar|export control)")
LOCATION_REQ_RX = re.compile(r"(?i)(must (?:be located|reside|live|be based) (?:in|within|near)[^.;\n]{2,80}|(?:relocation|relocate) (?:to|is required)[^.;\n]{0,60}|(?:within commuting distance|commutable distance) (?:of|to)[^.;\n]{2,60}|\b\d\s*days? (?:a|per) week (?:in|at) (?:the|our)? ?office[^.;\n]{0,40})")

SENIORITY = [
    ("intern", r"\bintern(ship)?\b|\bco-?op\b"),
    ("entry", r"new grad|\bentry[- ]level\b|\bjunior\b|\bjr\.?\b|\bassociate\b|\bgraduate\b|(?<![\w/])(?:i|1)$|\bengineer i\b|\bl3\b|early career|university grad"),
    ("staff", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\barchitect\b|\bhead of\b|\bdirector\b|\bvp\b"),
    ("senior", r"\bsenior\b|\bsr\.?\b|\blead\b|\biii\b|\bl5\b|\bengineer 3\b"),
    ("mid", r"\bii\b|\bmid[- ]level\b|\bl4\b|\bengineer 2\b"),
]


@dataclass
class Requirement:
    id: str
    kind: str  # required | preferred | responsibility | unclear
    text: str
    start: int
    end: int
    confidence: str  # high | medium | low
    basis: str
    skills: list[str] = field(default_factory=list)
    any_of: bool = False
    implied_skills: list[str] = field(default_factory=list)
    years_min: float | None = None
    years_max: float | None = None
    degree_level: int | None = None  # 1 bachelor, 2 master, 3 phd
    degree_equivalent_ok: bool = False
    category: str = "other"  # skill | experience | degree | soft | other


@dataclass
class Eligibility:
    sponsorship: str = "unknown"  # unknown | not_available | available
    sponsorship_evidence: str | None = None
    citizenship_or_clearance: bool = False
    citizenship_evidence: str | None = None
    location_requirements: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    version: str
    requirements: list[Requirement]
    eligibility: Eligibility
    seniority: str  # intern | entry | mid | senior | staff | unknown
    seniority_basis: str
    role_families: list[str]
    structured: bool  # True when explicit section headings were found
    instruction_like_text: list[dict]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache
def role_families() -> list[dict]:
    data = yaml.safe_load((get_settings().config_dir / "roles.yaml").read_text())
    return [{"id": f["id"], "name": f["name"], "rx": [re.compile(p, re.I) for p in f["title_patterns"]]} for f in data["families"]]


def classify_role_families(title: str) -> list[str]:
    return [f["id"] for f in role_families() if any(rx.search(title) for rx in f["rx"])]


def detect_seniority(title: str, years_min: float | None) -> tuple[str, str]:
    t = title.lower().strip()
    for level, pat in SENIORITY:
        if re.search(pat, t):
            return level, f"title “{title}”"
    if years_min is not None:
        if years_min >= 8:
            return "staff", f"requires {years_min:g}+ years"
        if years_min >= 5:
            return "senior", f"requires {years_min:g}+ years"
        if years_min >= 2:
            return "mid", f"requires {years_min:g}+ years"
        return "entry", f"requires {years_min:g}+ years"
    return "unknown", "title and requirements do not state a level"


def _heading_kind(line: str) -> str | None:
    s = line.strip().strip(":").strip()
    if not s or len(s) > 70 or s.startswith("•"):
        return None
    if s.endswith(".") and len(s) > 40:
        return None
    for kind, rx in _HEADING_RX:
        if rx.search(s):
            return kind
    return None


def _items(text: str) -> list[tuple[str, int, int, str | None]]:
    """Yield (item_text, start, end, section_kind) for bullet lines and sentences, tracking headings."""
    # Normalise plain-text list markers ("- ", "* ", "1. ") to bullets; same length keeps offsets aligned.
    text = LIST_MARKER_RX.sub(lambda m: m.group(1) + "•" + " " * (len(m.group(0)) - len(m.group(1)) - 1), text)
    out: list[tuple[str, int, int, str | None]] = []
    pos = 0
    section: str | None = None
    for raw in text.split("\n"):
        start = pos
        pos += len(raw) + 1
        line = raw.strip()
        if not line:
            continue
        hk = _heading_kind(line)
        body_start = start + (len(raw) - len(raw.lstrip()))
        body = line
        if hk is None:
            m = INLINE_HEADING_RX.match(line)
            if m and not line.startswith("•"):
                hk2 = _heading_kind(m.group(1))
                if hk2 is not None:
                    section = hk2
                    body = m.group(2).strip()
                    body_start = start + raw.index(body)
        elif hk is not None:
            section = hk
            continue
        if hk is None and line.endswith(":") and len(line) <= 60 and not line.startswith("•"):
            continue  # an unrecognised heading: not itself a requirement
        if body.startswith("•"):
            stripped = re.sub(r"^•\s*", "", body)
            off = body_start + (len(body) - len(stripped))
            out.append((stripped, off, off + len(stripped), section))
            continue
        for m in SENTENCE_RX.finditer(body):
            sent = m.group(0).strip()
            if len(sent) > 6:
                s_off = body_start + m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
                out.append((sent, s_off, s_off + len(sent), section))
    return out


def _degree_level(t: str) -> int | None:
    m = DEGREE_RX.search(t)
    if not m:
        return None
    w = m.group(1).lower()
    if w.startswith(("ph", "doct")):
        return 3
    if w.startswith(("master", "m.s", "ms", "msc", "graduate")):
        return 2
    if w == "degree" and not re.search(r"(?i)bachelor|master|ph\.?d|b\.?s|m\.?s|computer science|engineering|related field|technical field", t):
        return None
    return 1


def extract(title: str, description: str, taxonomy: Taxonomy | None = None) -> Extraction:
    tax = taxonomy or get_taxonomy()
    items = _items(description)
    structured = any(sec in ("required", "preferred") for _, _, _, sec in items)
    reqs: list[Requirement] = []
    for text, start, end, sec in items:
        if sec == "other":
            continue
        skills = tax.find_ids(text)
        years = YEARS_RX.search(text)
        deg = _degree_level(text)
        pref_cue = PREFERRED_CUE.search(text)
        req_cue = REQUIRED_CUE.search(text)
        if sec in ("required", "preferred"):
            kind, conf, basis = sec, "high", "listed under a requirements heading" if sec == "required" else "listed under a preferred heading"
            if sec == "required" and pref_cue:
                kind, conf, basis = "preferred", "medium", f"wording “{pref_cue.group(0)}” inside a requirements section"
        elif sec == "responsibility":
            kind, conf, basis = "responsibility", "high", "listed under a responsibilities heading"
            if pref_cue and (skills or years or deg):
                kind, conf, basis = "preferred", "medium", f"wording “{pref_cue.group(0)}”"
            elif req_cue and (skills or years or deg) and re.search(r"(?i)\b(experience|proficien|knowledge|familiar|degree)", text):
                kind, conf, basis = "required", "medium", f"wording “{req_cue.group(0)}” in a responsibilities section"
        else:  # no heading seen yet
            if pref_cue:
                kind, conf, basis = "preferred", "medium", f"wording “{pref_cue.group(0)}”"
            elif req_cue and (skills or years or deg):
                kind, conf, basis = "required", "medium", f"wording “{req_cue.group(0)}”"
            elif skills or years or deg:
                kind, conf, basis = "unclear", "low", "the posting has no requirements heading; required vs preferred is unknown"
            else:
                continue  # narrative text without any checkable criterion
        if kind in ("required", "preferred", "unclear") and not (skills or years or deg):
            category = "soft"
        elif years and re.search(r"(?i)experience|professional|industry|working", text):
            category = "experience"
        elif deg and not skills:
            category = "degree"
        elif skills:
            category = "skill"
        else:
            category = "other"
        any_of = bool(len(skills) > 1 and ANY_OF_CUE.search(text))
        implied = sorted({i for s in skills for i in tax.implied_by(s)} - set(skills))
        reqs.append(
            Requirement(
                id=f"r{len(reqs)}",
                kind=kind,
                text=text[:600],
                start=start,
                end=end,
                confidence=conf,
                basis=basis,
                skills=skills,
                any_of=any_of,
                implied_skills=implied,
                years_min=float(years.group(1)) if years else None,
                years_max=float(years.group(2)) if years and years.group(2) else None,
                degree_level=deg,
                degree_equivalent_ok=bool(EQUIVALENT_RX.search(text)),
                category=category,
            )
        )
    elig = Eligibility()
    m = SPONSOR_NO.search(description)
    if m:
        elig.sponsorship, elig.sponsorship_evidence = "not_available", _sentence(description, m.start(), m.end())
    else:
        m = SPONSOR_YES.search(description)
        if m:
            elig.sponsorship, elig.sponsorship_evidence = "available", _sentence(description, m.start(), m.end())
    m = CITIZENSHIP_RX.search(description)
    if m:
        elig.citizenship_or_clearance, elig.citizenship_evidence = True, _sentence(description, m.start(), m.end())
    elig.location_requirements = [_sentence(description, x.start(), x.end()) for x in LOCATION_REQ_RX.finditer(description)][:3]

    req_years = [r.years_min for r in reqs if r.kind == "required" and r.years_min is not None and r.category == "experience"]
    seniority, s_basis = detect_seniority(title, min(req_years) if req_years else None)
    warnings = []
    if not structured:
        warnings.append("No requirements heading was found; required vs. preferred classification is uncertain.")
    if not any(r.kind in ("required", "unclear") for r in reqs):
        warnings.append("No checkable required qualifications were extracted from this posting.")
    flags = find_instruction_like_text(description)
    if flags:
        warnings.append("The posting contains instruction-like text. It is treated as data and does not affect scoring.")
    return Extraction(EXTRACTION_VERSION, reqs, elig, seniority, s_basis, classify_role_families(title), structured, flags, warnings)


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    b_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    b = min(b_candidates) if b_candidates else len(text)
    return text[a + 1 : b + 1].strip()[:400]
