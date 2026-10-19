"""Normalisation of raw postings into RoleMatch's job fields.

Every derived field records its basis. Nothing is invented: salary appears only when the source supplies it
(structured field or an explicit range in the posting text); posting dates come only from the source.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
STATE_NAMES = {v.lower(): k for k, v in US_STATES.items()}
COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states": "United States", "united states of america": "United States", "america": "United States",
    "uk": "United Kingdom", "united kingdom": "United Kingdom", "england": "United Kingdom",
    "canada": "Canada", "ca": "Canada", "india": "India", "germany": "Germany", "ireland": "Ireland",
    "netherlands": "Netherlands", "france": "France", "spain": "Spain", "mexico": "Mexico", "brazil": "Brazil",
    "australia": "Australia", "singapore": "Singapore", "japan": "Japan", "poland": "Poland", "israel": "Israel",
}
METRO_ALIASES = {
    "sf": "San Francisco", "nyc": "New York", "la": "Los Angeles", "bay area": "San Francisco Bay Area",
    "sf bay area": "San Francisco Bay Area",
}


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "section", "article", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    # Greenhouse returns HTML-escaped HTML in `content`; unescape once if it looks escaped.
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    p = _TextExtractor()
    p.feed(raw)
    p.close()
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n•\s*\n", "\n• ", text)
    text = re.sub(r"\n\n(?=• )", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class ParsedLocation:
    raw: str
    city: str | None = None
    region: str | None = None  # US state code where recognisable
    country: str | None = None
    remote: bool = False

    def as_dict(self) -> dict:
        return {"raw": self.raw, "city": self.city, "region": self.region, "country": self.country, "remote": self.remote}


def parse_location(raw: str) -> ParsedLocation:
    loc = ParsedLocation(raw=raw.strip())
    s = raw.strip()
    if re.search(r"(?i)\bremote\b|\banywhere\b|\bdistributed\b", s):
        loc.remote = True
    cleaned = re.sub(r"(?i)\b(remote|hybrid|on-?site|anywhere|in-office|office)\b", " ", s)
    cleaned = re.sub(r"[()\[\]]", " ", cleaned)
    parts = [p.strip(" -–—/") for p in re.split(r",|\s-\s|\s–\s|/", cleaned) if p.strip(" -–—/")]
    for i, part in enumerate(parts):
        low = part.lower()
        nxt = parts[i + 1] if i + 1 < len(parts) else ""
        if low in STATE_NAMES and nxt.upper() in US_STATES and len(nxt) == 2 and not loc.city:
            loc.city = part  # "New York, NY": city named like its state
            continue
        if low in COUNTRY_ALIASES and not (low == "ca" and loc.city):
            loc.country = COUNTRY_ALIASES[low]
        elif part.upper() in US_STATES and len(part) == 2:
            loc.region = part.upper()
            loc.country = loc.country or "United States"
        elif low in STATE_NAMES:
            loc.region = STATE_NAMES[low]
            loc.country = loc.country or "United States"
        elif low in METRO_ALIASES:
            loc.city = METRO_ALIASES[low]
        elif not loc.city and re.fullmatch(r"[A-Za-z .'-]{2,40}", part):
            loc.city = part.title() if part.islower() else part
    return loc


def split_locations(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r";|\s\|\s|\n|\bor\b", raw or "") if p.strip()]


REMOTE_RX = re.compile(r"(?i)\b(fully remote|remote[- ]first|100% remote|remote position|this (?:role|position) is remote|work from anywhere|remote\b)")
HYBRID_RX = re.compile(r"(?i)\bhybrid\b|\b\d\s*days? (?:a|per) week in (?:the )?office\b|\bin[- ]office \d")
ONSITE_RX = re.compile(r"(?i)\bon-?site\b|\bin[- ]person\b|\bin the office five days\b|\bfully in[- ]office\b")
RESTRICTION_RX = re.compile(
    r"(?i)(?:must (?:be located|reside|live) (?:in|within) (?:the )?([A-Z][A-Za-z .]+?)(?:[.,;]|$)"
    r"|remote\s*[-–(]\s*(US|USA|U\.S\.|United States|Canada|UK|EU|Europe|India)\b"
    r"|remote within (?:the )?([A-Z][A-Za-z .]+?)(?:[.,;]|$)"
    r"|(US|USA|U\.S\.|United States|Canada|UK)[- ]based remote)"
)


def detect_remote(location_raw: str, text: str, structured: str | None = None) -> tuple[str, str, str | None]:
    """Return (remote_type, basis, restriction)."""
    restriction = None
    m = RESTRICTION_RX.search(location_raw + "\n" + text)
    if m:
        restriction = next(g for g in m.groups() if g).strip()
    if structured:
        st = structured.lower()
        mapping = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite", "in-office": "onsite"}
        if st in mapping:
            return mapping[st], "source field", restriction
    if re.search(r"(?i)\bhybrid\b", location_raw):
        return "hybrid", "location field", restriction
    if re.search(r"(?i)\bremote\b", location_raw):
        return "remote", "location field", restriction
    head = text[:4000]
    if HYBRID_RX.search(head):
        return "hybrid", "description text", restriction
    if ONSITE_RX.search(head):
        return "onsite", "description text", restriction
    if REMOTE_RX.search(head) and not re.search(r"(?i)\bnot (?:a )?remote\b|\bno remote\b", head):
        return "remote", "description text", restriction
    return "unknown", "not stated", restriction


def detect_employment_type(title: str, text: str, structured: str | None = None) -> tuple[str, str]:
    if structured:
        s = structured.lower()
        if "intern" in s:
            return "internship", "source field"
        if "contract" in s or "temporary" in s or "freelance" in s:
            return "contract", "source field"
        if "part" in s:
            return "part_time", "source field"
        if "full" in s or "permanent" in s or "regular" in s:
            return "full_time", "source field"
    if re.search(r"(?i)\bintern(ship)?\b|\bco-?op\b", title):
        return "internship", "title"
    if re.search(r"(?i)\bcontract(or)?\b|\bfreelance\b|\btemporary\b", title):
        return "contract", "title"
    head = text[:3000]
    if re.search(r"(?i)\bfull[- ]time\b", head):
        return "full_time", "description text"
    if re.search(r"(?i)\bpart[- ]time\b", head):
        return "part_time", "description text"
    if re.search(r"(?i)\b(?:\d+[- ]month )?contract (?:role|position)\b|\bW-?2 contract\b|\b1099\b", head):
        return "contract", "description text"
    return "unknown", "not stated"


_MONEY = r"(?:(?P<cur>[$£€])\s?|(?P<code>USD|CAD|EUR|GBP|INR)\s?)(?P<num>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<k>[kK])?"
SALARY_RX = re.compile(
    _MONEY + r"\s*(?:-|–|—|to)\s*(?:[$£€]|USD|CAD|EUR|GBP|INR)?\s?(?P<num2>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<k2>[kK])?"
    r"(?:\s*(?:USD|CAD|EUR|GBP))?(?:\s*(?:per|/|an?)\s*(?P<per>year|yr|annum|annually|hour|hr|month))?"
)
CUR_SYMBOL = {"$": "USD", "£": "GBP", "€": "EUR"}


@dataclass
class Salary:
    min: float
    max: float
    currency: str
    interval: str
    basis: str


def _num(s: str, k: str | None) -> float:
    v = float(s.replace(",", ""))
    return v * 1000 if k else v


def extract_salary_from_text(text: str) -> Salary | None:
    for m in SALARY_RX.finditer(text):
        lo, hi = _num(m.group("num"), m.group("k")), _num(m.group("num2"), m.group("k2") or m.group("k"))
        if hi < lo:
            continue
        per = (m.group("per") or "").lower()
        interval = "hour" if per in ("hour", "hr") else "month" if per == "month" else "year"
        if interval == "year" and hi < 1000:
            continue  # "$5 - $10" etc. is not an annual salary
        currency = CUR_SYMBOL.get(m.group("cur") or "", m.group("code") or "USD")
        if "$" == m.group("cur") and re.search(r"\bCAD\b", m.group(0)):
            currency = "CAD"
        excerpt = text[max(0, m.start() - 40) : m.end() + 20].replace("\n", " ").strip()
        return Salary(lo, hi, currency, interval, f"posting text: “{excerpt}”")
    return None


def content_hash(*parts: str | None) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode())
        h.update(b"\x1f")
    return h.hexdigest()


@dataclass
class NormalizedJob:
    source_job_id: str
    employer: str
    title: str
    description_text: str
    description_html: str | None = None
    department: str | None = None
    requisition_id: str | None = None
    location_raw: str = ""
    locations: list[dict] = field(default_factory=list)
    remote_type: str = "unknown"
    remote_basis: str = "not stated"
    remote_restriction: str | None = None
    employment_type: str = "unknown"
    employment_type_basis: str = "not stated"
    salary: Salary | None = None
    posted_at: datetime | None = None
    posted_at_basis: str | None = None
    source_updated_at: datetime | None = None
    apply_url: str | None = None
    canonical_url: str | None = None
    raw_payload: dict | None = None

    def fingerprint(self) -> str:
        return content_hash(self.title, self.description_text, self.location_raw, self.employment_type, self.apply_url)


def finish(job: NormalizedJob, structured_workplace: str | None = None, structured_commitment: str | None = None) -> NormalizedJob:
    """Fill derived fields (locations, remote, employment type, salary-from-text) consistently for all adapters."""
    job.locations = [parse_location(x).as_dict() for x in split_locations(job.location_raw)] if job.location_raw else []
    job.remote_type, job.remote_basis, job.remote_restriction = detect_remote(job.location_raw, job.description_text, structured_workplace)
    job.employment_type, job.employment_type_basis = detect_employment_type(job.title, job.description_text, structured_commitment)
    if job.salary is None:
        job.salary = extract_salary_from_text(job.description_text)
    return job
