"""Skill taxonomy: alias-aware, boundary-safe skill mention detection.

Design notes
- Matching is regex-based with *technical* word boundaries: "+", "#" and "_" count as word characters, so
  "C" never matches inside "C++" or "C#", and "Java" never matches inside "JavaScript".
- Overlapping mentions are resolved longest-first ("React Native" beats "React", "PL/SQL" beats "SQL").
- `implies` relationships give *partial* evidence only and are reported as such by the matcher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

_LEFT = r"(?<![A-Za-z0-9_+#])"
_RIGHT = r"(?![A-Za-z0-9_+#])"


@dataclass(frozen=True)
class SkillDef:
    id: str
    name: str
    category: str
    ambiguous: bool
    implies: tuple[str, ...]
    distinct_from: tuple[str, ...]
    regexes: tuple[re.Pattern[str], ...] = field(repr=False)


@dataclass(frozen=True)
class SkillMention:
    skill_id: str
    start: int
    end: int
    text: str
    ambiguous: bool


class Taxonomy:
    def __init__(self, skills: list[SkillDef]):
        self.skills = {s.id: s for s in skills}
        self._ordered = skills

    # ------------------------------------------------------------------ lookup
    def get(self, skill_id: str) -> SkillDef | None:
        return self.skills.get(skill_id)

    def name(self, skill_id: str) -> str:
        s = self.skills.get(skill_id)
        return s.name if s else skill_id

    def resolve(self, text: str) -> str | None:
        """Map a user-typed skill name to a canonical id when it is an exact alias match."""
        text = text.strip()
        if not text:
            return None
        lowered = text.lower()
        for s in self._ordered:
            if lowered in (s.id, s.name.lower()):
                return s.id
        for m in self.find(text):
            if m.start == 0 and m.end == len(text):
                return m.skill_id
        return None

    def implied_by(self, skill_id: str) -> set[str]:
        """Transitive closure of skills implied by `skill_id` (excluding itself)."""
        seen: set[str] = set()
        stack = list(self.skills[skill_id].implies) if skill_id in self.skills else []
        while stack:
            cur = stack.pop()
            if cur in seen or cur == skill_id:
                continue
            seen.add(cur)
            if cur in self.skills:
                stack.extend(self.skills[cur].implies)
        return seen

    # ------------------------------------------------------------------ matching
    def find(self, text: str) -> list[SkillMention]:
        raw: list[SkillMention] = []
        for s in self._ordered:
            for rx in s.regexes:
                for m in rx.finditer(text):
                    if m.end() > m.start():
                        raw.append(SkillMention(s.id, m.start(), m.end(), m.group(0), s.ambiguous))
        # Longest-first, then earliest; drop anything overlapping an accepted mention.
        raw.sort(key=lambda x: (-(x.end - x.start), x.start))
        accepted: list[SkillMention] = []
        taken: list[tuple[int, int]] = []
        for mention in raw:
            if any(mention.start < e and s < mention.end for s, e in taken):
                continue
            accepted.append(mention)
            taken.append((mention.start, mention.end))
        accepted.sort(key=lambda x: x.start)
        return accepted

    def find_ids(self, text: str) -> list[str]:
        out: list[str] = []
        for m in self.find(text):
            if m.skill_id not in out:
                out.append(m.skill_id)
        return out


def _compile(entry: dict) -> SkillDef:
    case_sensitive = bool(entry.get("case_sensitive", False))
    regexes: list[re.Pattern[str]] = []
    for alias in entry.get("aliases", []) or []:
        alias_rx = re.escape(str(alias)).replace(r"\ ", r"[\s\-]+")
        flags = 0 if case_sensitive else re.IGNORECASE
        regexes.append(re.compile(_LEFT + alias_rx + _RIGHT, flags))
    for pat in entry.get("patterns", []) or []:
        regexes.append(re.compile(pat))  # patterns carry their own (?i) when wanted
    return SkillDef(
        id=entry["id"],
        name=entry["name"],
        category=entry.get("category", "other"),
        ambiguous=bool(entry.get("ambiguous", False)),
        implies=tuple(entry.get("implies", []) or []),
        distinct_from=tuple(entry.get("distinct_from", []) or []),
        regexes=tuple(regexes),
    )


def load_taxonomy(path: Path) -> Taxonomy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    skills = [_compile(e) for e in data["skills"]]
    ids = [s.id for s in skills]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate skill ids in taxonomy: {sorted(dupes)}")
    known = set(ids)
    for s in skills:
        for ref in (*s.implies, *s.distinct_from):
            if ref not in known:
                raise ValueError(f"skill {s.id!r} references unknown skill {ref!r}")
    return Taxonomy(skills)


@lru_cache
def get_taxonomy() -> Taxonomy:
    return load_taxonomy(get_settings().config_dir / "skills.yaml")
