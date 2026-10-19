from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk


class StoredFile(UUIDPk, Timestamps, Base):
    __tablename__ = "stored_files"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)  # relative to upload_dir


class Resume(UUIDPk, Timestamps, Base):
    """A logical resume (e.g. "Backend resume"). Holds immutable versions."""

    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    versions: Mapped[list[ResumeVersion]] = relationship(
        back_populates="resume", cascade="all, delete-orphan", order_by="ResumeVersion.version_number"
    )


class ResumeVersion(UUIDPk, Timestamps, Base):
    """An immutable snapshot of resume text.

    origin:
      upload   – text extracted from an uploaded file (or pasted text)
      tailored – produced by the tailoring assistant from a parent version. Tailored wording is NEVER
                 used as matching evidence; matching always uses the parent's verified profile.
    """

    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("resume_id", "version_number"),)

    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # upload | pasted | tailored
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL"))
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="SET NULL"))
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Character spans of pages (PDF) or paragraphs (DOCX): [{"kind": "page", "index": 0, "start": 0, "end": 812}]
    segments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(30), nullable=False)  # ok | partial | needs_text | failed
    extraction_warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(20), default="1", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Only versions derived from the user's real resume may supply matching evidence.
    evidence_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tailored_for_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    tailoring_record: Mapped[dict | None] = mapped_column(JSONB)
    # Cached embeddings of profile items for semantic matching: {"model": str, "key": str, "items": {id: b64}}
    embedding_cache: Mapped[dict | None] = mapped_column(JSONB)

    resume: Mapped[Resume] = relationship(back_populates="versions")
    entries: Mapped[list[ProfileEntry]] = relationship(back_populates="version", cascade="all, delete-orphan", order_by="ProfileEntry.position")
    skills: Mapped[list[ProfileSkill]] = relationship(back_populates="version", cascade="all, delete-orphan", order_by="ProfileSkill.position")


class ProfileEntry(UUIDPk, Timestamps, Base):
    """A structured item extracted from a resume version: employment, project, education, or activity.

    Projects, education and activities are never counted as professional employment.
    `extracted` keeps the original parser output so user corrections preserve provenance.
    """

    __tablename__ = "profile_entries"

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # employment | project | education | activity
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    organization: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    employment_type: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)  # full_time|internship|part_time|research|unknown
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_precision: Mapped[str] = mapped_column(String(10), default="unknown", nullable=False)  # month | year | unknown
    date_text: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # [{"id": "b0", "text": "...", "start": 123, "end": 190}]
    bullets: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    degree: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    field_of_study: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # Where the header line came from in raw_text.
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="extracted", nullable=False)  # extracted | user_added
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extracted: Mapped[dict | None] = mapped_column(JSONB)  # original parser output (immutable)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # user removed it from matching

    version: Mapped[ResumeVersion] = relationship(back_populates="entries")


class ProfileSkill(UUIDPk, Timestamps, Base):
    """A skill attached to a resume version, with provenance.

    source:
      extracted   – found in the resume text; `evidence` lists every location
      user_added  – added by the user and marked verified by them; never presented as resume text
    """

    __tablename__ = "profile_skills"
    __table_args__ = (UniqueConstraint("version_id", "canonical"),)

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    canonical: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="extracted", nullable=False)
    # [{"start": 10, "end": 16, "text": "Python", "context": "skills|employment|project|education|other", "entry_id": "..."}]
    evidence: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    user_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    version: Mapped[ResumeVersion] = relationship(back_populates="skills")


class ProfileEditLog(UUIDPk, Timestamps, Base):
    """Append-only record of manual profile corrections."""

    __tablename__ = "profile_edit_log"

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # entry | skill
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # update | add | remove | restore
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
