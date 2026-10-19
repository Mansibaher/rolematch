"""Heuristic structured extraction from resume text.

Everything extracted carries character offsets into the stored resume text so the UI can show the exact source
of each claim. The parser is deliberately conservative: when a field cannot be determined it is left empty and
a review warning is produced, rather than guessed. Resume text is treated purely as data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from app.services.resume.dates import DateRange, find_date_range
from app.services.skills import Taxonomy, get_taxonomy
from app.services.text_safety import find_instruction_like_text

PARSER_VERSION = "1.2"

SECTION_ALIASES: dict[str, list[str]] = {
    "employment": [
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "relevant experience", "industry experience", "career history", "internships",
        "internship experience", "technical experience",
    ],
    "research": ["research experience", "research", "research positions"],
    "project": ["projects", "personal projects", "academic projects", "selected projects", "side projects",
                "technical projects", "project experience", "key projects"],
    "education": ["education", "academic background", "education and training", "academics"],
    "skills": ["skills", "technical skills", "technologies", "tools", "core competencies", "skills & tools",
               "skills and tools", "technical proficiencies", "tech stack", "skills & interests", "languages and tools"],
    "activity": ["leadership", "activities", "volunteer", "volunteering", "extracurricular", "involvement",
                 "leadership experience", "leadership & activities", "awards", "honors", "honors & awards",
                 "certifications", "certificates", "publications", "achievements", "interests"],
    "summary": ["summary", "profile", "objective", "about", "about me", "professional summary", "career objective"],
}
_HEADING_LOOKUP = {alias: kind for kind, aliases in SECTION_ALIASES.items() for alias in aliases}

TITLE_WORDS = re.compile(
    r"(?i)\b(engineer|developer|intern|internship|assistant|analyst|scientist|manager|lead|leader|researcher|"
    r"consultant|specialist|architect|fellow|tutor|teaching|administrator|programmer|designer|associate|"
    r"co-?op|founder|director|technician|officer|coordinator|mentor|grader|volunteer|president|member)\b"
)
ORG_WORDS = re.compile(r"(?i)\b(university|college|institute|inc\.?|llc|ltd|corp(?:oration)?|company|labs?|school|technologies|systems|group|bank|hospital|center|centre|foundation)\b")
DEGREE_RX = re.compile(
    r"(?i)\b(ph\.?\s?d\.?|doctor(?:ate)? of [a-z ]+|m\.?\s?s\.?(?=[\s,]|$)|m\.?\s?sc\.?|m\.?\s?eng\.?|m\.?\s?tech|mba|master(?:'s|s)?(?: of [a-z ]+)?|"
    r"b\.?\s?s\.?(?=[\s,]|$)|b\.?\s?sc\.?|b\.?\s?e\.?(?=[\s,]|$)|b\.?\s?tech|b\.?\s?a\.?(?=[\s,]|$)|bachelor(?:'s|s)?(?: of [a-z ]+)?|associate(?:'s)? degree|high school diploma)"
)
LOCATION_FULL_RX = re.compile(
    r"(?:Remote|Hybrid|On-?site)(?:\s*\(.*\))?|[A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+){0,3},\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)?)"
)
BULLET_RX = re.compile(r"^\s*•\s*")
GPA_RX = re.compile(r"(?i)\bGPA[:\s]*\d(?:\.\d+)?(?:\s*/\s*\d(?:\.\d+)?)?")
CONTACT_RX = re.compile(r"(?i)(@|\bhttps?://|linkedin\.com|github\.com|\(\d{3}\)|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)")


@dataclass
class Line:
    text: str
    start: int
    end: int

    @property
    def stripped(self) -> str:
        return self.text.strip()


@dataclass
class ParsedBullet:
    id: str
    text: str
    start: int
    end: int


@dataclass
class ParsedEntry:
    kind: str  # employment | project | education | activity
    title: str = ""
    organization: str = ""
    location: str = ""
    employment_type: str = "unknown"
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    date_precision: str = "unknown"
    date_text: str = ""
    degree: str = ""
    field_of_study: str = ""
    bullets: list[ParsedBullet] = field(default_factory=list)
    header_start: int = 0
    header_end: int = 0
    span_end: int = 0
    review_flags: list[str] = field(default_factory=list)
    section_heading: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat() if self.start_date else None
        d["end_date"] = self.end_date.isoformat() if self.end_date else None
        return d


@dataclass
class SkillEvidence:
    start: int
    end: int
    text: str
    context: str  # skills | employment | project | education | activity | summary | other
    entry_index: int | None
    ambiguous: bool


@dataclass
class ParsedSkill:
    canonical: str
    display_name: str
    category: str
    evidence: list[SkillEvidence]


@dataclass
class Section:
    kind: str
    heading: str
    start: int
    end: int
    lines: list[Line]


@dataclass
class ParsedResume:
    sections: list[Section]
    entries: list[ParsedEntry]
    skills: list[ParsedSkill]
    warnings: list[str]
    instruction_like_text: list[dict]


def split_lines(text: str) -> list[Line]:
    out, pos = [], 0
    for raw in text.split("\n"):
        out.append(Line(raw, pos, pos + len(raw)))
        pos += len(raw) + 1
    return out


def _heading_kind(line: str) -> str | None:
    s = line.strip().strip(":").strip()
    if not s or len(s) > 45 or BULLET_RX.match(line):
        return None
    key = re.sub(r"\s+", " ", s.lower()).replace(" and ", " & ") if "&" in s else re.sub(r"\s+", " ", s.lower())
    if key in _HEADING_LOOKUP:
        return _HEADING_LOOKUP[key]
    key2 = key.replace("&", "and")
    if key2 in _HEADING_LOOKUP:
        return _HEADING_LOOKUP[key2]
    return None


def find_sections(lines: list[Line]) -> list[Section]:
    sections: list[Section] = []
    current = Section("header", "", 0, 0, [])
    for ln in lines:
        kind = _heading_kind(ln.text)
        if kind:
            if current.lines or current.kind != "header":
                current.end = current.lines[-1].end if current.lines else current.start
                sections.append(current)
            current = Section(kind, ln.stripped, ln.start, ln.end, [])
        else:
            current.lines.append(ln)
    current.end = current.lines[-1].end if current.lines else current.start
    sections.append(current)
    return sections


def _split_header(text: str) -> list[str]:
    """Split a header line into fields. Locations like "Los Angeles, CA" are kept intact."""
    coarse = re.split(r"\s*\|\s*|\s{2,}|\s+[–—]\s+|\s+-\s+|\s+@\s+", text)
    out: list[str] = []
    for piece in coarse:
        piece = piece.strip(" ,;:")
        if not piece:
            continue
        if LOCATION_FULL_RX.fullmatch(piece):
            out.append(piece)
            continue
        # "Title, Organization" or "Title at Organization"; keep a trailing location together.
        loc = re.search(r",\s*(" + LOCATION_FULL_RX.pattern + r")$", piece)
        tail = None
        if loc and not ORG_WORDS.search(loc.group(1)):
            tail = loc.group(1)
            piece = piece[: loc.start()]
        out.extend(p.strip(" ,;:") for p in re.split(r",\s+(?=[A-Z])|\s+at\s+(?=[A-Z])", piece) if p.strip(" ,;:"))
        if tail:
            out.append(tail)
    return out


def _employment_type(title: str, heading_kind: str) -> str:
    t = title.lower()
    if re.search(r"\bintern(ship)?\b|\bco-?op\b", t):
        return "internship"
    if "part-time" in t or "part time" in t:
        return "part_time"
    if heading_kind == "research" or re.search(r"\bresearch (assistant|associate|intern)\b|\bgraduate research\b", t):
        return "research"
    if re.search(r"\b(teaching assistant|tutor|grader)\b", t):
        return "part_time"
    return "unknown"


def _apply_dates(entry: ParsedEntry, dr: DateRange | None) -> None:
    if not dr:
        return
    entry.date_text = dr.text.strip()
    entry.is_current = dr.is_current
    entry.date_precision = dr.precision
    if dr.start:
        entry.start_date = dr.start.lo()
    if dr.end:
        entry.end_date = dr.end.lo() if dr.end.month else dr.end.hi()


def _fill_header_fields(entry: ParsedEntry, header_lines: list[Line], heading_kind: str) -> None:
    joined = "  ".join(h.stripped for h in header_lines)
    dr = find_date_range(joined)
    _apply_dates(entry, dr)
    pieces: list[str] = []
    for h in header_lines:
        t = h.stripped
        if dr and dr.text.strip() and dr.text.strip() in t:
            t = t.replace(dr.text.strip(), "  ")
        t = GPA_RX.sub("  ", t)
        pieces.extend(_split_header(t))
    pieces = [p for p in pieces if p and not re.fullmatch(r"[\W\d]+", p)]
    if entry.kind != "project":
        for p in list(pieces):
            if LOCATION_FULL_RX.fullmatch(p) and not ORG_WORDS.search(p) and not TITLE_WORDS.search(p):
                if not entry.location:
                    entry.location = p
                pieces.remove(p)

    if entry.kind == "education":
        for p in pieces:
            dm = DEGREE_RX.search(p)
            if dm and not entry.degree:
                entry.degree = p
                fm = re.search(r"\bin\s+([A-Za-z&/ ]{2,80})$", p.strip())
                if fm:
                    entry.field_of_study = fm.group(1).strip()
                elif p[dm.end():].strip(" ,"):
                    entry.field_of_study = p[dm.end():].strip(" ,")
            elif re.search(r"(?i)universit|college|institute|school", p) and not entry.organization:
                entry.organization = p
        if not entry.organization and pieces:
            entry.organization = next((p for p in pieces if p != entry.degree), "")
        entry.title = entry.degree
        return

    if entry.kind == "project":
        entry.title = pieces[0] if pieces else ""
        if len(pieces) > 1:
            entry.organization = ""  # tech stack / context remains visible in the header evidence
        return

    # employment / activity
    titled = [p for p in pieces if TITLE_WORDS.search(p)]
    orgish = [p for p in pieces if ORG_WORDS.search(p) and p not in titled]
    if titled:
        entry.title = titled[0]
        rest = [p for p in pieces if p != entry.title]
        entry.organization = (orgish[0] if orgish else (rest[0] if rest else ""))
    elif pieces:
        entry.organization = pieces[0]
        entry.title = pieces[1] if len(pieces) > 1 else ""
        entry.review_flags.append("Could not tell which part is the job title; please check title and organization.")
    if entry.kind == "employment":
        entry.employment_type = _employment_type(entry.title, heading_kind)
        if heading_kind == "research":
            entry.review_flags.append(
                "Listed under research. It is counted as professional employment only if you keep it as employment; "
                "change the type to 'activity' if it was unpaid coursework research."
            )


def parse_entries(section: Section) -> list[ParsedEntry]:
    kind_map = {"employment": "employment", "research": "employment", "project": "project",
                "education": "education", "activity": "activity"}
    kind = kind_map.get(section.kind)
    if not kind:
        return []
    if kind == "education":
        return _parse_education(section)
    entries: list[ParsedEntry] = []
    header: list[Line] = []
    current: ParsedEntry | None = None
    seen_bullet = False

    def close() -> None:
        nonlocal current
        if current is not None:
            if not current.date_text and current.kind in ("employment", "education"):
                current.review_flags.append("No dates found for this entry; duration is unknown.")
            entries.append(current)
        current = None

    lines = [ln for ln in section.lines if ln.stripped]
    for ln in lines:
        is_bullet = bool(BULLET_RX.match(ln.text))
        if is_bullet:
            if current is None:
                current = ParsedEntry(
                    kind=kind,
                    header_start=header[0].start if header else ln.start,
                    header_end=header[-1].end if header else ln.start,
                    section_heading=section.heading,
                )
                _fill_header_fields(current, header, section.kind)
                header = []
            body = BULLET_RX.sub("", ln.text)
            offset = ln.start + (len(ln.text) - len(body))
            current.bullets.append(ParsedBullet(f"b{len(current.bullets)}", body.strip(), offset, ln.end))
            current.span_end = ln.end
            seen_bullet = True
            continue
        # Non-bullet line: either a wrapped bullet continuation, a header line, or free text.
        looks_like_header = bool(find_date_range(ln.text)) or (len(ln.stripped) < 90 and not ln.stripped.endswith("."))
        if current is not None and current.bullets and not looks_like_header:
            last = current.bullets[-1]
            last.text = (last.text + " " + ln.stripped).strip()
            last.end = ln.end
            current.span_end = ln.end
            continue
        if seen_bullet or (current is not None and current.bullets):
            close()
            seen_bullet = False
        if current is None:
            header.append(ln)
            if len(header) >= 2 and kind in ("education", "project", "activity") and find_date_range(" ".join(h.stripped for h in header)):
                current = ParsedEntry(kind=kind, header_start=header[0].start, header_end=header[-1].end,
                                      span_end=header[-1].end, section_heading=section.heading)
                _fill_header_fields(current, header, section.kind)
                header = []
            elif len(header) >= 3:
                current = ParsedEntry(kind=kind, header_start=header[0].start, header_end=header[-1].end,
                                      span_end=header[-1].end, section_heading=section.heading)
                _fill_header_fields(current, header, section.kind)
                header = []
        else:
            hdr_so_far = _collect_header_lines(section, current.header_start, current.header_end)
            if (current.date_text and find_date_range(ln.text)) or len(hdr_so_far) >= 3:
                # A second date (or a fourth header line) means a new entry without bullets has started.
                close()
                header = [ln]
                continue
            # Additional header line before any bullets (e.g. "Title  Dates" after "Company  Location").
            hdr = _collect_header_lines(section, current.header_start, ln.end)
            refreshed = ParsedEntry(kind=kind, header_start=current.header_start, header_end=ln.end,
                                    span_end=ln.end, section_heading=section.heading)
            _fill_header_fields(refreshed, hdr, section.kind)
            current = refreshed
    if current is None and header:
        current = ParsedEntry(kind=kind, header_start=header[0].start, header_end=header[-1].end,
                              span_end=header[-1].end, section_heading=section.heading)
        _fill_header_fields(current, header, section.kind)
    close()
    return entries


def _parse_education(section: Section) -> list[ParsedEntry]:
    """Group education lines: a new entry starts when a second institution or second degree appears."""
    groups: list[list[Line]] = []
    cur: list[Line] = []
    has_inst = has_degree = False
    for ln in (x for x in section.lines if x.stripped):
        if BULLET_RX.match(ln.text) or re.match(r"(?i)\s*(relevant )?coursework|\s*gpa\b|\s*honou?rs\b|\s*thesis\b", ln.text):
            cur.append(ln)
            continue
        is_inst = bool(re.search(r"(?i)universit|college|institute|school|academy", ln.text))
        is_degree = bool(DEGREE_RX.search(ln.text))
        if cur and ((is_inst and has_inst and not is_degree) or (is_degree and has_degree)):
            groups.append(cur)
            cur, has_inst, has_degree = [], False, False
        cur.append(ln)
        has_inst = has_inst or is_inst
        has_degree = has_degree or is_degree
    if cur:
        groups.append(cur)
    out: list[ParsedEntry] = []
    for g in groups:
        header = [ln for ln in g if not BULLET_RX.match(ln.text) and not re.match(r"(?i)\s*(relevant )?coursework", ln.text)]
        e = ParsedEntry(kind="education", header_start=g[0].start, header_end=g[-1].end, span_end=g[-1].end,
                        section_heading=section.heading)
        _fill_header_fields(e, header or g, "education")
        for ln in g:
            if ln not in header:
                body = BULLET_RX.sub("", ln.text)
                e.bullets.append(ParsedBullet(f"b{len(e.bullets)}", body.strip(), ln.end - len(body), ln.end))
        if not e.date_text:
            e.review_flags.append("No dates found for this degree.")
        out.append(e)
    return out


def _collect_header_lines(section: Section, start: int, end: int) -> list[Line]:
    return [ln for ln in section.lines if ln.stripped and start <= ln.start and ln.end <= end and not BULLET_RX.match(ln.text)]


def _context_for(pos: int, sections: list[Section], entries: list[ParsedEntry]) -> tuple[str, int | None]:
    for i, e in enumerate(entries):
        end = max(e.span_end, e.header_end)
        if e.header_start <= pos <= end:
            return e.kind, i
    for s in sections:
        if s.start <= pos <= s.end:
            if s.kind in ("skills", "summary"):
                return s.kind, None
            if s.kind == "research":
                return "employment", None
            return (s.kind if s.kind in ("employment", "project", "education", "activity") else "other"), None
    return "other", None


def extract_skills(text: str, sections: list[Section], entries: list[ParsedEntry], taxonomy: Taxonomy) -> list[ParsedSkill]:
    by_id: dict[str, ParsedSkill] = {}
    contact_spans = [(ln.start, ln.end) for ln in split_lines(text) if CONTACT_RX.search(ln.text)]
    for m in taxonomy.find(text):
        if any(a <= m.start < b for a, b in contact_spans):
            continue  # e.g. "github.com/..." in the contact line is not evidence of Git experience
        ctx, idx = _context_for(m.start, sections, entries)
        if ctx == "header":
            ctx = "other"
        sk = by_id.get(m.skill_id)
        if sk is None:
            sd = taxonomy.get(m.skill_id)
            sk = ParsedSkill(m.skill_id, sd.name if sd else m.skill_id, sd.category if sd else "other", [])
            by_id[m.skill_id] = sk
        sk.evidence.append(SkillEvidence(m.start, m.end, m.text, ctx, idx, m.ambiguous))
    # Drop ambiguous skills that only appear outside skills/employment/project context (likely false positives).
    out = []
    for sk in by_id.values():
        if all(e.ambiguous for e in sk.evidence) and not any(e.context in ("skills", "employment", "project") for e in sk.evidence):
            continue
        out.append(sk)
    order = {"skills": 0, "employment": 1, "project": 2}
    out.sort(key=lambda s: (min(order.get(e.context, 3) for e in s.evidence), min(e.start for e in s.evidence)))
    return out


def parse_resume(text: str, taxonomy: Taxonomy | None = None) -> ParsedResume:
    taxonomy = taxonomy or get_taxonomy()
    lines = split_lines(text)
    sections = find_sections(lines)
    warnings: list[str] = []
    entries: list[ParsedEntry] = []
    for s in sections:
        entries.extend(parse_entries(s))
    kinds = {s.kind for s in sections}
    if not ({"employment", "research", "project", "education"} & kinds):
        warnings.append("No standard section headings (Experience, Projects, Education) were recognised. "
                        "Review the extracted items carefully and add missing ones manually.")
    if "employment" not in kinds and "research" not in kinds:
        warnings.append("No work-experience section was found, so professional experience is reported as unknown.")
    skills = extract_skills(text, sections, entries, taxonomy)
    flags = find_instruction_like_text(text)
    if flags:
        warnings.append("The resume contains instruction-like text. It is treated as plain data and has no effect on matching.")
    return ParsedResume(sections, entries, skills, warnings, flags)
