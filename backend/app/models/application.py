from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk

APPLICATION_STATUSES = (
    "saved",
    "applied",
    "oa_received",
    "oa_completed",
    "recruiter_screen",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "closed_without_application",
)

DEADLINE_KINDS = ("oa", "interview", "follow_up", "other")


class Application(UUIDPk, Timestamps, Base):
    """The user's own pipeline record for a job.

    Status here is the user's application status and is independent of whether the source still lists the job.
    """

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL"))
    # Frozen copy of the posting at the moment the user marked it applied.
    job_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    employer: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    external_url: Mapped[str | None] = mapped_column(String(1000))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="ApplicationEvent.occurred_at"
    )
    deadlines: Mapped[list[Deadline]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Deadline.due_at"
    )


class ApplicationEvent(UUIDPk, Timestamps, Base):
    """Append-only stage history. Status changes add rows; nothing is overwritten."""

    __tablename__ = "application_events"

    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    application: Mapped[Application] = relationship(back_populates="events")


class Deadline(UUIDPk, Timestamps, Base):
    """OA / interview / follow-up deadline. due_at is an absolute instant; tz_name records how the user entered it."""

    __tablename__ = "deadlines"

    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tz_name: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    application: Mapped[Application] = relationship(back_populates="deadlines")
