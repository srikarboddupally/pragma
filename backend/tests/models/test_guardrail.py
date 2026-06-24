import pytest

from app.models.guardrail import (
    GuardrailDecision,
    GuardrailEscalation,
    GuardrailRejection,
)


def test_rejection_carries_check_and_reason() -> None:
    err = GuardrailRejection("skill_match", "no validated skill")
    assert err.check == "skill_match"
    assert "no validated skill" in str(err)


def test_escalation_is_a_distinct_type() -> None:
    err = GuardrailEscalation("permission", "amount exceeds limit")
    assert isinstance(err, GuardrailEscalation)
    assert not isinstance(err, GuardrailRejection)


def test_both_exceptions_are_raisable() -> None:
    with pytest.raises(GuardrailRejection):
        raise GuardrailRejection("idempotency", "already executed")


def test_decision_minimal_defaults() -> None:
    decision = GuardrailDecision(
        request_id="req-1",
        outcome="approved",
        reason="ok",
        risk_tier="low",
        idempotency_key="key-1",
    )
    assert decision.requires_human is False
    assert decision.condition_results == []
    assert decision.failed_check is None
