"""initial schema: core tables + pgvector + HNSW indexes + audit_log RLS

Revision ID: 0001_init
Revises:
Create Date: 2026-06-23

Hand-written initial migration (no DB was available to autogenerate against, and Alembic
autogenerate cannot express the pgvector extension, HNSW indexes, or RLS policies anyway).
Keep it in sync with app/db/tables.py.

NOTE: the embedding dimension below (1024) corresponds to EMBEDDING_PROVIDER=voyage
(voyage-3.5). Changing the embedding provider requires a new migration + re-embed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024  # voyage-3.5


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "source_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("credentials", JSONB(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("config", JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "source", name="uq_source_per_workspace"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("author", sa.String()),
        sa.Column("url", sa.Text()),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("metadata", JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source", "source_id", name="uq_source_source_id"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "doc_id",
            sa.String(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("doc_title", sa.Text()),
        sa.Column("doc_url", sa.Text()),
        sa.Column("author", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("position", sa.Integer()),
        sa.Column("total_chunks", sa.Integer()),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("embedding_model", sa.String(), server_default="voyage-3.5"),
        sa.Column("near_duplicate_of", sa.String(), sa.ForeignKey("chunks.id")),
        sa.Column("trace_id", sa.String()),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_chunks_workspace_doctype_source", "chunks", ["workspace_id", "doc_type", "source"]
    )
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    # HNSW vector index (better recall + incremental inserts than ivfflat — CLAUDE.md §4.3)
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "skills",
        sa.Column("skill_id", sa.String(), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("skill_name", sa.String(), nullable=False),
        sa.Column("trigger_text", sa.Text(), nullable=False),
        sa.Column("trigger_embedding", Vector(EMBEDDING_DIM)),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("steps", JSONB(), nullable=False),
        sa.Column("conditions", JSONB(), nullable=False),
        sa.Column("contradictions", JSONB(), nullable=False),
        sa.Column("sources", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("superseded_by", sa.String(), sa.ForeignKey("skills.skill_id")),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_skills_workspace_confidence", "skills", ["workspace_id", "confidence"])
    op.execute(
        "CREATE INDEX ix_skills_trigger_embedding_hnsw ON skills "
        "USING hnsw (trigger_embedding vector_cosine_ops)"
    )

    op.create_table(
        "agent_permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("max_amount", sa.Numeric()),
        sa.Column("allowed_scopes", JSONB(), server_default="[]"),
        sa.Column("granted_by", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "agent_id", "tool_name", name="uq_agent_tool_grant"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("skill_id", sa.String()),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("context", JSONB(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("failed_check", sa.String()),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("risk_tier", sa.String()),
        sa.Column("idempotency_key", sa.String(), unique=True),
        sa.Column("executed", sa.Boolean(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("trace_id", sa.String()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    # Append-only enforcement at the DB level (CLAUDE.md: audit log is immutable).
    # FORCE makes RLS apply even to the table owner; the application must connect as a
    # non-superuser role for these policies to bite (superusers bypass RLS).
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY deny_update ON audit_log FOR UPDATE USING (false)")
    op.execute("CREATE POLICY deny_delete ON audit_log FOR DELETE USING (false)")
    op.execute("CREATE POLICY allow_read ON audit_log FOR SELECT USING (true)")
    op.execute("CREATE POLICY allow_insert ON audit_log FOR INSERT WITH CHECK (true)")

    op.create_table(
        "workspace_api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workspace_api_keys_key_prefix", "workspace_api_keys", ["key_prefix"])


def downgrade() -> None:
    op.drop_table("workspace_api_keys")
    op.drop_table("audit_log")
    op.drop_table("agent_permissions")
    op.drop_table("skills")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("source_connections")
    op.drop_table("workspaces")
    op.execute("DROP EXTENSION IF EXISTS vector")
