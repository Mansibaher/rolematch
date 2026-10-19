"""Resume versions and the editable, provenance-preserving profile."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Preferences,
    ProfileEditLog,
    ProfileEntry,
    ProfileSkill,
    Resume,
    ResumeVersion,
    StoredFile,
    User,
)
from app.services.resume.experience import EntryDates, summarize
from app.services.resume.extract import ExtractionResult, detect_kind, extract_text, locate, normalize_pasted_text
from app.services.resume.parse import PARSER_VERSION, parse_resume
from app.services.skills import get_taxonomy
from app.services.text_safety import find_instruction_like_text

ENTRY_EDITABLE = (
    "kind", "title", "organization", "location", "employment_type", "start_date", "end_date",
    "is_current", "date_precision", "degree", "field_of_study", "excluded",
)
ENTRY_KINDS = ("employment", "project", "education", "activity")
EMPLOYMENT_TYPES = ("full_time", "internship", "part_time", "research", "contract", "unknown")


def _store_file(user: User, filename: str, content_type: str, data: bytes) -> StoredFile:
    settings = get_settings()
    file_id = uuid.uuid4()
    ext = os.path.splitext(filename)[1].lower()[:10]
    rel = f"{user.id}/{file_id}{ext}"
    path = settings.upload_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return StoredFile(
        id=file_id,
        user_id=user.id,
        original_filename=os.path.basename(filename)[:255],
        content_type=content_type[:100],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=rel,
    )


def _next_version_number(db: Session, resume_id: uuid.UUID) -> int:
    cur = db.scalar(select(func.max(ResumeVersion.version_number)).where(ResumeVersion.resume_id == resume_id))
    return (cur or 0) + 1


def _get_or_create_resume(db: Session, user: User, resume_id: uuid.UUID | None, label: str) -> Resume:
    if resume_id:
        r = db.get(Resume, resume_id)
        if r is None or r.user_id != user.id:
            raise LookupError("resume")
        return r
    r = Resume(user_id=user.id, label=label or "My resume")
    db.add(r)
    db.flush()
    return r


def _persist_parse(db: Session, version: ResumeVersion) -> None:
    parsed = parse_resume(version.raw_text)
    version.parser_version = PARSER_VERSION
    version.extraction_warnings = list(version.extraction_warnings or []) + parsed.warnings
    entry_ids: list[uuid.UUID] = []
    for i, e in enumerate(parsed.entries):
        d = e.to_dict()
        pe = ProfileEntry(
            id=uuid.uuid4(),
            version_id=version.id,
            user_id=version.user_id,
            position=i,
            kind=e.kind,
            title=e.title[:300],
            organization=e.organization[:300],
            location=e.location[:200],
            employment_type=e.employment_type,
            start_date=e.start_date,
            end_date=e.end_date,
            is_current=e.is_current,
            date_precision=e.date_precision,
            date_text=e.date_text[:120],
            degree=e.degree[:200],
            field_of_study=e.field_of_study[:200],
            bullets=[{"id": b.id, "text": b.text, "start": b.start, "end": b.end} for b in e.bullets],
            evidence={
                "start": e.header_start,
                "end": e.header_end,
                "location": locate(version.segments, e.header_start),
                "section": e.section_heading,
                "review_flags": e.review_flags,
            },
            source="extracted",
            extracted=d,
        )
        db.add(pe)
        entry_ids.append(pe.id)
    for i, s in enumerate(parsed.skills):
        db.add(
            ProfileSkill(
                version_id=version.id,
                user_id=version.user_id,
                position=i,
                canonical=s.canonical,
                display_name=s.display_name,
                category=s.category,
                source="extracted",
                evidence=[
                    {
                        "start": ev.start,
                        "end": ev.end,
                        "text": ev.text,
                        "context": ev.context,
                        "entry_id": str(entry_ids[ev.entry_index]) if ev.entry_index is not None else None,
                        "ambiguous": ev.ambiguous,
                        "location": locate(version.segments, ev.start),
                    }
                    for ev in s.evidence
                ],
            )
        )
    version.tailoring_record = None
    if parsed.instruction_like_text:
        version.extraction_warnings = version.extraction_warnings + [
            {"kind": "instruction_like_text", "spans": parsed.instruction_like_text}
        ]


def create_version(
    db: Session,
    user: User,
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes | None,
    pasted_text: str | None,
    label: str = "",
    resume_id: uuid.UUID | None = None,
) -> ResumeVersion:
    """Create a new immutable resume version from an upload or pasted text and extract its profile."""
    stored: StoredFile | None = None
    if data is not None:
        kind = detect_kind(filename or "", content_type, data)  # raises UploadRejected
        result: ExtractionResult = extract_text(kind, data)
        stored = _store_file(user, filename or f"resume.{kind}", content_type or "", data)
        origin = "upload"
    else:
        result = normalize_pasted_text(pasted_text or "")
        origin = "pasted"
    resume = _get_or_create_resume(db, user, resume_id, label)
    if stored:
        db.add(stored)
        db.flush()
    version = ResumeVersion(
        id=uuid.uuid4(),
        resume_id=resume.id,
        user_id=user.id,
        version_number=_next_version_number(db, resume.id),
        origin=origin,
        file_id=stored.id if stored else None,
        raw_text=result.text,
        segments=result.segments,
        extraction_status=result.status,
        extraction_warnings=list(result.warnings),
    )
    db.add(version)
    db.flush()
    if result.status in ("ok", "partial"):
        _persist_parse(db, version)
    db.commit()
    db.refresh(version)
    return version


def owned_version(db: Session, user: User, version_id: uuid.UUID) -> ResumeVersion | None:
    v = db.get(ResumeVersion, version_id)
    if v is None or v.user_id != user.id:
        return None
    return v


def _entry_dict(e: ProfileEntry) -> dict:
    return {
        "id": str(e.id),
        "position": e.position,
        "kind": e.kind,
        "title": e.title,
        "organization": e.organization,
        "location": e.location,
        "employment_type": e.employment_type,
        "start_date": e.start_date.isoformat() if e.start_date else None,
        "end_date": e.end_date.isoformat() if e.end_date else None,
        "is_current": e.is_current,
        "date_precision": e.date_precision,
        "date_text": e.date_text,
        "degree": e.degree,
        "field_of_study": e.field_of_study,
        "bullets": e.bullets,
        "evidence": e.evidence,
        "source": e.source,
        "user_edited": e.user_edited,
        "excluded": e.excluded,
        "extracted": e.extracted,
    }


def _skill_dict(s: ProfileSkill) -> dict:
    return {
        "id": str(s.id),
        "canonical": s.canonical,
        "display_name": s.display_name,
        "category": s.category,
        "source": s.source,
        "evidence": s.evidence,
        "user_note": s.user_note,
        "removed": s.removed,
        "verified_at": s.verified_at.isoformat() if s.verified_at else None,
        "contexts": sorted({e["context"] for e in s.evidence}) if s.evidence else (["user_added"] if s.source == "user_added" else []),
    }


def experience_for(version: ResumeVersion, as_of: date | None = None) -> dict:
    as_of = as_of or datetime.now(UTC).date()
    return summarize(
        [
            EntryDates(str(e.id), e.kind, e.employment_type, e.title, e.start_date, e.end_date, e.is_current, e.date_precision, e.excluded)
            for e in version.entries
        ],
        as_of,
    )


def profile_payload(version: ResumeVersion) -> dict:
    warnings = [w for w in version.extraction_warnings if isinstance(w, str)]
    injection = next((w["spans"] for w in version.extraction_warnings if isinstance(w, dict) and w.get("kind") == "instruction_like_text"), [])
    return {
        "version": {
            "id": str(version.id),
            "resume_id": str(version.resume_id),
            "version_number": version.version_number,
            "origin": version.origin,
            "created_at": version.created_at.isoformat(),
            "reviewed_at": version.reviewed_at.isoformat() if version.reviewed_at else None,
            "extraction_status": version.extraction_status,
            "evidence_eligible": version.evidence_eligible,
            "parent_version_id": str(version.parent_version_id) if version.parent_version_id else None,
            "parser_version": version.parser_version,
        },
        "raw_text": version.raw_text,
        "segments": version.segments,
        "warnings": warnings,
        "instruction_like_text": injection,
        "entries": [_entry_dict(e) for e in version.entries],
        "skills": [_skill_dict(s) for s in version.skills],
        "experience": experience_for(version),
    }


def _log(db: Session, version: ResumeVersion, kind: str, target_id: uuid.UUID | None, action: str, before: dict | None, after: dict | None) -> None:
    db.add(ProfileEditLog(version_id=version.id, user_id=version.user_id, target_kind=kind, target_id=target_id,
                          action=action, before=before, after=after))


def _coerce(field: str, value):
    if field in ("start_date", "end_date"):
        return date.fromisoformat(value) if value else None
    if field == "kind" and value not in ENTRY_KINDS:
        raise ValueError(f"kind must be one of {ENTRY_KINDS}")
    if field == "employment_type" and value not in EMPLOYMENT_TYPES:
        raise ValueError(f"employment_type must be one of {EMPLOYMENT_TYPES}")
    if field == "date_precision" and value not in ("month", "year", "unknown"):
        raise ValueError("date_precision must be month, year or unknown")
    return value


def update_entry(db: Session, version: ResumeVersion, entry: ProfileEntry, changes: dict) -> ProfileEntry:
    before = _entry_dict(entry)
    for k, v in changes.items():
        if k in ENTRY_EDITABLE:
            setattr(entry, k, _coerce(k, v))
    entry.user_edited = True
    after = _entry_dict(entry)
    _log(db, version, "entry", entry.id, "update",
         {k: before[k] for k in changes if k in before}, {k: after[k] for k in changes if k in after})
    db.commit()
    return entry


def add_entry(db: Session, version: ResumeVersion, data: dict) -> ProfileEntry:
    pos = max((e.position for e in version.entries), default=-1) + 1
    bullets = [{"id": f"u{i}", "text": t.strip(), "start": None, "end": None, "user_added": True}
               for i, t in enumerate(data.get("bullets") or []) if t and t.strip()]
    e = ProfileEntry(
        version_id=version.id, user_id=version.user_id, position=pos, kind=_coerce("kind", data.get("kind", "employment")),
        title=data.get("title", "")[:300], organization=data.get("organization", "")[:300], location=data.get("location", "")[:200],
        employment_type=_coerce("employment_type", data.get("employment_type", "unknown")),
        start_date=_coerce("start_date", data.get("start_date")), end_date=_coerce("end_date", data.get("end_date")),
        is_current=bool(data.get("is_current", False)), date_precision=_coerce("date_precision", data.get("date_precision", "month")),
        degree=data.get("degree", "")[:200], field_of_study=data.get("field_of_study", "")[:200],
        bullets=bullets, evidence={}, source="user_added", user_edited=True, extracted=None,
    )
    db.add(e)
    db.flush()
    _log(db, version, "entry", e.id, "add", None, _entry_dict(e))
    db.commit()
    return e


def add_skill(db: Session, version: ResumeVersion, name: str, note: str = "") -> ProfileSkill:
    tax = get_taxonomy()
    canonical = tax.resolve(name)
    display = tax.name(canonical) if canonical else name.strip()[:120]
    category = tax.get(canonical).category if canonical and tax.get(canonical) else "other"
    if canonical is None:
        canonical = "custom:" + "-".join(name.lower().split())[:100]
    existing = next((s for s in version.skills if s.canonical == canonical), None)
    now = datetime.now(UTC)
    if existing:
        before = _skill_dict(existing)
        existing.removed = False
        existing.verified_at = now
        if note:
            existing.user_note = note
        _log(db, version, "skill", existing.id, "restore", before, _skill_dict(existing))
        db.commit()
        return existing
    pos = max((s.position for s in version.skills), default=-1) + 1
    s = ProfileSkill(version_id=version.id, user_id=version.user_id, position=pos, canonical=canonical, display_name=display,
                     category=category, source="user_added", evidence=[], user_note=note, verified_at=now)
    db.add(s)
    db.flush()
    _log(db, version, "skill", s.id, "add", None, _skill_dict(s))
    db.commit()
    return s


def set_skill_removed(db: Session, version: ResumeVersion, skill: ProfileSkill, removed: bool) -> ProfileSkill:
    before = _skill_dict(skill)
    skill.removed = removed
    _log(db, version, "skill", skill.id, "remove" if removed else "restore", before, _skill_dict(skill))
    db.commit()
    return skill


def mark_reviewed(db: Session, user: User, version: ResumeVersion) -> None:
    if not version.evidence_eligible:
        raise ValueError("Tailored versions cannot be used as the matching profile.")
    if version.extraction_status not in ("ok", "partial"):
        raise ValueError("This version has no usable text. Upload another file or paste the text.")
    version.reviewed_at = datetime.now(UTC)
    prefs = db.get(Preferences, user.id)
    if prefs is None:
        prefs = Preferences(user_id=user.id, hard_filters={}, feedback_adjustments={})
        db.add(prefs)
    prefs.active_resume_version_id = version.id
    db.commit()


def delete_resume(db: Session, user: User, resume: Resume) -> int:
    """Delete a resume, all its versions, profile records and stored files. Returns files removed."""
    settings = get_settings()
    removed = 0
    file_ids = [v.file_id for v in resume.versions if v.file_id]
    db.delete(resume)
    db.flush()
    for fid in file_ids:
        f = db.get(StoredFile, fid)
        if f and f.user_id == user.id:
            p = settings.upload_dir / f.storage_key
            if p.exists():
                p.unlink()
                removed += 1
            db.delete(f)
    db.commit()
    return removed


def instruction_flags(text: str) -> list[dict]:
    return find_instruction_like_text(text)
