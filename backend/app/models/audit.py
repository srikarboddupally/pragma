"""Audit log entry schema — mirrors the append-only ``audit_log`` table.

A row is written on EVERY guardrail outcome (approved / rejected / escalated) and on
execution success/failure. The table is immutable at the DB level (RLS).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuditLogEntry(BaseModel):
    request_id: str
    workspace_id: str
    agent_id: str
    skill_id: str | None = None
    tool_name: str
    params: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)
    outcome: str  # "approved" | "rejected" | "escalated"
    failed_check: str | None = None
    rejection_reason: str | None = None
    risk_tier: str | None = None
    idempotency_key: str | None = None
    executed: bool = False
    executed_at: datetime | None = None
    result: dict | None = None
    error: str | None = None
    trace_id: str | None = None
