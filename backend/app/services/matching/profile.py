"""The matching view of a user's verified profile (built from an evidence-eligible resume version)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
from sqlalchemy.orm import Session

from app.models import ProfileEntry, ResumeVersion
from app.services.embeddings.backend import get_embedder
from app.services.embeddings.store import pack, unpack
from app.services.resume.service import experience_for
from app.services.skills import get_taxonomy

DEGREE_LEVEL_RX = [
    (3, re.compile(r"(?i)\bph\.?\s?d|doctor")),
    (2, re.compile(r"(?i)\bmaster|\bm\.?\s?s\.?\b|\bm\.?\s?sc|\bm\.?\s?eng|\bm\.?\s?tech|\bmba\b")),
    (1, re.compile(r"(?i)\bbachelor|\bb\.?\s?s\.?\b|\bb\.?\s?sc|\bb\.?\s?e\.?\b|\bb\.?\s?tech|\bb\.?\s?a\.?\b")),
]
RELEVANT_FIELD_RX = re.compile(r"(?i)comput|software|electrical|electronic|engineering|math|statistic|data|information|physics|machine learning|artificial intelligence")


@dataclass
class Evidence:
    text: str  # the exact resume excerpt (or a user-provided note)
    context: str  # employment | project | skills | education | activity | summary | other | user_added
    start: int | None = None
    end: int | None = None
    location: dict | None = None
    entry_title: str | None = None
    entry_id: str | None = None


@dataclass
class SkillEvidence:
    canonical: str
    name: str
    strength: float
    evidence: list[Evidence]
    contexts: set[str]


@dataclass
class ProfileItem:
    key: str
    text: str
    entry: ProfileEntry
    start: int | None
    end: int | None


@dataclass
class ProfileContext:
    version_id: str
    raw_text: str
    skills: dict[str, SkillEvidence]
    entries: list[ProfileEntry]
    experience: dict
    degrees: list[dict]
    items: list[ProfileItem]
    item_vectors: np.ndarray
    profile_vector: np.ndarray
    content_key: str
    as_of: date
    strengths: dict = field(default_factory=dict)


def _excerpt(raw: str, start: int, end: int, pad: int = 90) -> str:
    """The full resume line containing [start, end) — what the user will recognise as the source."""
    a = raw.rfind("\n", 0, start) + 1
    b = raw.find("\n", end)
    b = len(raw) if b == -1 else b
    line = raw[a:b].strip().lstrip("•").strip()
    if len(line) > 2 * pad + (end - start):
        s = max(0, start - a - pad)
        line = ("…" if s else "") + raw[a + s : min(b, end + pad)].strip() + "…"
    return line


def _degree_level(text: str) -> int | None:
    for level, rx in DEGREE_LEVEL_RX:
        if rx.search(text):
            return level
    return None


def build_profile(db: Session, version: ResumeVersion, strengths: dict, as_of: date | None = None) -> ProfileContext:
    as_of = as_of or datetime.now(UTC).date()
    tax = get_taxonomy()
    entries = [e for e in version.entries if not e.excluded]
    by_id = {str(e.id): e for e in entries}
    skills: dict[str, SkillEvidence] = {}
    for s in version.skills:
        if s.removed:
            continue
        evs: list[Evidence] = []
        contexts: set[str] = set()
        for ev in s.evidence or []:
            entry = by_id.get(ev.get("entry_id") or "")
            ctx = ev["context"]
            if entry is not None:
                ctx = entry.kind if entry.kind in ("employment", "project", "education", "activity") else ctx
            elif ev.get("entry_id"):
                continue  # evidence from an entry the user excluded
            contexts.add(ctx)
            evs.append(Evidence(_excerpt(version.raw_text, ev["start"], ev["end"]), ctx, ev["start"], ev["end"],
                                ev.get("location"), entry.title if entry else None, str(entry.id) if entry else None))
        if s.source == "user_added":
            contexts.add("user_added")
            evs.append(Evidence(s.user_note or "Added and verified by you (not in resume text)", "user_added"))
        if not evs:
            continue
        strength = max(strengths.get(c, 0.4) for c in contexts)
        # Order evidence: strongest context first.
        evs.sort(key=lambda e: -strengths.get(e.context, 0.4))
        skills[s.canonical] = SkillEvidence(s.canonical, tax.name(s.canonical) if not s.canonical.startswith("custom:") else s.display_name, strength, evs, contexts)

    degrees = []
    for e in entries:
        if e.kind == "education":
            lvl = _degree_level(e.degree or e.title)
            if lvl:
                degrees.append({
                    "level": lvl, "text": e.degree or e.title, "field": e.field_of_study, "organization": e.organization,
                    "end_date": e.end_date, "in_progress": bool(e.is_current or (e.end_date and e.end_date > as_of)),
                    "entry_id": str(e.id), "start": (e.evidence or {}).get("start"), "end": (e.evidence or {}).get("end"),
                    "relevant_field": bool(RELEVANT_FIELD_RX.search((e.field_of_study or "") + " " + (e.degree or ""))),
                })

    items: list[ProfileItem] = []
    for e in entries:
        if e.kind not in ("employment", "project", "education", "activity"):
            continue
        header = " — ".join(x for x in (e.title, e.organization) if x)
        if header:
            items.append(ProfileItem(f"{e.id}:h", header, e, (e.evidence or {}).get("start"), (e.evidence or {}).get("end")))
        for b in e.bullets or []:
            items.append(ProfileItem(f"{e.id}:{b['id']}", b["text"], e, b.get("start"), b.get("end")))

    content = json.dumps(
        {
            "v": str(version.id),
            "skills": sorted((k, round(v.strength, 2), sorted(v.contexts)) for k, v in skills.items()),
            "entries": [(str(e.id), e.kind, e.employment_type, str(e.start_date), str(e.end_date), e.is_current, e.date_precision, e.title) for e in entries],
            "as_of": as_of.isoformat(),
        },
        sort_keys=True,
    )
    content_key = hashlib.sha256(content.encode()).hexdigest()

    item_vectors, profile_vector = _profile_vectors(db, version, items)
    return ProfileContext(
        version_id=str(version.id), raw_text=version.raw_text, skills=skills, entries=entries,
        experience=experience_for(version, as_of), degrees=degrees, items=items, item_vectors=item_vectors,
        profile_vector=profile_vector, content_key=content_key, as_of=as_of, strengths=strengths,
    )


def _profile_vectors(db: Session, version: ResumeVersion, items: list[ProfileItem]) -> tuple[np.ndarray, np.ndarray]:
    emb = get_embedder()
    texts = [i.text for i in items]
    key = hashlib.sha256(("\x1f".join(texts) + emb.name).encode()).hexdigest()
    cache = version.embedding_cache or {}
    if cache.get("key") == key and cache.get("model") == emb.name:
        vecs = np.stack([unpack(v) for v in cache["items"]]) if cache["items"] else np.zeros((0, emb.dim), np.float32)
        prof = unpack(cache["profile"])
        return vecs, prof
    vecs = emb.embed(texts) if texts else np.zeros((0, emb.dim), np.float32)
    prof = vecs.mean(0) if len(texts) else np.zeros(emb.dim, np.float32)
    n = np.linalg.norm(prof)
    prof = prof / n if n else prof
    version.embedding_cache = {"key": key, "model": emb.name, "items": [pack(v) for v in vecs], "profile": pack(prof)}
    db.commit()
    return vecs, prof
