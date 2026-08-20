from datetime import datetime
from sqlalchemy import (
    String, Integer, BigInteger, Text, DateTime, Boolean, ForeignKey, func,
    Index, CheckConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('resident', 'worker', 'dispatcher', 'administrator')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "worker_category IS NULL OR worker_category IN ('electrician', 'plumber', 'security')",
            name="ck_users_worker_category",
        ),
        CheckConstraint(
            "language IS NULL OR language IN ('kk', 'ru')",
            name="ck_users_language",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    apartment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="resident", nullable=False)  # resident | worker | dispatcher | administrator
    worker_category: Mapped[str | None] = mapped_column(String(20), nullable=True)  # electrician | plumber | security
    # NULL means that a new user has not made the initial language choice yet.
    language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_on_shift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    requests: Mapped[list["Request"]] = relationship("Request", back_populates="resident", foreign_keys="Request.resident_id")
    assigned_requests: Mapped[list["Request"]] = relationship("Request", back_populates="worker", foreign_keys="Request.worker_id")


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(
            "category IN ('electrician', 'plumber', 'security')",
            name="ck_requests_category",
        ),
        CheckConstraint(
            "status IN ('new', 'accepted', 'closed')",
            name="ck_requests_status",
        ),
        CheckConstraint(
            "urgency IS NULL OR urgency IN ('low', 'normal', 'high')",
            name="ck_requests_urgency",
        ),
        CheckConstraint(
            "completion_result IS NULL OR completion_result IN ('done', 'not_done')",
            name="ck_requests_completion_result",
        ),
        Index("ix_requests_status_created", "status", "created_at"),
        Index("ix_requests_category_status_created", "category", "status", "created_at"),
        Index("ix_requests_worker_status", "worker_id", "status"),
        Index("ix_requests_resident_created", "resident_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # electrician | plumber | security
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)  # new | accepted | closed
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # LLM enrichment (nullable for backward compat)
    urgency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    raw_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # json dump of classify result
    completion_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    completion_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_raw_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_llm_meta: Mapped[str | None] = mapped_column(Text, nullable=True)

    resident: Mapped["User"] = relationship("User", back_populates="requests", foreign_keys=[resident_id])
    worker: Mapped["User | None"] = relationship("User", back_populates="assigned_requests", foreign_keys=[worker_id])


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    author: Mapped["User"] = relationship("User")


class RequestEvent(Base):
    """Append-only request history retained even if a request is deleted."""

    __tablename__ = "request_events"
    __table_args__ = (
        Index("ix_request_events_request_created", "request_id", "created_at"),
        CheckConstraint(
            "action IN ('created', 'claimed', 'assigned', 'reassigned', 'closed', 'deleted')",
            name="ck_request_events_action",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Deliberately no FK: deletion must not destroy the audit history.
    request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped["User | None"] = relationship("User")
