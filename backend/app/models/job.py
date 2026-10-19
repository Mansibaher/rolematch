from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk

EMBEDDING_DIM = 384


class JobSource(UUIDPk, Timestamps, Base):
    """A place jobs come from.

    kind:
      greenhouse – public Greenhouse Job Board API for one board token (one employer)
      lever      – public Lever Postings API for one site name (one employer)
      manual     – jobs pasted by users (private to the user who added them)
      fixture    – bundled synthetic jobs for the offline demo (never presented as real)
    """

    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("kind", "identifier"),)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    identifier: Mapped[str] = mapped_column(String(120), nullable=False)  # board token / site name / "manual" / "fixtures"
    employer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[str] = mapped_column(String(10), default="us", nullable=False)  # lever: us | eu
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    from_config: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserWatch(Timestamps, Base):
    """Which sources a user follows. Feeds only include followed sources plus the user's own manual jobs."""

    __tablename__ = "user_watches"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), primary_key=True)


class SourceRun(UUIDPk, Timestamps, Base):
    """One ingestion attempt. Drives source-health reporting and guards against outage-induced closures."""

    __tablename__ = "source_runs"

    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), index=True)
    # Celery task id (or a generated id). Unique so a re-delivered task cannot record a second run.
    run_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # running | success | partial | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    complete_listing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_closed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(60))
    error_detail: Mapped[str | None] = mapped_column(Text)


class Job(UUIDPk, Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source_id", "source_job_id"),
        Index("ix_jobs_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("ix_jobs_canonical_url", "canonical_url"),
        Index("ix_jobs_status", "status"),
        Index(
            "ix_jobs_embedding_hnsw", "embedding", postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), index=True)
    source_job_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # Set for manual jobs: only this user can see them.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_fixture: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    employer: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description_html: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(String(200))
    requisition_id: Mapped[str | None] = mapped_column(String(120))

    location_raw: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    locations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    remote_type: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)  # remote|hybrid|onsite|unknown
    remote_restriction: Mapped[str | None] = mapped_column(String(300))
    employment_type: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    employment_type_basis: Mapped[str] = mapped_column(String(60), default="unknown", nullable=False)

    # Salary only when the source supplies it; basis records where it came from.
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_interval: Mapped[str | None] = mapped_column(String(20))
    salary_basis: Mapped[str | None] = mapped_column(String(200))

    # posted_at is the source's own posting date, only when the source provides one.
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at_basis: Mapped[str | None] = mapped_column(String(120))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    apply_url: Mapped[str | None] = mapped_column(String(1000))
    canonical_url: Mapped[str | None] = mapped_column(String(1000))

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active | closed | unknown
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # When conservative dedup decides two rows are the same requisition, the newer row points at the canonical one.
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    duplicate_reason: Mapped[str | None] = mapped_column(String(200))

    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    extraction: Mapped[dict | None] = mapped_column(JSONB)
    extraction_version: Mapped[str | None] = mapped_column(String(20))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(description_text, '')), 'B')",
            persisted=True,
        ),
    )

    source: Mapped[JobSource] = relationship()


class MatchResult(UUIDPk, Timestamps, Base):
    """Cached match computation for (user, job, profile version, scoring config)."""

    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("user_id", "job_id", "cache_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qualification_score: Mapped[float] = mapped_column(Float, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)


class JobFeedback(Timestamps, Base):
    __tablename__ = "job_feedback"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    label: Mapped[str] = mapped_column(String(20), nullable=False)  # relevant | not_relevant
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
