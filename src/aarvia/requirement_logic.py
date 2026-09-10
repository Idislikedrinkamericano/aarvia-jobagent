"""Deterministic, provider-independent requirement logic semantics."""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from .role_catalog import Phase2ValidationError


class RequirementLogicOperator(str, Enum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"


class LogicMemberStatus(str, Enum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class RequirementModality(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    RESPONSIBILITY_SIGNAL = "responsibility_signal"
    PRESENT_AMBIGUOUS = "present_ambiguous"


def canonical_logic_members(member_references: Iterable[str]) -> tuple[str, ...]:
    members = tuple(member_references)
    if len(members) < 2:
        raise Phase2ValidationError("requirement logic group requires at least two members")
    if any(not isinstance(item, str) or not item.strip() for item in members):
        raise Phase2ValidationError("requirement logic member references must be non-empty strings")
    normalized = tuple(sorted(members))
    if len(normalized) != len(set(normalized)):
        raise Phase2ValidationError("requirement logic group contains duplicate members")
    return normalized


def aggregate_logic_status(
    operator: RequirementLogicOperator,
    member_statuses: Iterable[LogicMemberStatus],
) -> LogicMemberStatus:
    if not isinstance(operator, RequirementLogicOperator):
        raise Phase2ValidationError("operator must be a RequirementLogicOperator")
    statuses = tuple(member_statuses)
    if not statuses or any(not isinstance(item, LogicMemberStatus) for item in statuses):
        raise Phase2ValidationError("member_statuses must contain LogicMemberStatus values")
    applicable = tuple(
        item for item in statuses if item != LogicMemberStatus.NOT_APPLICABLE
    )
    if not applicable:
        return LogicMemberStatus.NOT_APPLICABLE
    present = set(applicable)
    if operator == RequirementLogicOperator.ALL_OF:
        if present == {LogicMemberStatus.SATISFIED}:
            return LogicMemberStatus.SATISFIED
        if LogicMemberStatus.MISSING in present:
            return LogicMemberStatus.MISSING
        if LogicMemberStatus.PARTIAL in present:
            return LogicMemberStatus.PARTIAL
        return LogicMemberStatus.UNKNOWN
    if LogicMemberStatus.SATISFIED in present:
        return LogicMemberStatus.SATISFIED
    if LogicMemberStatus.PARTIAL in present:
        return LogicMemberStatus.PARTIAL
    if LogicMemberStatus.UNKNOWN in present:
        return LogicMemberStatus.UNKNOWN
    return LogicMemberStatus.MISSING
