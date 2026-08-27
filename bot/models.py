from datetime import datetime, time
from sqlalchemy import (
    String, Integer, BigInteger, Text, DateTime, Boolean, ForeignKey, func,
    Index, CheckConstraint, Time, UniqueConstraint,
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
            "worker_category IS NULL OR worker_category IN ('electrician', 'plumber', 'security', 'cleaning', 'kazakhdomofon')",
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
    resident_subrole: Mapped[str | None] = mapped_column(String(20), nullable=True)  # owner | tenant
    worker_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # NULL means that a new user has not made the initial language choice yet.
    language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by_owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_on_shift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    requests: Mapped[list["Request"]] = relationship("Request", back_populates="resident", foreign_keys="Request.resident_id")
    assigned_requests: Mapped[list["Request"]] = relationship("Request", back_populates="worker", foreign_keys="Request.worker_id")
    approved_by_owner: Mapped["User | None"] = relationship(
        "User", remote_side="User.id", foreign_keys=[approved_by_owner_id]
    )
    working_hours: Mapped[list["WorkerWorkingHour"]] = relationship(
        "WorkerWorkingHour", back_populates="worker", cascade="all, delete-orphan"
    )
    schedule_exceptions: Mapped[list["WorkerScheduleException"]] = relationship(
        "WorkerScheduleException", back_populates="worker", cascade="all, delete-orphan"
    )


class DevPersona(Base):
    """Stable synthetic user controlled by one developer in DEV_MODE."""

    __tablename__ = "dev_personas"
    __table_args__ = (
        UniqueConstraint(
            "controller_telegram_id", "persona_key",
            name="uq_dev_personas_controller_key",
        ),
        UniqueConstraint("user_id", name="uq_dev_personas_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    controller_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    persona_key: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    user: Mapped["User"] = relationship("User")


class DevSession(Base):
    """The persona currently impersonated by a Telegram account."""

    __tablename__ = "dev_sessions"

    controller_telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("dev_personas.id", ondelete="CASCADE"), nullable=False
    )

    persona: Mapped["DevPersona"] = relationship("DevPersona")


class WorkerWorkingHour(Base):
    """One recurring local-time interval in a worker's official schedule."""

    __tablename__ = "worker_working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_working_hours_weekday"),
        UniqueConstraint(
            "worker_id", "weekday", "start_time", "end_time",
            name="uq_worker_working_interval",
        ),
        Index("ix_working_hours_worker_weekday", "worker_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday = 0
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    worker: Mapped["User"] = relationship("User", back_populates="working_hours")


class WorkerScheduleException(Base):
    """A concrete UTC interval overriding a worker's recurring schedule."""

    __tablename__ = "worker_schedule_exceptions"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_schedule_exception_interval"),
        Index("ix_schedule_exception_worker_interval", "worker_id", "starts_at", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    worker: Mapped["User"] = relationship("User", back_populates="schedule_exceptions")


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(
            "category IN ('electrician', 'plumber', 'security', 'cleaning', 'kazakhdomofon')",
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
        CheckConstraint(
            "service_area IS NULL OR service_area IN ('apartment', 'common')",
            name="ck_requests_service_area",
        ),
        CheckConstraint(
            "approval_status IS NULL OR approval_status IN ('pending', 'approved', 'rejected')",
            name="ck_requests_approval_status",
        ),
        Index("ix_requests_status_created", "status", "created_at"),
        Index("ix_requests_category_status_created", "category", "status", "created_at"),
        Index("ix_requests_worker_status", "worker_id", "status"),
        Index("ix_requests_resident_created", "resident_id", "created_at"),
        Index(
            "ix_requests_deferred_dispatch",
            "dispatch_after", "dispatched_at", "status",
        ),
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
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # LLM enrichment (nullable for backward compat)
    urgency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    raw_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # json dump of classify result
    completion_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    completion_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_raw_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_llm_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_area: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL",
            name="fk_requests_reviewed_by_id_users",
        ),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resident: Mapped["User"] = relationship("User", back_populates="requests", foreign_keys=[resident_id])
    worker: Mapped["User | None"] = relationship("User", back_populates="assigned_requests", foreign_keys=[worker_id])
    attachments: Mapped[list["RequestAttachment"]] = relationship(
        "RequestAttachment", back_populates="request", cascade="all, delete-orphan"
    )
    translations: Mapped[list["RequestTranslation"]] = relationship(
        "RequestTranslation", back_populates="request", cascade="all, delete-orphan"
    )


class RequestTranslation(Base):
    """One persistent LLM translation of a request into a target language."""

    __tablename__ = "request_translations"
    __table_args__ = (
        CheckConstraint(
            "target_language IN ('kk', 'ru')",
            name="ck_request_translations_language",
        ),
        UniqueConstraint(
            "request_id", "target_language",
            name="uq_request_translations_request_language",
        ),
        Index("ix_request_translations_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    target_language: Mapped[str] = mapped_column(String(2), nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped["Request"] = relationship(
        "Request", back_populates="translations"
    )


class RequestAttachment(Base):
    """Telegram-hosted media attached to a request."""

    __tablename__ = "request_attachments"
    __table_args__ = (
        CheckConstraint(
            "media_type IN ('photo', 'video', 'document')",
            name="ck_request_attachments_media_type",
        ),
        Index("ix_request_attachments_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped["Request"] = relationship("Request", back_populates="attachments")


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
            "action IN ('created', 'approved', 'rejected', 'claimed', 'assigned', 'reassigned', 'closed', 'deleted')",
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
