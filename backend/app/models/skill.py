"""Skill schema — the extractor's output.

Conditions are STRUCTURED data (``field``/``op``/``value``), never strings to be eval'd.
The guardrail layer evaluates them with a deterministic, whitelisted evaluator — there is
nothing to inject (see CLAUDE.md §4.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ConditionOp = Literal["==", "!=", ">", "<", ">=", "<=", "in", "not_in"]
Confidence = Literal["high", "medium", "low"]


class SkillCondition(BaseModel):
    field: str  # key looked up in the guardrail request context, e.g. "amount"
    op: ConditionOp
    value: Any  # e.g. 500, or a list for in/not_in
    then_action: str  # human-readable consequence, e.g. "require manager approval"


class SkillSource(BaseModel):
    doc_id: str
    url: str
    author: str
    date: str
    source: str


class Skill(BaseModel):
    skill_id: str  # "skl_{uuid4().hex[:8]}"
    skill_name: str
    trigger: str  # "when a customer requests a refund"
    confidence: Confidence  # computed mechanically in extractor.py — never by the LLM
    steps: list[str] = Field(default_factory=list)
    conditions: list[SkillCondition] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)  # never hide these
    sources: list[SkillSource] = Field(default_factory=list)
    last_updated: datetime
    superseded_by: str | None = None
    cluster_id: str
    version: int = 1
