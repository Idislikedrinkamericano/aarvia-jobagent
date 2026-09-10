import pytest

from aarvia.requirement_logic import (
    LogicMemberStatus,
    RequirementLogicOperator,
    aggregate_logic_status,
    canonical_logic_members,
)
from aarvia.role_catalog import Phase2ValidationError


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("satisfied", "satisfied"), "satisfied"),
        (("satisfied", "unknown"), "unknown"),
        (("partial", "unknown"), "partial"),
        (("missing", "unknown"), "missing"),
        (("satisfied", "not_applicable"), "satisfied"),
        (("not_applicable", "not_applicable"), "not_applicable"),
    ],
)
def test_all_of_truth_table(statuses, expected) -> None:
    assert aggregate_logic_status(
        RequirementLogicOperator.ALL_OF,
        tuple(LogicMemberStatus(item) for item in statuses),
    ) == LogicMemberStatus(expected)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("satisfied", "missing"), "satisfied"),
        (("partial", "unknown"), "partial"),
        (("missing", "unknown"), "unknown"),
        (("missing", "missing"), "missing"),
        (("satisfied", "not_applicable"), "satisfied"),
        (("not_applicable", "not_applicable"), "not_applicable"),
    ],
)
def test_any_of_truth_table(statuses, expected) -> None:
    assert aggregate_logic_status(
        RequirementLogicOperator.ANY_OF,
        tuple(LogicMemberStatus(item) for item in statuses),
    ) == LogicMemberStatus(expected)


def test_logic_evaluator_and_member_validation_are_strict() -> None:
    with pytest.raises(Phase2ValidationError, match="member_statuses"):
        aggregate_logic_status(RequirementLogicOperator.ALL_OF, ())
    with pytest.raises(Phase2ValidationError, match="operator"):
        aggregate_logic_status("all_of", (LogicMemberStatus.SATISFIED,))
    with pytest.raises(Phase2ValidationError, match="at least two"):
        canonical_logic_members(("candidate_one",))
    with pytest.raises(Phase2ValidationError, match="duplicate"):
        canonical_logic_members(("candidate_one", "candidate_one"))
    assert canonical_logic_members(("candidate_two", "candidate_one")) == (
        "candidate_one",
        "candidate_two",
    )
