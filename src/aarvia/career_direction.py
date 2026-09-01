"""Phase 2 contracts for recommendations, decisions, and role-level gaps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .profile import CareerProfile
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


class FitType(str, Enum):
    CURRENT_FIT = "current_fit"
    DIRECTIONAL_FIT = "directional_fit"


class Fit(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    UNKNOWN = "unknown"


class ConstraintCompatibility(str, Enum):
    COMPATIBLE = "compatible"
    CONDITIONALLY_COMPATIBLE = "conditionally_compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class RecommendationTier(str, Enum):
    PRIMARY_CANDIDATE = "primary_candidate"
    STRONG_ALTERNATIVE = "strong_alternative"
    EXPLORATORY = "exploratory"
    NOT_RECOMMENDED = "not_recommended"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class DecisionStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class GapStatus(str, Enum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


_PATH_PART = re.compile(r"([a-z][a-z0-9_]*)(?:\[(\d+)\])?")
_ALLOWED_PROFILE_ROOTS = {
    "basic_profile",
    "education",
    "experience_overview",
    "skills",
    "career_preferences",
    "constraints",
}


def profile_fingerprint(profile: CareerProfile) -> str:
    """Fingerprint the exact validated Profile snapshot used by an analysis."""
    if not isinstance(profile, CareerProfile):
        raise TypeError("profile must be a CareerProfile")
    payload = json.dumps(
        CareerProfile.from_dict(profile.to_dict()).to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _json_snapshot(value: Any, path: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise Phase2ValidationError(f"{path} must be JSON-serializable") from error
    return json.loads(encoded)


def _profile_value(profile: CareerProfile, path: str) -> Any:
    parts = path.split(".")
    if not parts or parts[0].split("[", 1)[0] not in _ALLOWED_PROFILE_ROOTS:
        raise Phase2ValidationError(f"profile reference uses an unsupported path: {path}")
    value: Any = profile.to_dict()
    for raw_part in parts:
        match = _PATH_PART.fullmatch(raw_part)
        if match is None:
            raise Phase2ValidationError(f"invalid profile reference path: {path}")
        key, raw_index = match.groups()
        if not isinstance(value, Mapping) or key not in value:
            raise Phase2ValidationError(f"profile reference does not exist: {path}")
        value = value[key]
        if raw_index is not None:
            index = int(raw_index)
            if not isinstance(value, list) or index >= len(value):
                raise Phase2ValidationError(f"profile reference does not exist: {path}")
            value = value[index]
    if value in (None, "", []):
        raise Phase2ValidationError(f"profile reference is not a confirmed fact: {path}")
    return value


@dataclass(frozen=True, eq=True)
class ProfileReference:
    path: str
    value_snapshot: Any
    profile_fingerprint: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "profile_reference") -> ProfileReference:
        data = _mapping(value, path)
        _reject_unknown(data, {"path", "value_snapshot", "profile_fingerprint"}, path)
        fingerprint = _text(data.get("profile_fingerprint"), f"{path}.profile_fingerprint")
        assert fingerprint is not None
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
            raise Phase2ValidationError(f"{path}.profile_fingerprint must be a SHA-256 fingerprint")
        if "value_snapshot" not in data:
            raise Phase2ValidationError(f"{path}.value_snapshot is required")
        return cls(
            path=_text(data.get("path"), f"{path}.path"),
            value_snapshot=_json_snapshot(data["value_snapshot"], f"{path}.value_snapshot"),
            profile_fingerprint=fingerprint,
        )

    @classmethod
    def capture(cls, profile: CareerProfile, path: str) -> ProfileReference:
        return cls(path, _json_snapshot(_profile_value(profile, path), path), profile_fingerprint(profile))

    def validate(self, profile: CareerProfile) -> None:
        if self.profile_fingerprint != profile_fingerprint(profile):
            raise Phase2ValidationError("profile reference fingerprint does not match the current Profile")
        actual = _json_snapshot(_profile_value(profile, self.path), self.path)
        if actual != self.value_snapshot:
            raise Phase2ValidationError(
                f"profile reference value does not match current Profile: {self.path}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value_snapshot": _json_snapshot(self.value_snapshot, self.path),
            "profile_fingerprint": self.profile_fingerprint,
        }


@dataclass(frozen=True, eq=True)
class SupportedInference:
    inference_id: str
    statement: str
    supporting_profile_references: tuple[ProfileReference, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "inference") -> SupportedInference:
        data = _mapping(value, path)
        _reject_unknown(data, {"inference_id", "statement", "supporting_profile_references"}, path)
        references = _profile_reference_tuple(
            data.get("supporting_profile_references"), f"{path}.supporting_profile_references"
        )
        if not references:
            raise Phase2ValidationError(f"{path} requires at least one Profile reference")
        return cls(
            inference_id=_stable_id(data.get("inference_id"), f"{path}.inference_id"),
            statement=_text(data.get("statement"), f"{path}.statement"),
            supporting_profile_references=references,
        )

    def validate(self, profile: CareerProfile) -> None:
        for reference in self.supporting_profile_references:
            reference.validate(profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inference_id": self.inference_id,
            "statement": self.statement,
            "supporting_profile_references": [item.to_dict() for item in self.supporting_profile_references],
        }


@dataclass(frozen=True, eq=True)
class RoleFitRecommendation:
    recommendation_id: str
    role_id: str
    fit_type: FitType
    rank: int
    fit: Fit
    constraint_compatibility: ConstraintCompatibility
    recommendation_tier: RecommendationTier
    confidence: Confidence
    supporting_profile_references: tuple[ProfileReference, ...] = ()
    supported_inferences: tuple[SupportedInference, ...] = ()
    limitations: tuple[str, ...] = ()
    unknown_information: tuple[str, ...] = ()
    requirement_references: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "recommendation"
    ) -> RoleFitRecommendation:
        data = _mapping(value, path)
        allowed = {
            "recommendation_id", "role_id", "fit_type", "rank", "fit",
            "constraint_compatibility", "recommendation_tier", "confidence",
            "supporting_profile_references", "supported_inferences", "limitations",
            "unknown_information", "requirement_references",
        }
        _reject_unknown(data, allowed, path)
        rank = data.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise Phase2ValidationError(f"{path}.rank must be a positive integer")
        raw_inferences = data.get("supported_inferences", [])
        if not isinstance(raw_inferences, list):
            raise Phase2ValidationError(f"{path}.supported_inferences must be a list")
        inferences = tuple(
            SupportedInference.from_dict(item, f"{path}.supported_inferences[{index}]")
            for index, item in enumerate(raw_inferences)
        )
        _ensure_unique(
            (item.inference_id for item in inferences), f"{path}.supported_inferences"
        )
        return cls(
            recommendation_id=_stable_id(data.get("recommendation_id"), f"{path}.recommendation_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            fit_type=_enum(data.get("fit_type"), FitType, f"{path}.fit_type"),
            rank=rank,
            fit=_enum(data.get("fit"), Fit, f"{path}.fit"),
            constraint_compatibility=_enum(
                data.get("constraint_compatibility"),
                ConstraintCompatibility,
                f"{path}.constraint_compatibility",
            ),
            recommendation_tier=_enum(
                data.get("recommendation_tier"), RecommendationTier, f"{path}.recommendation_tier"
            ),
            confidence=_enum(data.get("confidence"), Confidence, f"{path}.confidence"),
            supporting_profile_references=_profile_reference_tuple(
                data.get("supporting_profile_references"), f"{path}.supporting_profile_references"
            ),
            supported_inferences=inferences,
            limitations=_string_tuple(data.get("limitations"), f"{path}.limitations"),
            unknown_information=_string_tuple(
                data.get("unknown_information"), f"{path}.unknown_information"
            ),
            requirement_references=_string_tuple(
                data.get("requirement_references"), f"{path}.requirement_references", ids=True
            ),
        )

    def validate(self, catalog: RoleCatalog, profile: CareerProfile) -> None:
        catalog.role(self.role_id)
        for requirement_id in self.requirement_references:
            requirement = catalog.requirement(requirement_id)
            if requirement.role_id != self.role_id:
                raise Phase2ValidationError(
                    f"recommendation {self.recommendation_id} references a requirement from another role"
                )
        for reference in self.supporting_profile_references:
            reference.validate(profile)
        for inference in self.supported_inferences:
            inference.validate(profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "role_id": self.role_id,
            "fit_type": self.fit_type.value,
            "rank": self.rank,
            "fit": self.fit.value,
            "constraint_compatibility": self.constraint_compatibility.value,
            "recommendation_tier": self.recommendation_tier.value,
            "confidence": self.confidence.value,
            "supporting_profile_references": [item.to_dict() for item in self.supporting_profile_references],
            "supported_inferences": [item.to_dict() for item in self.supported_inferences],
            "limitations": list(self.limitations),
            "unknown_information": list(self.unknown_information),
            "requirement_references": list(self.requirement_references),
        }


@dataclass(frozen=True, eq=True)
class RecommendationSet:
    recommendation_set_id: str
    catalog_version: str
    created_at: str
    current_fit: tuple[RoleFitRecommendation, ...]
    directional_fit: tuple[RoleFitRecommendation, ...]
    schema: str = "aarvia.role_recommendations"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecommendationSet:
        data = _mapping(value, "recommendations")
        allowed = {
            "schema", "schema_version", "recommendation_set_id", "catalog_version",
            "created_at", "current_fit", "directional_fit",
        }
        _reject_unknown(data, allowed, "recommendations")
        if data.get("schema") != "aarvia.role_recommendations" or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Recommendation Set schema version")
        current = _recommendation_tuple(data.get("current_fit"), "recommendations.current_fit")
        directional = _recommendation_tuple(
            data.get("directional_fit"), "recommendations.directional_fit"
        )
        result = cls(
            recommendation_set_id=_stable_id(
                data.get("recommendation_set_id"), "recommendations.recommendation_set_id"
            ),
            catalog_version=_version(data.get("catalog_version"), "recommendations.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "recommendations.created_at"),
            current_fit=current,
            directional_fit=directional,
        )
        result._validate_shape()
        return result

    def _validate_shape(self) -> None:
        all_items = self.current_fit + self.directional_fit
        _ensure_unique((item.recommendation_id for item in all_items), "recommendation IDs")
        for expected, items in (
            (FitType.CURRENT_FIT, self.current_fit),
            (FitType.DIRECTIONAL_FIT, self.directional_fit),
        ):
            _ensure_unique((item.role_id for item in items), f"{expected.value} role IDs")
            _ensure_unique((str(item.rank) for item in items), f"{expected.value} ranks")
            if any(item.fit_type != expected for item in items):
                raise Phase2ValidationError(f"{expected.value} contains the wrong fit_type")

    def validate(self, catalog: RoleCatalog, profile: CareerProfile) -> None:
        self._validate_shape()
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Recommendation Set catalog version does not match Role Catalog")
        for item in self.current_fit + self.directional_fit:
            item.validate(catalog, profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "recommendation_set_id": self.recommendation_set_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "current_fit": [item.to_dict() for item in self.current_fit],
            "directional_fit": [item.to_dict() for item in self.directional_fit],
        }


@dataclass(frozen=True, eq=True)
class SelectedRole:
    role_id: str
    specialization_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "selected_role") -> SelectedRole:
        data = _mapping(value, path)
        _reject_unknown(data, {"role_id", "specialization_ids"}, path)
        return cls(
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            specialization_ids=_string_tuple(
                data.get("specialization_ids"), f"{path}.specialization_ids", ids=True
            ),
        )

    def validate(self, catalog: RoleCatalog) -> None:
        role = catalog.role(self.role_id)
        allowed = {item.specialization_id for item in role.specializations}
        unknown = set(self.specialization_ids) - allowed
        if unknown:
            raise Phase2ValidationError(
                f"role {self.role_id} has unknown specializations: {', '.join(sorted(unknown))}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"role_id": self.role_id, "specialization_ids": list(self.specialization_ids)}


@dataclass(frozen=True, eq=True)
class UnmappedRoleDirection:
    user_provided_name: str
    created_at: str
    status: str = "unmapped"
    source: str = "user_provided"
    analysis_available: bool = False

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "unmapped_role"
    ) -> UnmappedRoleDirection:
        data = _mapping(value, path)
        allowed = {"user_provided_name", "created_at", "status", "source", "analysis_available"}
        _reject_unknown(data, allowed, path)
        if (
            data.get("status", "unmapped") != "unmapped"
            or data.get("source", "user_provided") != "user_provided"
            or data.get("analysis_available", False) is not False
        ):
            raise Phase2ValidationError(
                f"{path} must remain unmapped, user_provided, and unavailable for analysis"
            )
        return cls(
            user_provided_name=_text(data.get("user_provided_name"), f"{path}.user_provided_name"),
            created_at=_iso_datetime(data.get("created_at"), f"{path}.created_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_provided_name": self.user_provided_name,
            "created_at": self.created_at,
            "status": self.status,
            "source": self.source,
            "analysis_available": self.analysis_available,
        }


@dataclass(frozen=True, eq=True)
class UserRoleDecision:
    decision_id: str
    status: DecisionStatus
    catalog_version: str
    primary_role: SelectedRole | None
    secondary_roles: tuple[SelectedRole, ...]
    rejected_role_ids: tuple[str, ...]
    explore_later_role_ids: tuple[str, ...]
    unmapped_roles: tuple[UnmappedRoleDirection, ...]
    user_reason: str | None
    created_at: str
    updated_at: str
    source_recommendation_reference: str | None = None
    schema: str = "aarvia.user_role_decision"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> UserRoleDecision:
        data = _mapping(value, "decision")
        allowed = {
            "schema", "schema_version", "decision_id", "status", "catalog_version",
            "primary_role", "secondary_roles", "rejected_role_ids", "explore_later_role_ids",
            "unmapped_roles", "user_reason", "created_at", "updated_at",
            "source_recommendation_reference",
        }
        _reject_unknown(data, allowed, "decision")
        if data.get("schema") != "aarvia.user_role_decision" or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported User Decision schema version")
        raw_primary = data.get("primary_role")
        primary = None if raw_primary is None else SelectedRole.from_dict(raw_primary, "decision.primary_role")
        secondary = _selected_role_tuple(data.get("secondary_roles"), "decision.secondary_roles")
        raw_unmapped = data.get("unmapped_roles", [])
        if not isinstance(raw_unmapped, list):
            raise Phase2ValidationError("decision.unmapped_roles must be a list")
        unmapped = tuple(
            UnmappedRoleDirection.from_dict(item, f"decision.unmapped_roles[{index}]")
            for index, item in enumerate(raw_unmapped)
        )
        result = cls(
            decision_id=_stable_id(data.get("decision_id"), "decision.decision_id"),
            status=_enum(data.get("status"), DecisionStatus, "decision.status"),
            catalog_version=_version(data.get("catalog_version"), "decision.catalog_version"),
            primary_role=primary,
            secondary_roles=secondary,
            rejected_role_ids=_string_tuple(
                data.get("rejected_role_ids"), "decision.rejected_role_ids", ids=True
            ),
            explore_later_role_ids=_string_tuple(
                data.get("explore_later_role_ids"), "decision.explore_later_role_ids", ids=True
            ),
            unmapped_roles=unmapped,
            user_reason=_text(data.get("user_reason"), "decision.user_reason", required=False),
            created_at=_iso_datetime(data.get("created_at"), "decision.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "decision.updated_at"),
            source_recommendation_reference=_text(
                data.get("source_recommendation_reference"),
                "decision.source_recommendation_reference",
                required=False,
            ),
        )
        result._validate_shape()
        return result

    def _validate_shape(self) -> None:
        if self.status == DecisionStatus.CONFIRMED and self.primary_role is None:
            raise Phase2ValidationError("confirmed decision requires exactly one Primary Role Family")
        if len(self.secondary_roles) > 2:
            raise Phase2ValidationError("decision allows at most two Secondary Role Families")
        secondary_ids = [item.role_id for item in self.secondary_roles]
        _ensure_unique(secondary_ids, "secondary role IDs")
        primary_id = self.primary_role.role_id if self.primary_role else None
        if primary_id in secondary_ids:
            raise Phase2ValidationError("Primary Role Family cannot also be Secondary")
        selected = set(secondary_ids) | ({primary_id} if primary_id else set())
        rejected_conflicts = selected & set(self.rejected_role_ids)
        if rejected_conflicts:
            raise Phase2ValidationError(
                f"rejected roles cannot be selected: {', '.join(sorted(rejected_conflicts))}"
            )
        explore_conflicts = selected & set(self.explore_later_role_ids)
        if explore_conflicts:
            raise Phase2ValidationError(
                "explore-later roles cannot be selected: "
                f"{', '.join(sorted(explore_conflicts))}"
            )

    def _validate_catalog_selection(self, catalog: RoleCatalog) -> None:
        self._validate_shape()
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("User Decision catalog version does not match Role Catalog")
        selected = (() if self.primary_role is None else (self.primary_role,)) + self.secondary_roles
        for role in selected:
            role.validate(catalog)
        for role_id in self.rejected_role_ids + self.explore_later_role_ids:
            catalog.role(role_id)

    def validate(
        self,
        catalog: RoleCatalog,
        recommendation_set: RecommendationSet | None = None,
        profile: CareerProfile | None = None,
    ) -> None:
        self._validate_catalog_selection(catalog)
        if self.source_recommendation_reference is not None:
            if recommendation_set is None:
                raise Phase2ValidationError("source recommendation cannot be validated without its set")
            if profile is None:
                raise Phase2ValidationError(
                    "source recommendation cannot be validated without its Career Profile"
                )
            if self.source_recommendation_reference != recommendation_set.recommendation_set_id:
                raise Phase2ValidationError("User Decision references a different Recommendation Set")
            if recommendation_set.catalog_version != self.catalog_version:
                raise Phase2ValidationError(
                    "source Recommendation Set catalog version does not match User Decision"
                )
            recommendation_set.validate(catalog, profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "status": self.status.value,
            "catalog_version": self.catalog_version,
            "primary_role": None if self.primary_role is None else self.primary_role.to_dict(),
            "secondary_roles": [item.to_dict() for item in self.secondary_roles],
            "rejected_role_ids": list(self.rejected_role_ids),
            "explore_later_role_ids": list(self.explore_later_role_ids),
            "unmapped_roles": [item.to_dict() for item in self.unmapped_roles],
            "user_reason": self.user_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_recommendation_reference": self.source_recommendation_reference,
        }


@dataclass(frozen=True, eq=True)
class GapItem:
    gap_item_id: str
    role_id: str
    requirement_id: str
    status: GapStatus
    career_profile_references: tuple[ProfileReference, ...]
    supported_inference_references: tuple[str, ...]
    explanation: str
    confidence: Confidence
    follow_up_needed: bool
    follow_up_question: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "gap_item") -> GapItem:
        data = _mapping(value, path)
        allowed = {
            "gap_item_id", "role_id", "requirement_id", "status",
            "career_profile_references", "supported_inference_references", "explanation",
            "confidence", "follow_up_needed", "follow_up_question",
        }
        _reject_unknown(data, allowed, path)
        follow_up = data.get("follow_up_needed")
        if not isinstance(follow_up, bool):
            raise Phase2ValidationError(f"{path}.follow_up_needed must be a boolean")
        return cls(
            gap_item_id=_stable_id(data.get("gap_item_id"), f"{path}.gap_item_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            requirement_id=_stable_id(data.get("requirement_id"), f"{path}.requirement_id"),
            status=_enum(data.get("status"), GapStatus, f"{path}.status"),
            career_profile_references=_profile_reference_tuple(
                data.get("career_profile_references"), f"{path}.career_profile_references"
            ),
            supported_inference_references=_string_tuple(
                data.get("supported_inference_references"),
                f"{path}.supported_inference_references",
                ids=True,
            ),
            explanation=_text(data.get("explanation"), f"{path}.explanation"),
            confidence=_enum(data.get("confidence"), Confidence, f"{path}.confidence"),
            follow_up_needed=follow_up,
            follow_up_question=_text(
                data.get("follow_up_question"), f"{path}.follow_up_question", required=False
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_item_id": self.gap_item_id,
            "role_id": self.role_id,
            "requirement_id": self.requirement_id,
            "status": self.status.value,
            "career_profile_references": [item.to_dict() for item in self.career_profile_references],
            "supported_inference_references": list(self.supported_inference_references),
            "explanation": self.explanation,
            "confidence": self.confidence.value,
            "follow_up_needed": self.follow_up_needed,
            "follow_up_question": self.follow_up_question,
        }


@dataclass(frozen=True, eq=True)
class RoleGapAnalysis:
    analysis_id: str
    catalog_version: str
    created_at: str
    role_ids: tuple[str, ...]
    supported_inferences: tuple[SupportedInference, ...]
    items: tuple[GapItem, ...]
    schema: str = "aarvia.role_gap_analysis"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RoleGapAnalysis:
        data = _mapping(value, "gap_analysis")
        allowed = {
            "schema", "schema_version", "analysis_id", "catalog_version", "created_at",
            "role_ids", "supported_inferences", "items",
        }
        _reject_unknown(data, allowed, "gap_analysis")
        if data.get("schema") != "aarvia.role_gap_analysis" or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Gap Analysis schema version")
        raw_inferences = data.get("supported_inferences", [])
        raw_items = data.get("items", [])
        if not isinstance(raw_inferences, list) or not isinstance(raw_items, list):
            raise Phase2ValidationError("gap_analysis inferences and items must be lists")
        result = cls(
            analysis_id=_stable_id(data.get("analysis_id"), "gap_analysis.analysis_id"),
            catalog_version=_version(data.get("catalog_version"), "gap_analysis.catalog_version"),
            created_at=_iso_datetime(data.get("created_at"), "gap_analysis.created_at"),
            role_ids=_string_tuple(data.get("role_ids"), "gap_analysis.role_ids", ids=True),
            supported_inferences=tuple(
                SupportedInference.from_dict(item, f"gap_analysis.supported_inferences[{index}]")
                for index, item in enumerate(raw_inferences)
            ),
            items=tuple(
                GapItem.from_dict(item, f"gap_analysis.items[{index}]")
                for index, item in enumerate(raw_items)
            ),
        )
        _ensure_unique((item.inference_id for item in result.supported_inferences), "inference IDs")
        _ensure_unique((item.gap_item_id for item in result.items), "gap item IDs")
        return result

    def validate(
        self, catalog: RoleCatalog, profile: CareerProfile, decision: UserRoleDecision
    ) -> None:
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Gap Analysis catalog version does not match Role Catalog")
        for role_id in self.role_ids:
            catalog.role(role_id)
        if not isinstance(decision, UserRoleDecision):
            raise Phase2ValidationError("Gap Analysis requires a confirmed User Decision")
        decision._validate_catalog_selection(catalog)
        if decision.status != DecisionStatus.CONFIRMED:
            raise Phase2ValidationError("Gap Analysis requires a confirmed User Decision")
        selected = {
            *(item.role_id for item in decision.secondary_roles),
            *(() if decision.primary_role is None else (decision.primary_role.role_id,)),
        }
        if set(self.role_ids) - selected:
            raise Phase2ValidationError("Gap Analysis contains a role not selected by the user")
        inference_map = {item.inference_id: item for item in self.supported_inferences}
        for inference in self.supported_inferences:
            inference.validate(profile)
        for item in self.items:
            if item.role_id not in self.role_ids:
                raise Phase2ValidationError(f"gap item {item.gap_item_id} uses an unanalyzed role")
            requirement = catalog.requirement(item.requirement_id)
            if requirement.role_id != item.role_id:
                raise Phase2ValidationError(
                    f"gap item {item.gap_item_id} references a requirement from another role"
                )
            missing_inferences = set(item.supported_inference_references) - set(inference_map)
            if missing_inferences:
                raise Phase2ValidationError(
                    f"gap item {item.gap_item_id} references unknown inferences: "
                    f"{', '.join(sorted(missing_inferences))}"
                )
            for reference in item.career_profile_references:
                reference.validate(profile)
            if item.status in {GapStatus.SATISFIED, GapStatus.PARTIAL} and not (
                item.career_profile_references or item.supported_inference_references
            ):
                raise Phase2ValidationError(
                    f"gap item {item.gap_item_id} cannot be {item.status.value} without evidence"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "analysis_id": self.analysis_id,
            "catalog_version": self.catalog_version,
            "created_at": self.created_at,
            "role_ids": list(self.role_ids),
            "supported_inferences": [item.to_dict() for item in self.supported_inferences],
            "items": [item.to_dict() for item in self.items],
        }


def _profile_reference_tuple(value: Any, path: str) -> tuple[ProfileReference, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return tuple(ProfileReference.from_dict(item, f"{path}[{index}]") for index, item in enumerate(value))


def _recommendation_tuple(value: Any, path: str) -> tuple[RoleFitRecommendation, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return tuple(RoleFitRecommendation.from_dict(item, f"{path}[{index}]") for index, item in enumerate(value))


def _selected_role_tuple(value: Any, path: str) -> tuple[SelectedRole, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return tuple(SelectedRole.from_dict(item, f"{path}[{index}]") for index, item in enumerate(value))


def _ensure_unique(values: Any, path: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise Phase2ValidationError(f"{path} contains duplicates: {', '.join(sorted(duplicates))}")


def save_recommendation_set(
    value: RecommendationSet,
    path: str | Path,
    *,
    catalog: RoleCatalog,
    profile: CareerProfile,
) -> Path:
    if not isinstance(value, RecommendationSet):
        raise TypeError("value must be a RecommendationSet")
    value.validate(catalog, profile)
    from .phase2_storage import save_phase2_json

    return save_phase2_json(RecommendationSet.from_dict(value.to_dict()).to_dict(), path)


def load_recommendation_set(
    path: str | Path, *, catalog: RoleCatalog, profile: CareerProfile
) -> RecommendationSet:
    from .phase2_storage import load_phase2_json

    value = RecommendationSet.from_dict(load_phase2_json(path))
    value.validate(catalog, profile)
    return value


def save_user_role_decision(
    value: UserRoleDecision,
    path: str | Path,
    *,
    catalog: RoleCatalog,
    recommendation_set: RecommendationSet | None = None,
    profile: CareerProfile | None = None,
) -> Path:
    if not isinstance(value, UserRoleDecision):
        raise TypeError("value must be a UserRoleDecision")
    value.validate(catalog, recommendation_set, profile)
    from .phase2_storage import save_phase2_json

    return save_phase2_json(UserRoleDecision.from_dict(value.to_dict()).to_dict(), path)


def load_user_role_decision(
    path: str | Path,
    *,
    catalog: RoleCatalog,
    recommendation_set: RecommendationSet | None = None,
    profile: CareerProfile | None = None,
) -> UserRoleDecision:
    from .phase2_storage import load_phase2_json

    value = UserRoleDecision.from_dict(load_phase2_json(path))
    value.validate(catalog, recommendation_set, profile)
    return value


def save_role_gap_analysis(
    value: RoleGapAnalysis,
    path: str | Path,
    *,
    catalog: RoleCatalog,
    profile: CareerProfile,
    decision: UserRoleDecision,
) -> Path:
    if not isinstance(value, RoleGapAnalysis):
        raise TypeError("value must be a RoleGapAnalysis")
    value.validate(catalog, profile, decision)
    from .phase2_storage import save_phase2_json

    return save_phase2_json(RoleGapAnalysis.from_dict(value.to_dict()).to_dict(), path)


def load_role_gap_analysis(
    path: str | Path,
    *,
    catalog: RoleCatalog,
    profile: CareerProfile,
    decision: UserRoleDecision,
) -> RoleGapAnalysis:
    from .phase2_storage import load_phase2_json

    value = RoleGapAnalysis.from_dict(load_phase2_json(path))
    value.validate(catalog, profile, decision)
    return value
