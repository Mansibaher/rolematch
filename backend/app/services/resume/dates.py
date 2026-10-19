"""Date-range parsing for resume entries, with explicit precision and uncertainty bounds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_YEAR = r"(?:19|20)\d{2}"
_POINT = rf"(?:{_MONTH}\s*,?\s*'?{_YEAR}|{_SEASON}\s+{_YEAR}|(?:0?[1-9]|1[0-2])\s*[/.-]\s*{_YEAR}|{_YEAR})"
_OPEN = r"(?:present|current|now|ongoing|today|date)"
_SEP = r"\s*(?:-|–|—|to|until|through|~)\s*"

RANGE_RX = re.compile(rf"(?i)(?:expected\s+)?(?P<start>{_POINT}){_SEP}(?P<end>{_POINT}|{_OPEN})")
SINGLE_RX = re.compile(rf"(?i)(?P<expected>expected\s+|anticipated\s+|graduat\w*\s+)?(?P<point>{_POINT})")


@dataclass(frozen=True)
class DatePoint:
    year: int
    month: int | None  # None => year precision
    approx: bool = False  # seasons

    @property
    def precision(self) -> str:
        return "month" if self.month else "year"

    def lo(self) -> date:
        return date(self.year, self.month or 1, 1)

    def hi(self) -> date:
        return date(self.year, self.month or 12, 1)

    def mid(self) -> date:
        return date(self.year, self.month or 7, 1)


@dataclass(frozen=True)
class DateRange:
    start: DatePoint | None
    end: DatePoint | None
    is_current: bool
    text: str
    span: tuple[int, int]

    @property
    def precision(self) -> str:
        pts = [p for p in (self.start, self.end) if p]
        if not pts:
            return "unknown"
        if any(p.month is None or p.approx for p in pts) or (self.start is None):
            return "year"
        return "month"


def parse_point(s: str) -> DatePoint | None:
    s = s.strip().lower().replace("'", "").rstrip(".")
    m = re.fullmatch(rf"({_MONTH})\s*,?\s*({_YEAR})", s)
    if m:
        return DatePoint(int(m.group(2)), MONTHS[m.group(1).rstrip(".")[:3]])
    m = re.fullmatch(rf"({_SEASON})\s+({_YEAR})", s)
    if m:
        return DatePoint(int(m.group(2)), SEASONS[m.group(1)], approx=True)
    m = re.fullmatch(rf"(0?[1-9]|1[0-2])\s*[/.-]\s*({_YEAR})", s)
    if m:
        return DatePoint(int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(_YEAR, s)
    if m:
        return DatePoint(int(s), None)
    return None


def find_date_range(text: str) -> DateRange | None:
    m = RANGE_RX.search(text)
    if m:
        start = parse_point(m.group("start"))
        end_raw = m.group("end")
        is_current = bool(re.fullmatch(_OPEN, end_raw.strip(), re.I))
        end = None if is_current else parse_point(end_raw)
        return DateRange(start, end, is_current, m.group(0), m.span())
    m = SINGLE_RX.search(text)
    if m:
        pt = parse_point(m.group("point"))
        if pt and pt.approx and pt.month == 6:
            # "Summer 2023" – treat as an approximate June–August window (precision stays "year").
            return DateRange(pt, DatePoint(pt.year, 8, approx=True), False, m.group(0), m.span())
        if pt:
            # A single date on an entry usually marks completion (graduation / project end).
            return DateRange(None, pt, False, m.group(0), m.span())
    return None


def months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0
