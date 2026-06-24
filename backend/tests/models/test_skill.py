from datetime import UTC, datetime

from app.models.skill import Skill, SkillCondition, SkillSource


def test_condition_is_structured_not_a_string() -> None:
    cond = SkillCondition(field="amount", op=">", value=500, then_action="require approval")
    assert cond.field == "amount"
    assert cond.op == ">"
    assert cond.value == 500


def test_skill_defaults() -> None:
    skill = Skill(
        skill_id="skl_abc12345",
        skill_name="Billing refund approval",
        trigger="when a customer requests a refund",
        confidence="high",
        cluster_id="cluster-1",
        last_updated=datetime.now(UTC),
    )
    assert skill.version == 1
    assert skill.steps == []
    assert skill.conditions == []
    assert skill.superseded_by is None


def test_skill_carries_sources_and_conditions() -> None:
    skill = Skill(
        skill_id="skl_abc12345",
        skill_name="Refund",
        trigger="refund",
        confidence="medium",
        cluster_id="c1",
        last_updated=datetime.now(UTC),
        conditions=[SkillCondition(field="amount", op=">", value=500, then_action="approve")],
        sources=[
            SkillSource(doc_id="d1", url="u", author="ceo", date="2026-01-01", source="slack")
        ],
    )
    assert skill.conditions[0].then_action == "approve"
    assert skill.sources[0].source == "slack"
