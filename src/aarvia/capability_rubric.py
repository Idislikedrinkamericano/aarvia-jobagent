"""Versioned capability dimensions used by Role Recommendation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
from importlib import resources
from pathlib import Path
import re
from typing import Any, Mapping

from .phase2_storage import load_phase2_json, save_phase2_json
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
    _version,
    production_role_catalog,
)


CAPABILITY_RUBRIC_SCHEMA = "aarvia.role_capability_rubric"
CAPABILITY_RUBRIC_SCHEMA_VERSION = 2
SUPPORTED_CAPABILITY_RUBRIC_SCHEMA_VERSIONS = frozenset({1, 2})
DIMENSION_ID_VERSION = "capability-dimension-v1"
CRITERION_ID_VERSION = "capability-criterion-v1"
_DISPLAY_CODE = re.compile(r"^[A-Z]+-D\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DimensionReadiness(str, Enum):
    READY = "ready_for_profile_matching"
    CONDITIONAL = "conditional_pending_evidence_review"


class MarketBasisStatus(str, Enum):
    CONFIRMED = "confirmed_market_basis"
    CONDITIONAL = "conditional_market_basis"


class EvidenceClass(str, Enum):
    SKILL_NAME = "skill_name"
    SKILL_PROFICIENCY = "skill_proficiency"
    EDUCATION_FIELD = "education_field"
    EXPERIENCE_SUMMARY = "experience_summary"
    PROJECT_SUMMARY = "project_summary"


class EvidenceStatusCap(str, Enum):
    UNKNOWN = "unknown"
    ADJACENT_TRANSFERABLE = "adjacent_transferable"
    PARTIALLY_DEMONSTRATED = "partially_demonstrated"
    DEMONSTRATED = "demonstrated"


class BehaviorEvidenceRequirement(str, Enum):
    NOT_REQUIRED = "not_required"
    DEMONSTRATED_ONLY = "demonstrated_only"
    PARTIAL_AND_DEMONSTRATED = "partial_and_demonstrated"


def generate_dimension_id(*, role_id: str, display_code: str, name: str) -> str:
    values = (
        DIMENSION_ID_VERSION,
        _stable_id(role_id, "role_id"),
        _display_code(display_code, "display_code"),
        _text(name, "name"),
    )
    return f"dimension_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


def generate_criterion_id(*, dimension_id: str, criterion: str) -> str:
    values = (
        CRITERION_ID_VERSION,
        _stable_id(dimension_id, "dimension_id"),
        _text(criterion, "criterion"),
    )
    return f"criterion_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


def _display_code(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not _DISPLAY_CODE.fullmatch(result):
        raise Phase2ValidationError(f"{path} must use an uppercase code such as AI-D01")
    return result


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _positive_count(value: Any, path: str) -> int:
    result = _count(value, path)
    if result < 1:
        raise Phase2ValidationError(f"{path} must be positive")
    return result


@dataclass(frozen=True, eq=True)
class CapabilityCriterion:
    criterion_id: str
    text: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, dimension_id: str, path: str
    ) -> CapabilityCriterion:
        data = _mapping(value, path)
        _reject_unknown(data, {"criterion_id", "text"}, path)
        result = cls(
            criterion_id=_stable_id(data.get("criterion_id"), f"{path}.criterion_id"),
            text=_text(data.get("text"), f"{path}.text"),
        )
        result.validate(dimension_id=dimension_id, path=path)
        return result

    def validate(self, *, dimension_id: str, path: str = "criterion") -> None:
        expected = generate_criterion_id(dimension_id=dimension_id, criterion=self.text)
        if self.criterion_id != expected:
            raise Phase2ValidationError(f"{path}.criterion_id is not deterministic")

    def to_dict(self) -> dict[str, str]:
        return {"criterion_id": self.criterion_id, "text": self.text}


@dataclass(frozen=True, eq=True)
class ConfirmedEvidenceRule:
    minimum_independent_evidence_count: int
    required_criterion_sets: tuple[tuple[str, ...], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> ConfirmedEvidenceRule:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {"minimum_independent_evidence_count", "required_criterion_sets"},
            path,
        )
        raw_sets = data.get("required_criterion_sets")
        if not isinstance(raw_sets, list) or not raw_sets:
            raise Phase2ValidationError(f"{path}.required_criterion_sets must be a non-empty list")
        criterion_sets = tuple(
            _string_tuple(item, f"{path}.required_criterion_sets[{index}]", ids=True)
            for index, item in enumerate(raw_sets)
        )
        if any(not item for item in criterion_sets):
            raise Phase2ValidationError(f"{path}.required_criterion_sets cannot contain an empty set")
        result = cls(
            minimum_independent_evidence_count=_positive_count(
                data.get("minimum_independent_evidence_count"),
                f"{path}.minimum_independent_evidence_count",
            ),
            required_criterion_sets=criterion_sets,
        )
        result.validate(path)
        return result

    def validate(self, path: str = "confirmed_rule") -> None:
        if (
            not isinstance(self.minimum_independent_evidence_count, int)
            or isinstance(self.minimum_independent_evidence_count, bool)
            or self.minimum_independent_evidence_count < 1
        ):
            raise Phase2ValidationError(
                f"{path}.minimum_independent_evidence_count must be positive"
            )
        if (
            not isinstance(self.required_criterion_sets, tuple)
            or not self.required_criterion_sets
            or any(not isinstance(item, tuple) or not item for item in self.required_criterion_sets)
        ):
            raise Phase2ValidationError(f"{path}.required_criterion_sets cannot be empty")
        for group in self.required_criterion_sets:
            for criterion_id in group:
                _stable_id(criterion_id, f"{path}.required_criterion_sets")
        normalized = [tuple(item) for item in self.required_criterion_sets]
        if len(normalized) != len(set(normalized)):
            raise Phase2ValidationError(f"{path} contains duplicate required criterion sets")
        if any(len(item) != len(set(item)) for item in normalized):
            raise Phase2ValidationError(f"{path} contains duplicate criterion references")

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_independent_evidence_count": self.minimum_independent_evidence_count,
            "required_criterion_sets": [list(item) for item in self.required_criterion_sets],
        }


@dataclass(frozen=True, eq=True)
class EvidenceSupportPolicy:
    policy_version: str
    composite_capability: bool
    allowed_evidence_classes: tuple[EvidenceClass, ...]
    forbidden_evidence_classes: tuple[EvidenceClass, ...]
    structural_status_caps: tuple[tuple[EvidenceClass, EvidenceStatusCap], ...]
    provisional_status_cap: EvidenceStatusCap
    confirmed_partial_rule: ConfirmedEvidenceRule
    confirmed_demonstrated_rule: ConfirmedEvidenceRule
    behavior_evidence_requirement: BehaviorEvidenceRequirement
    strong_recommendation_requires_confirmed_evidence: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceSupportPolicy:
        data = _mapping(value, path)
        allowed = {
            "policy_version", "composite_capability", "allowed_evidence_classes",
            "forbidden_evidence_classes", "structural_status_caps", "provisional_status_cap",
            "confirmed_partial_rule", "confirmed_demonstrated_rule",
            "behavior_evidence_requirement", "strong_recommendation_requires_confirmed_evidence",
        }
        _reject_unknown(data, allowed, path)
        if not isinstance(data.get("composite_capability"), bool):
            raise Phase2ValidationError(f"{path}.composite_capability must be boolean")
        if not isinstance(data.get("strong_recommendation_requires_confirmed_evidence"), bool):
            raise Phase2ValidationError(
                f"{path}.strong_recommendation_requires_confirmed_evidence must be boolean"
            )
        raw_caps = _mapping(data.get("structural_status_caps"), f"{path}.structural_status_caps")
        known_classes = {item.value for item in EvidenceClass}
        _reject_unknown(raw_caps, known_classes, f"{path}.structural_status_caps")
        if set(raw_caps) != known_classes:
            raise Phase2ValidationError(
                f"{path}.structural_status_caps must define every evidence class"
            )
        result = cls(
            policy_version=_version(data.get("policy_version"), f"{path}.policy_version"),
            composite_capability=data["composite_capability"],
            allowed_evidence_classes=tuple(
                _enum(item, EvidenceClass, f"{path}.allowed_evidence_classes[{index}]")
                for index, item in enumerate(
                    _string_tuple(data.get("allowed_evidence_classes"), f"{path}.allowed_evidence_classes")
                )
            ),
            forbidden_evidence_classes=tuple(
                _enum(item, EvidenceClass, f"{path}.forbidden_evidence_classes[{index}]")
                for index, item in enumerate(
                    _string_tuple(data.get("forbidden_evidence_classes"), f"{path}.forbidden_evidence_classes")
                )
            ),
            structural_status_caps=tuple(
                (
                    evidence_class,
                    _enum(
                        raw_caps[evidence_class.value],
                        EvidenceStatusCap,
                        f"{path}.structural_status_caps.{evidence_class.value}",
                    ),
                )
                for evidence_class in EvidenceClass
            ),
            provisional_status_cap=_enum(
                data.get("provisional_status_cap"),
                EvidenceStatusCap,
                f"{path}.provisional_status_cap",
            ),
            confirmed_partial_rule=ConfirmedEvidenceRule.from_dict(
                data.get("confirmed_partial_rule"), f"{path}.confirmed_partial_rule"
            ),
            confirmed_demonstrated_rule=ConfirmedEvidenceRule.from_dict(
                data.get("confirmed_demonstrated_rule"), f"{path}.confirmed_demonstrated_rule"
            ),
            behavior_evidence_requirement=_enum(
                data.get("behavior_evidence_requirement"),
                BehaviorEvidenceRequirement,
                f"{path}.behavior_evidence_requirement",
            ),
            strong_recommendation_requires_confirmed_evidence=data[
                "strong_recommendation_requires_confirmed_evidence"
            ],
        )
        result.validate(path=path)
        return result

    @property
    def structural_caps(self) -> dict[EvidenceClass, EvidenceStatusCap]:
        return dict(self.structural_status_caps)

    def validate(
        self, *, criterion_ids: set[str] | None = None, path: str = "evidence_support_policy"
    ) -> None:
        if self.policy_version != "1.0.0":
            raise Phase2ValidationError(f"{path}.policy_version is unsupported")
        if not isinstance(self.composite_capability, bool):
            raise Phase2ValidationError(f"{path}.composite_capability must be boolean")
        if not isinstance(self.strong_recommendation_requires_confirmed_evidence, bool):
            raise Phase2ValidationError(
                f"{path}.strong_recommendation_requires_confirmed_evidence must be boolean"
            )
        if any(not isinstance(item, EvidenceClass) for item in self.allowed_evidence_classes):
            raise Phase2ValidationError(f"{path}.allowed_evidence_classes must use EvidenceClass")
        if any(not isinstance(item, EvidenceClass) for item in self.forbidden_evidence_classes):
            raise Phase2ValidationError(f"{path}.forbidden_evidence_classes must use EvidenceClass")
        if not isinstance(self.provisional_status_cap, EvidenceStatusCap):
            raise Phase2ValidationError(f"{path}.provisional_status_cap must use EvidenceStatusCap")
        if not isinstance(self.behavior_evidence_requirement, BehaviorEvidenceRequirement):
            raise Phase2ValidationError(
                f"{path}.behavior_evidence_requirement must use BehaviorEvidenceRequirement"
            )
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], EvidenceClass)
            or not isinstance(item[1], EvidenceStatusCap)
            for item in self.structural_status_caps
        ):
            raise Phase2ValidationError(
                f"{path}.structural_status_caps must use EvidenceClass and EvidenceStatusCap"
            )
        allowed = set(self.allowed_evidence_classes)
        forbidden = set(self.forbidden_evidence_classes)
        all_classes = set(EvidenceClass)
        if not allowed:
            raise Phase2ValidationError(f"{path}.allowed_evidence_classes cannot be empty")
        if len(allowed) != len(self.allowed_evidence_classes) or len(forbidden) != len(
            self.forbidden_evidence_classes
        ):
            raise Phase2ValidationError(f"{path} contains duplicate evidence classes")
        if allowed & forbidden or allowed | forbidden != all_classes:
            raise Phase2ValidationError(
                f"{path} allowed and forbidden evidence classes must be disjoint and exhaustive"
            )
        caps = self.structural_caps
        if set(caps) != all_classes or len(self.structural_status_caps) != len(all_classes):
            raise Phase2ValidationError(f"{path}.structural_status_caps must be complete")
        if any(caps[item] != EvidenceStatusCap.UNKNOWN for item in forbidden):
            raise Phase2ValidationError(f"{path} forbidden evidence classes must have an unknown cap")
        if any(
            cap not in {EvidenceStatusCap.UNKNOWN, EvidenceStatusCap.ADJACENT_TRANSFERABLE}
            for cap in caps.values()
        ):
            raise Phase2ValidationError(
                f"{path} structural evidence cannot establish partial or demonstrated status"
            )
        if self.provisional_status_cap not in {
            EvidenceStatusCap.ADJACENT_TRANSFERABLE,
            EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
        }:
            raise Phase2ValidationError(
                f"{path}.provisional_status_cap must be adjacent or partial"
            )
        behavior_classes = {EvidenceClass.EXPERIENCE_SUMMARY, EvidenceClass.PROJECT_SUMMARY}
        if (
            self.behavior_evidence_requirement != BehaviorEvidenceRequirement.NOT_REQUIRED
            and not allowed & behavior_classes
        ):
            raise Phase2ValidationError(
                f"{path} requires behavior evidence but allows no behavior evidence class"
            )
        if (
            self.provisional_status_cap == EvidenceStatusCap.PARTIALLY_DEMONSTRATED
            and not allowed & behavior_classes
        ):
            raise Phase2ValidationError(
                f"{path} provisional partial status requires a behavior evidence class"
            )
        self.confirmed_partial_rule.validate(f"{path}.confirmed_partial_rule")
        self.confirmed_demonstrated_rule.validate(f"{path}.confirmed_demonstrated_rule")
        if (
            self.confirmed_demonstrated_rule.minimum_independent_evidence_count
            < self.confirmed_partial_rule.minimum_independent_evidence_count
        ):
            raise Phase2ValidationError(
                f"{path} demonstrated evidence count cannot be below partial evidence count"
            )
        if (
            self.composite_capability
            and self.confirmed_demonstrated_rule.minimum_independent_evidence_count < 2
        ):
            raise Phase2ValidationError(
                f"{path} composite demonstrated status requires at least two independent evidence items"
            )
        if not self.strong_recommendation_requires_confirmed_evidence:
            raise Phase2ValidationError(
                f"{path} strong recommendations must require confirmed evidence"
            )
        if criterion_ids is not None:
            for rule_name, rule in (
                ("confirmed_partial_rule", self.confirmed_partial_rule),
                ("confirmed_demonstrated_rule", self.confirmed_demonstrated_rule),
            ):
                referenced = {item for group in rule.required_criterion_sets for item in group}
                missing = referenced - criterion_ids
                if missing:
                    raise Phase2ValidationError(
                        f"{path}.{rule_name} references criteria outside its Dimension: "
                        + ", ".join(sorted(missing))
                    )
                if referenced != criterion_ids:
                    raise Phase2ValidationError(
                        f"{path}.{rule_name} must cover every criterion in its Dimension"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "composite_capability": self.composite_capability,
            "allowed_evidence_classes": [item.value for item in self.allowed_evidence_classes],
            "forbidden_evidence_classes": [item.value for item in self.forbidden_evidence_classes],
            "structural_status_caps": {
                evidence_class.value: cap.value
                for evidence_class, cap in self.structural_status_caps
            },
            "provisional_status_cap": self.provisional_status_cap.value,
            "confirmed_partial_rule": self.confirmed_partial_rule.to_dict(),
            "confirmed_demonstrated_rule": self.confirmed_demonstrated_rule.to_dict(),
            "behavior_evidence_requirement": self.behavior_evidence_requirement.value,
            "strong_recommendation_requires_confirmed_evidence": (
                self.strong_recommendation_requires_confirmed_evidence
            ),
        }


@dataclass(frozen=True, eq=True)
class CapabilityDimension:
    dimension_id: str
    display_code: str
    role_id: str
    name: str
    description: str
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    analytical_category: str
    readiness: DimensionReadiness
    market_basis_status: MarketBasisStatus
    confirmed_company_count: int
    projected_company_count: int
    sample_denominator: int
    shared_dimension_key: str | None
    profile_evidence_types: tuple[str, ...]
    criterion_ids: tuple[str, ...] = ()
    evidence_support_policy: EvidenceSupportPolicy | None = None

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "dimension", *, schema_version: int = 1
    ) -> CapabilityDimension:
        data = _mapping(value, path)
        allowed = {
            "dimension_id", "display_code", "role_id", "name", "description",
            "inclusion_criteria", "exclusion_criteria", "analytical_category",
            "readiness", "market_basis_status", "confirmed_company_count",
            "projected_company_count", "sample_denominator", "shared_dimension_key",
            "profile_evidence_types",
        }
        if schema_version == 2:
            allowed.add("evidence_support_policy")
        _reject_unknown(data, allowed, path)
        raw_criteria = data.get("inclusion_criteria")
        if schema_version == 1:
            inclusion_criteria = _string_tuple(raw_criteria, f"{path}.inclusion_criteria")
            criterion_ids: tuple[str, ...] = ()
            policy = None
        elif schema_version == 2:
            if not isinstance(raw_criteria, list) or not raw_criteria:
                raise Phase2ValidationError(f"{path}.inclusion_criteria must be a non-empty list")
            dimension_id = _stable_id(data.get("dimension_id"), f"{path}.dimension_id")
            criteria = tuple(
                CapabilityCriterion.from_dict(
                    item,
                    dimension_id=dimension_id,
                    path=f"{path}.inclusion_criteria[{index}]",
                )
                for index, item in enumerate(raw_criteria)
            )
            inclusion_criteria = tuple(item.text for item in criteria)
            criterion_ids = tuple(item.criterion_id for item in criteria)
            policy = EvidenceSupportPolicy.from_dict(
                data.get("evidence_support_policy"), f"{path}.evidence_support_policy"
            )
        else:
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        result = cls(
            dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
            display_code=_display_code(data.get("display_code"), f"{path}.display_code"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            name=_text(data.get("name"), f"{path}.name"),
            description=_text(data.get("description"), f"{path}.description"),
            inclusion_criteria=inclusion_criteria,
            exclusion_criteria=_string_tuple(data.get("exclusion_criteria"), f"{path}.exclusion_criteria"),
            analytical_category=_stable_id(data.get("analytical_category"), f"{path}.analytical_category"),
            readiness=_enum(data.get("readiness"), DimensionReadiness, f"{path}.readiness"),
            market_basis_status=_enum(
                data.get("market_basis_status"), MarketBasisStatus, f"{path}.market_basis_status"
            ),
            confirmed_company_count=_count(
                data.get("confirmed_company_count"), f"{path}.confirmed_company_count"
            ),
            projected_company_count=_count(
                data.get("projected_company_count"), f"{path}.projected_company_count"
            ),
            sample_denominator=_count(data.get("sample_denominator"), f"{path}.sample_denominator"),
            shared_dimension_key=_stable_id(
                data.get("shared_dimension_key"), f"{path}.shared_dimension_key"
            ) if data.get("shared_dimension_key") is not None else None,
            profile_evidence_types=_string_tuple(
                data.get("profile_evidence_types"), f"{path}.profile_evidence_types", ids=True
            ),
            criterion_ids=criterion_ids,
            evidence_support_policy=policy,
        )
        result.validate_identity(path, schema_version=schema_version)
        return result

    @property
    def criteria(self) -> tuple[CapabilityCriterion, ...]:
        if len(self.criterion_ids) != len(self.inclusion_criteria):
            raise Phase2ValidationError("criterion IDs must match inclusion criteria")
        return tuple(
            CapabilityCriterion(criterion_id=criterion_id, text=text)
            for criterion_id, text in zip(self.criterion_ids, self.inclusion_criteria, strict=True)
        )

    def validate_identity(self, path: str = "dimension", *, schema_version: int = 1) -> None:
        expected = generate_dimension_id(
            role_id=self.role_id, display_code=self.display_code, name=self.name
        )
        if self.dimension_id != expected:
            raise Phase2ValidationError(f"{path}.dimension_id is not deterministic")
        if not self.inclusion_criteria or not self.exclusion_criteria:
            raise Phase2ValidationError(f"{path} requires inclusion and exclusion criteria")
        if not self.profile_evidence_types:
            raise Phase2ValidationError(f"{path} requires Profile evidence types")
        if self.sample_denominator < 1:
            raise Phase2ValidationError(f"{path}.sample_denominator must be positive")
        if self.confirmed_company_count > self.projected_company_count:
            raise Phase2ValidationError(f"{path} confirmed count cannot exceed projected count")
        if self.projected_company_count > self.sample_denominator:
            raise Phase2ValidationError(f"{path} projected count cannot exceed denominator")
        expected_basis = (
            MarketBasisStatus.CONFIRMED
            if self.readiness == DimensionReadiness.READY
            else MarketBasisStatus.CONDITIONAL
        )
        if self.market_basis_status != expected_basis:
            raise Phase2ValidationError(f"{path} readiness and market basis are inconsistent")

        if schema_version == 1:
            if self.criterion_ids or self.evidence_support_policy is not None:
                raise Phase2ValidationError(f"{path} schema 1 cannot contain criterion IDs or policy")
            return
        if schema_version != 2:
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        if len(self.criterion_ids) != len(self.inclusion_criteria):
            raise Phase2ValidationError(f"{path} criterion IDs must match inclusion criteria")
        if len(set(self.criterion_ids)) != len(self.criterion_ids):
            raise Phase2ValidationError(f"{path} contains duplicate criterion IDs")
        if len(set(self.inclusion_criteria)) != len(self.inclusion_criteria):
            raise Phase2ValidationError(f"{path} contains duplicate inclusion criteria")
        for index, criterion in enumerate(self.criteria):
            criterion.validate(dimension_id=self.dimension_id, path=f"{path}.inclusion_criteria[{index}]")
        if self.evidence_support_policy is None:
            raise Phase2ValidationError(f"{path} schema 2 requires an Evidence Support Policy")
        self.evidence_support_policy.validate(
            criterion_ids=set(self.criterion_ids), path=f"{path}.evidence_support_policy"
        )

    def to_dict(self, *, schema_version: int = 1) -> dict[str, Any]:
        if schema_version == 1:
            inclusion_criteria: list[Any] = list(self.inclusion_criteria)
        elif schema_version == 2:
            inclusion_criteria = [item.to_dict() for item in self.criteria]
        else:
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        return {
            "dimension_id": self.dimension_id,
            "display_code": self.display_code,
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": list(self.exclusion_criteria),
            "analytical_category": self.analytical_category,
            "readiness": self.readiness.value,
            "market_basis_status": self.market_basis_status.value,
            "confirmed_company_count": self.confirmed_company_count,
            "projected_company_count": self.projected_company_count,
            "sample_denominator": self.sample_denominator,
            "shared_dimension_key": self.shared_dimension_key,
            "profile_evidence_types": list(self.profile_evidence_types),
        } | (
            {"evidence_support_policy": self.evidence_support_policy.to_dict()}
            if schema_version == 2 and self.evidence_support_policy is not None
            else {}
        )


@dataclass(frozen=True, eq=True)
class CapabilityRubric:
    rubric_version: str
    role_catalog_version: str
    as_of_date: str
    supported_role_ids: tuple[str, ...]
    dimensions: tuple[CapabilityDimension, ...]
    source_analysis_hashes: tuple[str, ...]
    provenance_summary: str
    schema: str = CAPABILITY_RUBRIC_SCHEMA
    schema_version: int = CAPABILITY_RUBRIC_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityRubric:
        data = _mapping(value, "capability_rubric")
        allowed = {
            "schema", "schema_version", "rubric_version", "role_catalog_version",
            "as_of_date", "supported_role_ids", "dimensions", "source_analysis_hashes",
            "provenance_summary",
        }
        _reject_unknown(data, allowed, "capability_rubric")
        schema_version = data.get("schema_version")
        if (
            data.get("schema") != CAPABILITY_RUBRIC_SCHEMA
            or schema_version not in SUPPORTED_CAPABILITY_RUBRIC_SCHEMA_VERSIONS
        ):
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        try:
            date.fromisoformat(str(data.get("as_of_date")))
        except ValueError as error:
            raise Phase2ValidationError("capability_rubric.as_of_date must use YYYY-MM-DD") from error
        raw_dimensions = data.get("dimensions")
        if not isinstance(raw_dimensions, list):
            raise Phase2ValidationError("capability_rubric.dimensions must be a list")
        hashes = _string_tuple(data.get("source_analysis_hashes"), "capability_rubric.source_analysis_hashes")
        if not hashes or any(not _SHA256.fullmatch(value) for value in hashes):
            raise Phase2ValidationError("source analysis hashes must be SHA-256 values")
        result = cls(
            rubric_version=_version(data.get("rubric_version"), "capability_rubric.rubric_version"),
            role_catalog_version=_version(
                data.get("role_catalog_version"), "capability_rubric.role_catalog_version"
            ),
            as_of_date=str(data.get("as_of_date")),
            supported_role_ids=_string_tuple(
                data.get("supported_role_ids"), "capability_rubric.supported_role_ids", ids=True
            ),
            dimensions=tuple(
                CapabilityDimension.from_dict(item, f"capability_rubric.dimensions[{index}]")
                if schema_version == 1
                else CapabilityDimension.from_dict(
                    item, f"capability_rubric.dimensions[{index}]", schema_version=2
                )
                for index, item in enumerate(raw_dimensions)
            ),
            source_analysis_hashes=hashes,
            provenance_summary=_text(
                data.get("provenance_summary"), "capability_rubric.provenance_summary"
            ),
            schema_version=schema_version,
        )
        result._validate_shape()
        return result

    def _validate_shape(self) -> None:
        if (
            self.schema != CAPABILITY_RUBRIC_SCHEMA
            or not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version not in SUPPORTED_CAPABILITY_RUBRIC_SCHEMA_VERSIONS
        ):
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        if len(set(self.supported_role_ids)) != len(self.supported_role_ids):
            raise Phase2ValidationError("Capability Rubric contains duplicate supported roles")
        ids = [item.dimension_id for item in self.dimensions]
        codes = [item.display_code for item in self.dimensions]
        if len(ids) != len(set(ids)):
            raise Phase2ValidationError("Capability Rubric contains duplicate dimension IDs")
        if len(codes) != len(set(codes)):
            raise Phase2ValidationError("Capability Rubric contains duplicate display codes")
        dimension_roles = {item.role_id for item in self.dimensions}
        if dimension_roles != set(self.supported_role_ids):
            raise Phase2ValidationError("Capability Rubric supported roles do not match dimensions")

    def validate(self, catalog: RoleCatalog) -> None:
        self._validate_shape()
        if self.role_catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Capability Rubric Role Catalog version mismatch")
        for role_id in self.supported_role_ids:
            catalog.role(role_id)
        for index, dimension in enumerate(self.dimensions):
            dimension.validate_identity(
                f"capability_rubric.dimensions[{index}]", schema_version=self.schema_version
            )
            catalog.role(dimension.role_id)

    def dimension(self, dimension_id: str) -> CapabilityDimension:
        for item in self.dimensions:
            if item.dimension_id == dimension_id:
                return item
        raise Phase2ValidationError(f"unknown capability dimension: {dimension_id}")

    def role_dimensions(self, role_id: str) -> tuple[CapabilityDimension, ...]:
        if role_id not in self.supported_role_ids:
            raise Phase2ValidationError(f"unsupported rubric role: {role_id}")
        return tuple(item for item in self.dimensions if item.role_id == role_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "rubric_version": self.rubric_version,
            "role_catalog_version": self.role_catalog_version,
            "as_of_date": self.as_of_date,
            "supported_role_ids": list(self.supported_role_ids),
            "dimensions": [
                item.to_dict(schema_version=self.schema_version) for item in self.dimensions
            ],
            "source_analysis_hashes": list(self.source_analysis_hashes),
            "provenance_summary": self.provenance_summary,
        }


def load_capability_rubric(
    path: str | Path, *, catalog: RoleCatalog | None = None
) -> CapabilityRubric:
    value = CapabilityRubric.from_dict(load_phase2_json(path))
    value.validate(catalog or production_role_catalog())
    return value


def save_capability_rubric(
    value: CapabilityRubric, path: str | Path, *, catalog: RoleCatalog | None = None
) -> Path:
    if not isinstance(value, CapabilityRubric):
        raise TypeError("value must be a CapabilityRubric")
    value.validate(catalog or production_role_catalog())
    return save_phase2_json(CapabilityRubric.from_dict(value.to_dict()).to_dict(), path)


def production_capability_rubric() -> CapabilityRubric:
    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-2.0.0.json")
    with resources.as_file(resource) as path:
        return load_capability_rubric(path, catalog=production_role_catalog())
