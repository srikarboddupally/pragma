"""SQLAlchemy ORM tables (source of truth for the app's queries and Alembic autogenerate).

Senior changes vs PRAGMA.md §6 (see CLAUDE.md §4):
- ``chunks.embedding`` / ``skills.trigger_embedding`` dimension comes from the active
  embedding provider (settings.embedding_dim). The HNSW indexes and audit_log RLS are added
  in the migration body (Alembic autogenerate can't express them).
- ``chunks.near_duplicate_of`` links near-duplicate chunks instead of inserting copies.
- ``workspace_api_keys`` stores an argon2 hash of the secret, never the plaintext.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db.base import Base

_DIM = get_settings().embedding_dim


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceConnection(Base):
    __tablename__ = "source_connections"
    __table_args__ = (UniqueConstraint("workspace_id", "source", name="uq_source_per_workspace"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Fernet-encrypted payload
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_source_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)  # native id from the source
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, server_default="{}")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_title: Mapped[str | None] = mapped_column(Text)
    doc_url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int | None] = mapped_column(Integer)
    total_chunks: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))
    embedding_model: Mapped[str] = mapped_column(String, server_default="voyage-3.5")
    near_duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"))
    trace_id: Mapped[str | None] = mapped_column(String)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Skill(Base):
    __tablename__ = "skills"

    skill_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    cluster_id: Mapped[str] = mapped_column(String, nullable=False)
    skill_name: Mapped[str] = mapped_column(String, nullable=False)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_embedding: Mapped[list[float] | None] = mapped_column(Vector(_DIM))
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    contradictions: Mapped[list] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    superseded_by: Mapped[str | None] = mapped_column(ForeignKey("skills.skill_id"))
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentPermission(Base):
    __tablename__ = "agent_permissions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_id", "tool_name", name="uq_agent_tool_grant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    max_amount: Mapped[float | None] = mapped_column(Numeric)
    allowed_scopes: Mapped[list] = mapped_column(JSONB, server_default="[]")
    granted_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    skill_id: Mapped[str | None] = mapped_column(String)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    failed_check: Mapped[str | None] = mapped_column(String)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    risk_tier: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str | None] = mapped_column(String, unique=True)
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspaceApiKey(Base):
    """API keys for workspace auth. Only the argon2 hash of the secret is stored."""

    __tablename__ = "workspace_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False, index=True)  # pk_live_{ws}
    secret_hash: Mapped[str] = mapped_column(String, nullable=False)  # argon2 hash
    name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(Base):
    """Transactional outbox (BUILD_PLAN Phase 1 retrofit + Distributed-systems standard).

    A downstream event written in the SAME transaction as the data change that emits it, so a
    crash can't leave the DB updated but the follow-up task un-enqueued (the dual-write problem).
    A poller reads unpublished rows (``published_at IS NULL``) and enqueues the real Celery task,
    then stamps ``published_at``. ``aggregate_id`` is a plain string (a ``workspace_id`` or
    ``doc_id``, depending on ``event_type``) — deliberately not a FK, since it spans tables.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FailedTask(Base):
    """Dead-letter record (BUILD_PLAN Phase 1 retrofit + Distributed-systems standard).

    A Celery task that exhausts its retries lands here instead of being silently dropped, for
    manual replay or alerting. ``args`` holds the task's ``{"args": [...], "kwargs": {...}}``.
    """

    __tablename__ = "failed_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_name: Mapped[str] = mapped_column(String, nullable=False)
    args: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
