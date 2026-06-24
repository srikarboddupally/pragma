"""Guardrail request/decision schemas and typed exceptions.

Internal guardrail functions raise ``GuardrailRejection`` / ``GuardrailEscalation`` — they
never return error dicts. The pipeline catches these and maps them to a ``GuardrailDecision``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.skill import SkillCondition

Outcome = Literal["approved", "rejected", "escalated"]


class GuardrailRequest(BaseModel):
    request_id: str
    agent_id: str
    workspace_id: str
    skill_id: str | None = None
    tool_name: str
    proposed_params: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)  # actual values to evaluate conditions against


class ConditionResult(BaseModel):
    condition: SkillCondition
    result: bool
    then_action: str | None = None  # set when result is True


class GuardrailDecision(BaseModel):
    request_id: str
    outcome: Outcome
    failed_check: str | None = None
    reason: str
    risk_tier: str
    requires_human: bool = False
    condition_results: list[ConditionResult] = Field(default_factory=list)
    idempotency_key: str


class GuardrailRejection(Exception):
    """Raised when a check fails hard — the action must not run."""

    def __init__(self, check: str, reason: str) -> None:
        self.check = check
        self.reason = reason
        super().__init__(f"[{check}] {reason}")


class GuardrailEscalation(Exception):
    """Raised when a check requires human approval before the action can run."""

    def __init__(self, check: str, reason: str) -> None:
        self.check = check
        self.reason = reason
        super().__init__(f"[{check}] {reason}")
