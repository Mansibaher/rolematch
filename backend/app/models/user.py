from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Opt-in for the optional external LLM integration. Off unless the user enables it.
    llm_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthSession(UUIDPk, Timestamps, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Only a SHA-256 of the opaque session token is stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class Preferences(Timestamps, Base):
    """Job-search preferences. Each filter can be marked hard (exclude) or soft (rank only)."""

    __tablename__ = "preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_families: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    locations: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    remote_modes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)  # remote|hybrid|onsite
    experience_levels: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)  # intern|entry|mid|senior|staff
    employment_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)  # full_time|part_time|contract|internship
    min_salary: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    excluded_employers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    excluded_keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    # e.g. {"locations": true, "remote_modes": false, ...}. Exclusions and sponsorship are always hard.
    hard_filters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    active_resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL")
    )
    # Soft ranking adjustments learned from "relevant / not relevant" feedback. Bounded; never touches eligibility.
    feedback_adjustments: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkEligibility(Base):
    """Private work-authorization details, kept in their own table.

    Never logged, never sent to any external provider, and only returned to the owner.
    """

    __tablename__ = "work_eligibility"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # None = not provided. True = the user needs employer visa sponsorship now or in the future.
    needs_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    # Free-form note kept for the user only (e.g. "F-1 OPT, STEM extension eligible").
    authorization_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Whether jobs that explicitly require citizenship / clearance should be hard-excluded.
    exclude_citizenship_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
