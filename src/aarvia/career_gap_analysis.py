"""Deterministic Dimension-level Career Gap Analysis contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .capability_rubric import (
    CapabilityDimension,
    CapabilityRubric,
    DimensionReadiness,
    capability_rubric_fingerprint,
)
from .career_direction import (
    DecisionStatus,
    UserRoleDecision,
    profile_fingerprint,
)
from .evidence_binding_review import (
    BindingReviewDecision,
    EvidenceBindingReviewArtifact,
)
from .evidence_group_allocation_review import EvidenceGroupAllocationReviewArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import (
    CurrentMatchStatus,
    EvidenceTrustLevel,
    ProfileDimensionMappingCandidateSet,
)
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
    _version,
)
from .role_recommendation import (
    DimensionAssessment,
    RoleRecommendationArtifact,
)


CAREER_GAP_ANALYSIS_SCHEMA = "aarvia.career_gap_analysis"
CAREER_GAP_ANALYSIS_SCHEMA_VERSION = 2
CAREER_GAP_ANALYSIS_ID_VERSION = "career-gap-analysis-v2"


class RoleSelectionType(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class GapClassification(str, Enum):
    CONFIRMED_STRENGTH = "confirmed_strength"
    DEVELOPING_CAPABILITY = "developing_capability"
    TRANSFERABLE_FOUNDATION = "transferable_foundation"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    CAPABILITY_GAP = "capability_gap"
    NOT_APPLICABLE = "not_applicable"


class GapEvidenceState(str, Enum):
    CONFIRMED_SEMANTIC = "confirmed_semantic"
    PROVISIONAL_SEMANTIC = "provisional_semantic"
    STRUCTURAL_ONLY = "structural_only"
    MIXED = "mixed"
    NONE = "none"


class GapReviewState(str, Enum):
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    MIXED = "mixed"
    NOT_REQUIRED = "not_required"
    ABSENT = "absent"


class GapPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class GapPriorityReason(str, Enum):
    PRIMARY_ROLE = "primary_role"
    SECONDARY_ROLE = "secondary_role"
    READY_DIMENSION = "ready_dimension"
    CONDITIONAL_DIMENSION = "conditional_dimension"
    CONFIRMED_STRENGTH = "confirmed_strength"
    DEVELOPING_CAPABILITY = "developing_capability"
    TRANSFERABLE_FOUNDATION = "transferable_foundation"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    EXPLICIT_NOT_DEMONSTRATED = "explicit_not_demonstrated"
    NOT_APPLICABLE = "not_applicable"


class GapNextActionType(str, Enum):
    SHOWCASE_CONFIRMED_STRENGTH = "showcase_confirmed_strength"
    STRENGTHEN_CAPABILITY_EVIDENCE = "strengthen_capability_evidence"
    VALIDATE_TRANSFERABLE_FOUNDATION = "validate_transferable_foundation"
    CONFIRM_OR_ADD_EVIDENCE = "confirm_or_add_evidence"
    DEVELOP_CAPABILITY = "develop_capability"
    NONE = "none"


_CLASSIFICATION_BY_STATUS = {
    CurrentMatchStatus.DEMONSTRATED: GapClassification.CONFIRMED_STRENGTH,
    CurrentMatchStatus.PARTIAL: GapClassification.DEVELOPING_CAPABILITY,
    CurrentMatchStatus.ADJACENT: GapClassification.TRANSFERABLE_FOUNDATION,
    CurrentMatchStatus.UNKNOWN: GapClassification.UNKNOWN_EVIDENCE,
    CurrentMatchStatus.NOT_DEMONSTRATED: GapClassification.CAPABILITY_GAP,
    CurrentMatchStatus.NOT_APPLICABLE: GapClassification.NOT_APPLICABLE,
}

_ACTION_BY_CLASSIFICATION = {
    GapClassification.CONFIRMED_STRENGTH: GapNextActionType.SHOWCASE_CONFIRMED_STRENGTH,
    GapClassification.DEVELOPING_CAPABILITY: GapNextActionType.STRENGTHEN_CAPABILITY_EVIDENCE,
    GapClassification.TRANSFERABLE_FOUNDATION: GapNextActionType.VALIDATE_TRANSFERABLE_FOUNDATION,
    GapClassification.UNKNOWN_EVIDENCE: GapNextActionType.CONFIRM_OR_ADD_EVIDENCE,
    GapClassification.CAPABILITY_GAP: GapNextActionType.DEVELOP_CAPABILITY,
    GapClassification.NOT_APPLICABLE: GapNextActionType.NONE,
}

_PRIORITY_ORDER = {
    GapPriority.HIGH: 0,
    GapPriority.MEDIUM: 1,
    GapPriority.LOW: 2,
    GapPriority.NONE: 3,
}


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return value


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _fingerprint(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not result.startswith("sha256:") or len(result) != 71:
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint")
    try:
        int(result[7:], 16)
    except ValueError as error:
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint") from error
    return result


@dataclass(frozen=True, eq=True)
class CareerGapDimensionItem:
    dimension_id: str
    readiness: DimensionReadiness
    recommendation_status: CurrentMatchStatus
    gap_classification: GapClassification
    confirmed_criterion_ids: tuple[str, ...]
    unresolved_criterion_ids: tuple[str, ...]
    supporting_binding_ids: tuple[str, ...]
    evidence_state: GapEvidenceState
    review_state: GapReviewState
    priority: GapPriority
    priority_reason_codes: tuple[GapPriorityReason, ...]
    next_action_type: GapNextActionType
    next_action: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> CareerGapDimensionItem:
        data = _mapping(value, path)
        allowed = {
            "dimension_id", "readiness", "recommendation_status", "gap_classification",
            "confirmed_criterion_ids", "unresolved_criterion_ids", "supporting_binding_ids",
            "evidence_state", "review_state", "priority", "priority_reason_codes",
            "next_action_type", "next_action",
        }
        _reject_unknown(data, allowed, path)
        reasons = tuple(
            _enum(item, GapPriorityReason, f"{path}.priority_reason_codes[{index}]")
            for index, item in enumerate(
                _string_tuple(data.get("priority_reason_codes"), f"{path}.priority_reason_codes")
            )
        )
        result = cls(
            dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
            readiness=_enum(data.get("readiness"), DimensionReadiness, f"{path}.readiness"),
            recommendation_status=_enum(
                data.get("recommendation_status"), CurrentMatchStatus,
                f"{path}.recommendation_status",
            ),
            gap_classification=_enum(
                data.get("gap_classification"), GapClassification,
                f"{path}.gap_classification",
            ),
            confirmed_criterion_ids=_string_tuple(
                data.get("confirmed_criterion_ids"), f"{path}.confirmed_criterion_ids", ids=True
            ),
            unresolved_criterion_ids=_string_tuple(
                data.get("unresolved_criterion_ids"), f"{path}.unresolved_criterion_ids", ids=True
            ),
            supporting_binding_ids=_string_tuple(
                data.get("supporting_binding_ids"), f"{path}.supporting_binding_ids", ids=True
            ),
            evidence_state=_enum(
                data.get("evidence_state"), GapEvidenceState, f"{path}.evidence_state"
            ),
            review_state=_enum(
                data.get("review_state"), GapReviewState, f"{path}.review_state"
            ),
            priority=_enum(data.get("priority"), GapPriority, f"{path}.priority"),
            priority_reason_codes=reasons,
            next_action_type=_enum(
                data.get("next_action_type"), GapNextActionType,
                f"{path}.next_action_type",
            ),
            next_action=_text(data.get("next_action"), f"{path}.next_action"),
        )
        result._validate_shape(path)
        return result

    def _validate_shape(self, path: str) -> None:
        for name, values in (
            ("confirmed_criterion_ids", self.confirmed_criterion_ids),
            ("unresolved_criterion_ids", self.unresolved_criterion_ids),
            ("supporting_binding_ids", self.supporting_binding_ids),
        ):
            if tuple(sorted(set(values))) != values:
                raise Phase2ValidationError(f"{path}.{name} must be unique and sorted")
        if set(self.confirmed_criterion_ids) & set(self.unresolved_criterion_ids):
            raise Phase2ValidationError(f"{path} criterion states must be disjoint")
        if not self.priority_reason_codes or len(set(self.priority_reason_codes)) != len(
            self.priority_reason_codes
        ):
            raise Phase2ValidationError(f"{path}.priority_reason_codes are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "readiness": self.readiness.value,
            "recommendation_status": self.recommendation_status.value,
            "gap_classification": self.gap_classification.value,
            "confirmed_criterion_ids": list(self.confirmed_criterion_ids),
            "unresolved_criterion_ids": list(self.unresolved_criterion_ids),
            "supporting_binding_ids": list(self.supporting_binding_ids),
            "evidence_state": self.evidence_state.value,
            "review_state": self.review_state.value,
            "priority": self.priority.value,
            "priority_reason_codes": [item.value for item in self.priority_reason_codes],
            "next_action_type": self.next_action_type.value,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, eq=True)
class CareerGapRoleAnalysis:
    role_id: str
    selection_type: RoleSelectionType
    dimensions: tuple[CareerGapDimensionItem, ...]
    confirmed_strength_count: int
    development_area_count: int
    transferable_foundation_count: int
    unknown_evidence_count: int
    capability_gap_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> CareerGapRoleAnalysis:
        data = _mapping(value, path)
        allowed = {
            "role_id", "selection_type", "dimensions", "confirmed_strength_count",
            "development_area_count", "transferable_foundation_count",
            "unknown_evidence_count", "capability_gap_count",
        }
        _reject_unknown(data, allowed, path)
        result = cls(
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            selection_type=_enum(
                data.get("selection_type"), RoleSelectionType, f"{path}.selection_type"
            ),
            dimensions=tuple(
                CareerGapDimensionItem.from_dict(item, f"{path}.dimensions[{index}]")
                for index, item in enumerate(_list(data.get("dimensions"), f"{path}.dimensions"))
            ),
            confirmed_strength_count=_count(
                data.get("confirmed_strength_count"), f"{path}.confirmed_strength_count"
            ),
            development_area_count=_count(
                data.get("development_area_count"), f"{path}.development_area_count"
            ),
            transferable_foundation_count=_count(
                data.get("transferable_foundation_count"),
                f"{path}.transferable_foundation_count",
            ),
            unknown_evidence_count=_count(
                data.get("unknown_evidence_count"), f"{path}.unknown_evidence_count"
            ),
            capability_gap_count=_count(
                data.get("capability_gap_count"), f"{path}.capability_gap_count"
            ),
        )
        result._validate_counts(path)
        return result

    def _validate_counts(self, path: str) -> None:
        if tuple(item.dimension_id for item in self.dimensions) != tuple(
            sorted(item.dimension_id for item in self.dimensions)
        ) or len({item.dimension_id for item in self.dimensions}) != len(self.dimensions):
            raise Phase2ValidationError(f"{path}.dimensions must be unique and sorted")
        expected = {
            "confirmed_strength_count": GapClassification.CONFIRMED_STRENGTH,
            "development_area_count": GapClassification.DEVELOPING_CAPABILITY,
            "transferable_foundation_count": GapClassification.TRANSFERABLE_FOUNDATION,
            "unknown_evidence_count": GapClassification.UNKNOWN_EVIDENCE,
            "capability_gap_count": GapClassification.CAPABILITY_GAP,
        }
        for field_name, classification in expected.items():
            actual = sum(item.gap_classification == classification for item in self.dimensions)
            if getattr(self, field_name) != actual:
                raise Phase2ValidationError(f"{path}.{field_name} does not match dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "selection_type": self.selection_type.value,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "confirmed_strength_count": self.confirmed_strength_count,
            "development_area_count": self.development_area_count,
            "transferable_foundation_count": self.transferable_foundation_count,
            "unknown_evidence_count": self.unknown_evidence_count,
            "capability_gap_count": self.capability_gap_count,
        }


@dataclass(frozen=True, eq=True)
class CareerGapSummary:
    confirmed_strength_count: int
    development_area_count: int
    transferable_foundation_count: int
    unknown_evidence_count: int
    capability_gap_count: int
    high_priority_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> CareerGapSummary:
        data = _mapping(value, path)
        allowed = {
            "confirmed_strength_count", "development_area_count",
            "transferable_foundation_count", "unknown_evidence_count",
            "capability_gap_count", "high_priority_count",
        }
        _reject_unknown(data, allowed, path)
        return cls(**{name: _count(data.get(name), f"{path}.{name}") for name in allowed})

    def to_dict(self) -> dict[str, int]:
        return {
            "confirmed_strength_count": self.confirmed_strength_count,
            "development_area_count": self.development_area_count,
            "transferable_foundation_count": self.transferable_foundation_count,
            "unknown_evidence_count": self.unknown_evidence_count,
            "capability_gap_count": self.capability_gap_count,
            "high_priority_count": self.high_priority_count,
        }


@dataclass(frozen=True, eq=True)
class CareerGapFollowUp:
    follow_up_id: str
    role_id: str
    dimension_id: str
    action_type: GapNextActionType
    prompt: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> CareerGapFollowUp:
        data = _mapping(value, path)
        allowed = {"follow_up_id", "role_id", "dimension_id", "action_type", "prompt"}
        _reject_unknown(data, allowed, path)
        result = cls(
            follow_up_id=_stable_id(data.get("follow_up_id"), f"{path}.follow_up_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
            action_type=_enum(
                data.get("action_type"), GapNextActionType, f"{path}.action_type"
            ),
            prompt=_text(data.get("prompt"), f"{path}.prompt"),
        )
        expected = _follow_up_id(result.role_id, result.dimension_id, result.action_type)
        if result.follow_up_id != expected:
            raise Phase2ValidationError(f"{path}.follow_up_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "follow_up_id": self.follow_up_id,
            "role_id": self.role_id,
            "dimension_id": self.dimension_id,
            "action_type": self.action_type.value,
            "prompt": self.prompt,
        }


@dataclass(frozen=True, eq=True)
class CareerGapAnalysis:
    analysis_id: str
    decision_id: str
    recommendation_set_id: str
    recommendation_schema_version: int
    profile_fingerprint: str
    rubric_version: str
    rubric_fingerprint: str
    catalog_version: str
    created_at: str
    primary_role_analysis: CareerGapRoleAnalysis
    secondary_role_analyses: tuple[CareerGapRoleAnalysis, ...]
    summary: CareerGapSummary
    follow_ups: tuple[CareerGapFollowUp, ...]
    schema: str = CAREER_GAP_ANALYSIS_SCHEMA
    schema_version: int = CAREER_GAP_ANALYSIS_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CareerGapAnalysis:
        data = _mapping(value, "career_gap_analysis")
        allowed = {
            "schema", "schema_version", "analysis_id", "decision_id",
            "recommendation_set_id", "recommendation_schema_version",
            "profile_fingerprint", "rubric_version", "rubric_fingerprint",
            "catalog_version", "created_at", "primary_role_analysis",
            "secondary_role_analyses", "summary", "follow_ups",
        }
        _reject_unknown(data, allowed, "career_gap_analysis")
        if (
            data.get("schema") != CAREER_GAP_ANALYSIS_SCHEMA
            or data.get("schema_version") != CAREER_GAP_ANALYSIS_SCHEMA_VERSION
        ):
            raise Phase2ValidationError("unsupported Career Gap Analysis schema")
        recommendation_schema_version = data.get("recommendation_schema_version")
        if recommendation_schema_version != 7:
            raise Phase2ValidationError(
                "Career Gap Analysis schema 2 requires Recommendation schema 7"
            )
        result = cls(
            analysis_id=_stable_id(data.get("analysis_id"), "career_gap_analysis.analysis_id"),
            decision_id=_stable_id(data.get("decision_id"), "career_gap_analysis.decision_id"),
            recommendation_set_id=_stable_id(
                data.get("recommendation_set_id"),
                "career_gap_analysis.recommendation_set_id",
            ),
            recommendation_schema_version=recommendation_schema_version,
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), "career_gap_analysis.profile_fingerprint"
            ),
            rubric_version=_version(
                data.get("rubric_version"), "career_gap_analysis.rubric_version"
            ),
            rubric_fingerprint=_fingerprint(
                data.get("rubric_fingerprint"), "career_gap_analysis.rubric_fingerprint"
            ),
            catalog_version=_version(
                data.get("catalog_version"), "career_gap_analysis.catalog_version"
            ),
            created_at=_iso_datetime(data.get("created_at"), "career_gap_analysis.created_at"),
            primary_role_analysis=CareerGapRoleAnalysis.from_dict(
                data.get("primary_role_analysis"),
                "career_gap_analysis.primary_role_analysis",
            ),
            secondary_role_analyses=tuple(
                CareerGapRoleAnalysis.from_dict(
                    item, f"career_gap_analysis.secondary_role_analyses[{index}]"
                )
                for index, item in enumerate(
                    _list(
                        data.get("secondary_role_analyses"),
                        "career_gap_analysis.secondary_role_analyses",
                    )
                )
            ),
            summary=CareerGapSummary.from_dict(
                data.get("summary"), "career_gap_analysis.summary"
            ),
            follow_ups=tuple(
                CareerGapFollowUp.from_dict(item, f"career_gap_analysis.follow_ups[{index}]")
                for index, item in enumerate(
                    _list(data.get("follow_ups"), "career_gap_analysis.follow_ups")
                )
            ),
        )
        result._validate_shape()
        if result.analysis_id != generate_career_gap_analysis_id(result):
            raise Phase2ValidationError("Career Gap Analysis ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if self.primary_role_analysis.selection_type != RoleSelectionType.PRIMARY:
            raise Phase2ValidationError("primary_role_analysis must use primary selection type")
        if any(
            item.selection_type != RoleSelectionType.SECONDARY
            for item in self.secondary_role_analyses
        ):
            raise Phase2ValidationError("secondary_role_analyses must use secondary selection type")
        secondary_ids = tuple(item.role_id for item in self.secondary_role_analyses)
        if secondary_ids != tuple(sorted(set(secondary_ids))):
            raise Phase2ValidationError("secondary role analyses must be unique and sorted")
        if self.primary_role_analysis.role_id in secondary_ids:
            raise Phase2ValidationError("Primary Role cannot also be Secondary")
        follow_up_ids = tuple(item.follow_up_id for item in self.follow_ups)
        if len(follow_up_ids) != len(set(follow_up_ids)):
            raise Phase2ValidationError("Career Gap follow-ups contain duplicates")

    def validate(
        self,
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
        catalog: RoleCatalog,
        mapping: ProfileDimensionMappingCandidateSet,
        recommendation: RoleRecommendationArtifact,
        decision: UserRoleDecision,
        evidence_reviews: EvidenceBindingReviewArtifact | None = None,
        allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
        superseded_decision: UserRoleDecision | None = None,
    ) -> None:
        _validate_context(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping=mapping,
            recommendation=recommendation,
            decision=decision,
            evidence_reviews=evidence_reviews,
            allocation_reviews=allocation_reviews,
            superseded_decision=superseded_decision,
        )
        expected = _build_career_gap_analysis(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            recommendation=recommendation,
            decision=decision,
            evidence_reviews=evidence_reviews,
            created_at=self.created_at,
        )
        if self != expected:
            raise Phase2ValidationError(
                "Career Gap Analysis does not match deterministic source artifacts"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "analysis_id": self.analysis_id,
            "decision_id": self.decision_id,
            "recommendation_set_id": self.recommendation_set_id,
            "recommendation_schema_version": self.recommendation_schema_version,
            "profile_fingerprint": self.profile_fingerprint,
            "rubric_version": self.rubric_version,
            "rubric_fingerprint": self.rubric_fingerprint,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "primary_role_analysis": self.primary_role_analysis.to_dict(),
            "secondary_role_analyses": [item.to_dict() for item in self.secondary_role_analyses],
            "summary": self.summary.to_dict(),
            "follow_ups": [item.to_dict() for item in self.follow_ups],
        }


def _follow_up_id(
    role_id: str, dimension_id: str, action_type: GapNextActionType
) -> str:
    payload = f"career-gap-follow-up-v1|{role_id}|{dimension_id}|{action_type.value}"
    return f"gap_follow_up_{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def _classification_reason(classification: GapClassification) -> GapPriorityReason:
    return {
        GapClassification.CONFIRMED_STRENGTH: GapPriorityReason.CONFIRMED_STRENGTH,
        GapClassification.DEVELOPING_CAPABILITY: GapPriorityReason.DEVELOPING_CAPABILITY,
        GapClassification.TRANSFERABLE_FOUNDATION: GapPriorityReason.TRANSFERABLE_FOUNDATION,
        GapClassification.UNKNOWN_EVIDENCE: GapPriorityReason.UNKNOWN_EVIDENCE,
        GapClassification.CAPABILITY_GAP: GapPriorityReason.EXPLICIT_NOT_DEMONSTRATED,
        GapClassification.NOT_APPLICABLE: GapPriorityReason.NOT_APPLICABLE,
    }[classification]


def _priority(
    selection: RoleSelectionType,
    readiness: DimensionReadiness,
    classification: GapClassification,
) -> GapPriority:
    if classification in {
        GapClassification.CONFIRMED_STRENGTH,
        GapClassification.NOT_APPLICABLE,
    }:
        return GapPriority.NONE
    if (
        selection == RoleSelectionType.PRIMARY
        and classification == GapClassification.CAPABILITY_GAP
    ) or (
        readiness == DimensionReadiness.READY
        and classification == GapClassification.CAPABILITY_GAP
    ) or (
        selection == RoleSelectionType.PRIMARY
        and readiness == DimensionReadiness.READY
        and classification == GapClassification.UNKNOWN_EVIDENCE
    ):
        return GapPriority.HIGH
    if selection == RoleSelectionType.PRIMARY or readiness == DimensionReadiness.READY:
        return GapPriority.MEDIUM
    return GapPriority.LOW


def _action_text(
    dimension: CapabilityDimension, classification: GapClassification
) -> str:
    name = dimension.name
    return {
        GapClassification.CONFIRMED_STRENGTH: (
            f"Showcase the confirmed evidence for {name} in relevant applications."
        ),
        GapClassification.DEVELOPING_CAPABILITY: (
            f"Add or confirm independent evidence for {name}; uncovered criteria remain unconfirmed."
        ),
        GapClassification.TRANSFERABLE_FOUNDATION: (
            f"Validate where the transferable foundation for {name} has been applied in practice."
        ),
        GapClassification.UNKNOWN_EVIDENCE: (
            f"Provide or confirm evidence for {name}; absence of evidence is not treated as a gap."
        ),
        GapClassification.CAPABILITY_GAP: (
            f"Develop and document evidence for {name} against the Rubric criteria."
        ),
        GapClassification.NOT_APPLICABLE: f"No action is required for {name} in this role.",
    }[classification]


def _follow_up_prompt(
    dimension: CapabilityDimension, classification: GapClassification
) -> str:
    if classification == GapClassification.UNKNOWN_EVIDENCE:
        return f"What evidence, if any, confirms your experience with {dimension.name}?"
    if classification == GapClassification.CAPABILITY_GAP:
        return f"What learning or project evidence could you build for {dimension.name}?"
    if classification == GapClassification.TRANSFERABLE_FOUNDATION:
        return f"Where have you applied your transferable foundation for {dimension.name}?"
    return f"What additional confirmed evidence could demonstrate {dimension.name} more fully?"


def _evidence_and_review_state(
    assessment: DimensionAssessment,
    decisions: Mapping[str, BindingReviewDecision],
) -> tuple[GapEvidenceState, GapReviewState, tuple[str, ...]]:
    bindings = assessment.supporting_bindings
    confirmed = tuple(
        item for item in bindings
        if decisions.get(item.binding_id) == BindingReviewDecision.CONFIRMED
        and item.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
    )
    provisional = tuple(
        item for item in bindings
        if item.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
        and decisions.get(item.binding_id) != BindingReviewDecision.CONFIRMED
    )
    structural = tuple(
        item for item in bindings if item.trust_level == EvidenceTrustLevel.STRUCTURAL_ONLY
    )
    present_kinds = sum(bool(values) for values in (confirmed, provisional, structural))
    if not bindings:
        evidence_state = GapEvidenceState.NONE
    elif present_kinds > 1:
        evidence_state = GapEvidenceState.MIXED
    elif confirmed:
        evidence_state = GapEvidenceState.CONFIRMED_SEMANTIC
    elif provisional:
        evidence_state = GapEvidenceState.PROVISIONAL_SEMANTIC
    else:
        evidence_state = GapEvidenceState.STRUCTURAL_ONLY
    if not bindings:
        review_state = GapReviewState.ABSENT
    elif confirmed and assessment.review_required:
        review_state = GapReviewState.MIXED
    elif confirmed:
        review_state = GapReviewState.CONFIRMED
    elif assessment.review_required:
        review_state = GapReviewState.REVIEW_REQUIRED
    else:
        review_state = GapReviewState.NOT_REQUIRED
    return (
        evidence_state,
        review_state,
        tuple(sorted({item.criterion_id for item in confirmed})),
    )


def _dimension_item(
    *,
    dimension: CapabilityDimension,
    assessment: DimensionAssessment,
    selection: RoleSelectionType,
    decisions: Mapping[str, BindingReviewDecision],
) -> CareerGapDimensionItem:
    classification = _CLASSIFICATION_BY_STATUS[assessment.status]
    if not assessment.supporting_bindings and assessment.status not in {
        CurrentMatchStatus.UNKNOWN,
        CurrentMatchStatus.NOT_APPLICABLE,
    }:
        raise Phase2ValidationError(
            "a Current Fit status without evidence must remain unknown"
        )
    evidence_state, review_state, confirmed_criteria = _evidence_and_review_state(
        assessment, decisions
    )
    if (
        assessment.status == CurrentMatchStatus.DEMONSTRATED
        and not confirmed_criteria
    ):
        raise Phase2ValidationError(
            "demonstrated Current Fit requires confirmed policy evidence"
        )
    unresolved = tuple(sorted(set(dimension.criterion_ids) - set(confirmed_criteria)))
    priority = _priority(selection, dimension.readiness, classification)
    reasons = (
        GapPriorityReason.PRIMARY_ROLE
        if selection == RoleSelectionType.PRIMARY else GapPriorityReason.SECONDARY_ROLE,
        GapPriorityReason.READY_DIMENSION
        if dimension.readiness == DimensionReadiness.READY
        else GapPriorityReason.CONDITIONAL_DIMENSION,
        _classification_reason(classification),
    )
    action_type = _ACTION_BY_CLASSIFICATION[classification]
    return CareerGapDimensionItem(
        dimension_id=dimension.dimension_id,
        readiness=dimension.readiness,
        recommendation_status=assessment.status,
        gap_classification=classification,
        confirmed_criterion_ids=confirmed_criteria,
        unresolved_criterion_ids=unresolved,
        supporting_binding_ids=tuple(sorted(item.binding_id for item in assessment.supporting_bindings)),
        evidence_state=evidence_state,
        review_state=review_state,
        priority=priority,
        priority_reason_codes=reasons,
        next_action_type=action_type,
        next_action=_action_text(dimension, classification),
    )


def _role_analysis(
    *,
    role_id: str,
    selection: RoleSelectionType,
    recommendation: RoleRecommendationArtifact,
    rubric: CapabilityRubric,
    decisions: Mapping[str, BindingReviewDecision],
) -> CareerGapRoleAnalysis:
    result = next(item for item in recommendation.role_results if item.role_id == role_id)
    core = {item.dimension_id: item for item in result.core_current_fit.dimension_assessments}
    extended = {item.dimension_id: item for item in result.extended_current_fit.dimension_assessments}
    dimensions: list[CareerGapDimensionItem] = []
    for dimension in sorted(rubric.role_dimensions(role_id), key=lambda item: item.dimension_id):
        source = core if dimension.readiness == DimensionReadiness.READY else extended
        if dimension.dimension_id not in source:
            raise Phase2ValidationError(
                f"Recommendation lacks the required {dimension.readiness.value} Dimension view"
            )
        if dimension.dimension_id in core and dimension.dimension_id in extended:
            core_item = core[dimension.dimension_id]
            extended_item = extended[dimension.dimension_id]
            if core_item != extended_item:
                raise Phase2ValidationError(
                    "Core and Extended views disagree for the same Dimension"
                )
        dimensions.append(
            _dimension_item(
                dimension=dimension,
                assessment=source[dimension.dimension_id],
                selection=selection,
                decisions=decisions,
            )
        )
    values = tuple(dimensions)
    return CareerGapRoleAnalysis(
        role_id=role_id,
        selection_type=selection,
        dimensions=values,
        confirmed_strength_count=sum(
            item.gap_classification == GapClassification.CONFIRMED_STRENGTH for item in values
        ),
        development_area_count=sum(
            item.gap_classification == GapClassification.DEVELOPING_CAPABILITY for item in values
        ),
        transferable_foundation_count=sum(
            item.gap_classification == GapClassification.TRANSFERABLE_FOUNDATION for item in values
        ),
        unknown_evidence_count=sum(
            item.gap_classification == GapClassification.UNKNOWN_EVIDENCE for item in values
        ),
        capability_gap_count=sum(
            item.gap_classification == GapClassification.CAPABILITY_GAP for item in values
        ),
    )


def _validate_context(
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact,
    decision: UserRoleDecision,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None,
    superseded_decision: UserRoleDecision | None,
) -> None:
    if rubric.schema_version != 2:
        raise Phase2ValidationError("Career Gap Analysis schema 2 requires Rubric schema 2")
    if mapping.schema_version != 5:
        raise Phase2ValidationError("Career Gap Analysis schema 2 requires Mapping schema 5")
    if recommendation.schema_version != 7:
        raise Phase2ValidationError("Career Gap Analysis schema 2 requires Recommendation schema 7")
    recommendation.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping_candidates=mapping,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    if decision.schema_version != 2 or decision.status != DecisionStatus.CONFIRMED:
        raise Phase2ValidationError(
            "Career Gap Analysis requires a confirmed User Decision schema 2"
        )
    decision.validate(
        catalog,
        recommendation,
        profile,
        rubric,
        superseded_decision,
    )


def _build_career_gap_analysis(
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    recommendation: RoleRecommendationArtifact,
    decision: UserRoleDecision,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    created_at: str,
) -> CareerGapAnalysis:
    if decision.primary_role is None:
        raise Phase2ValidationError("confirmed Decision lacks a Primary Role")
    decisions = {} if evidence_reviews is None else evidence_reviews.decision_by_binding()
    primary = _role_analysis(
        role_id=decision.primary_role.role_id,
        selection=RoleSelectionType.PRIMARY,
        recommendation=recommendation,
        rubric=rubric,
        decisions=decisions,
    )
    secondary = tuple(
        _role_analysis(
            role_id=item.role_id,
            selection=RoleSelectionType.SECONDARY,
            recommendation=recommendation,
            rubric=rubric,
            decisions=decisions,
        )
        for item in decision.secondary_roles
    )
    analyses = (primary, *secondary)
    dimensions = tuple(item for role in analyses for item in role.dimensions)
    summary = CareerGapSummary(
        confirmed_strength_count=sum(role.confirmed_strength_count for role in analyses),
        development_area_count=sum(role.development_area_count for role in analyses),
        transferable_foundation_count=sum(
            role.transferable_foundation_count for role in analyses
        ),
        unknown_evidence_count=sum(role.unknown_evidence_count for role in analyses),
        capability_gap_count=sum(role.capability_gap_count for role in analyses),
        high_priority_count=sum(item.priority == GapPriority.HIGH for item in dimensions),
    )
    dimension_by_id = {item.dimension_id: item for item in rubric.dimensions}
    follow_ups = tuple(
        CareerGapFollowUp(
            follow_up_id=_follow_up_id(role.role_id, item.dimension_id, item.next_action_type),
            role_id=role.role_id,
            dimension_id=item.dimension_id,
            action_type=item.next_action_type,
            prompt=_follow_up_prompt(
                dimension_by_id[item.dimension_id], item.gap_classification
            ),
        )
        for role in analyses
        for item in sorted(
            role.dimensions,
            key=lambda value: (
                _PRIORITY_ORDER[value.priority],
                0 if value.readiness == DimensionReadiness.READY else 1,
                value.dimension_id,
            ),
        )
        if item.gap_classification not in {
            GapClassification.CONFIRMED_STRENGTH,
            GapClassification.NOT_APPLICABLE,
        }
    )
    provisional = CareerGapAnalysis(
        analysis_id="gap_analysis_pending",
        decision_id=decision.decision_id,
        recommendation_set_id=recommendation.recommendation_set_id,
        recommendation_schema_version=recommendation.schema_version,
        profile_fingerprint=profile_fingerprint(profile),
        rubric_version=rubric.rubric_version,
        rubric_fingerprint=capability_rubric_fingerprint(rubric),
        catalog_version=catalog.catalog_version,
        created_at=_iso_datetime(created_at, "career_gap_analysis.created_at"),
        primary_role_analysis=primary,
        secondary_role_analyses=secondary,
        summary=summary,
        follow_ups=follow_ups,
    )
    return CareerGapAnalysis(
        **{
            **provisional.__dict__,
            "analysis_id": generate_career_gap_analysis_id(provisional),
        }
    )


def generate_career_gap_analysis_id(value: CareerGapAnalysis) -> str:
    if not isinstance(value, CareerGapAnalysis):
        raise TypeError("value must be a CareerGapAnalysis")
    payload = value.to_dict()
    payload.pop("analysis_id", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"gap_analysis_{hashlib.sha256((CAREER_GAP_ANALYSIS_ID_VERSION + '|').encode() + encoded).hexdigest()[:24]}"


def build_career_gap_analysis(
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact,
    decision: UserRoleDecision,
    created_at: str,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> CareerGapAnalysis:
    _validate_context(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    result = _build_career_gap_analysis(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        created_at=created_at,
    )
    result.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    return result


def save_career_gap_analysis(
    value: CareerGapAnalysis,
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact,
    decision: UserRoleDecision,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> Path:
    if not isinstance(value, CareerGapAnalysis):
        raise TypeError("value must be a CareerGapAnalysis")
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Career Gap Analysis output already exists: {target}")
    value.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    return save_phase2_json(CareerGapAnalysis.from_dict(value.to_dict()).to_dict(), target)


def load_career_gap_analysis(
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact,
    decision: UserRoleDecision,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> CareerGapAnalysis:
    value = CareerGapAnalysis.from_dict(load_phase2_json(path))
    value.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    return value
