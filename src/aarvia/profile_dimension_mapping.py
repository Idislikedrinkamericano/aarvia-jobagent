"""Strict provider-candidate boundary for Profile-to-capability mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from .capability_rubric import CapabilityRubric
from .career_direction import ProfileReference, profile_fingerprint
from .llm_client import LLMRequestError, LLMSettings, create_openai_client, is_bailian_endpoint, safe_llm_error
from .narrative_extraction import parse_provider_output
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
)


MAPPING_SCHEMA = "aarvia.profile_dimension_mapping_candidates"
MAPPING_SCHEMA_VERSION = 1
MAPPING_ID_VERSION = "profile-dimension-mapping-v1"


class CurrentMatchStatus(str, Enum):
    DEMONSTRATED = "demonstrated"
    PARTIAL = "partially_demonstrated"
    ADJACENT = "adjacent_transferable"
    UNKNOWN = "unknown"
    NOT_DEMONSTRATED = "not_demonstrated"
    NOT_APPLICABLE = "not_applicable"


class DirectionalSignalType(str, Enum):
    CAREER_GOAL = "career_goal_alignment"
    WORK_CONTENT = "work_content_preference_alignment"
    GROWTH_DIRECTION = "growth_direction_alignment"
    TRANSFERABLE_FOUNDATION = "transferable_foundation"


class DirectionalStatus(str, Enum):
    ALIGNED = "aligned"
    PARTIAL = "partially_aligned"
    MISALIGNED = "misaligned"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MappingConstraintStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class InferenceType(str, Enum):
    DIRECT = "direct"
    BOUNDED_SEMANTIC = "bounded_semantic"
    ADJACENT_TRANSFER = "adjacent_transfer"
    INTEREST_ONLY_DIRECTIONAL = "interest_only_directional"


class EvidenceStrength(str, Enum):
    STRONG = "strong"
    SUPPORTING = "supporting"
    WEAK = "weak"


class ContributionRelationship(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class ProviderConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _fact_identity(reference: ProfileReference) -> str:
    snapshot = json.dumps(reference.value_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{reference.path}|{snapshot}|{reference.profile_fingerprint}"


def generate_mapping_id(
    *, role_id: str, dimension_id: str, status: CurrentMatchStatus,
    relationship: ContributionRelationship, references: tuple[ProfileReference, ...]
) -> str:
    values = (
        MAPPING_ID_VERSION, role_id, dimension_id, status.value, relationship.value,
        *sorted(_fact_identity(item) for item in references),
    )
    return f"mapping_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


def _provider_references(
    value: Any, *, path: str, profile: CareerProfile
) -> tuple[ProfileReference, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    references: list[ProfileReference] = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{path}[{index}]")
        _reject_unknown(data, {"path", "value_snapshot"}, f"{path}[{index}]")
        raw_path = _text(data.get("path"), f"{path}[{index}].path")
        reference = ProfileReference.capture(profile, raw_path)
        if data.get("value_snapshot") != reference.value_snapshot:
            raise Phase2ValidationError(
                f"{path}[{index}].value_snapshot does not match the Career Profile"
            )
        references.append(reference)
    identities = [_fact_identity(item) for item in references]
    if len(identities) != len(set(identities)):
        raise Phase2ValidationError(f"{path} contains duplicate Profile references")
    return tuple(references)


@dataclass(frozen=True, eq=True)
class ProfileDimensionMappingCandidate:
    mapping_id: str
    role_id: str
    dimension_id: str
    match_status: CurrentMatchStatus
    profile_fact_references: tuple[ProfileReference, ...]
    reasoning: str
    inference_type: InferenceType
    evidence_strength: EvidenceStrength
    provider_confidence: ProviderConfidence
    review_required: bool
    relationship: ContributionRelationship
    suggested_follow_up: str | None = None

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric) -> None:
        dimension = rubric.dimension(self.dimension_id)
        if dimension.role_id != self.role_id:
            raise Phase2ValidationError("mapping dimension belongs to another Role Family")
        expected = generate_mapping_id(
            role_id=self.role_id, dimension_id=self.dimension_id, status=self.match_status,
            relationship=self.relationship, references=self.profile_fact_references,
        )
        if self.mapping_id != expected:
            raise Phase2ValidationError("mapping ID is not deterministic")
        if self.match_status in {
            CurrentMatchStatus.DEMONSTRATED,
            CurrentMatchStatus.PARTIAL,
            CurrentMatchStatus.ADJACENT,
        } and not self.profile_fact_references:
            raise Phase2ValidationError("positive capability mapping requires Profile evidence")
        if self.inference_type == InferenceType.INTEREST_ONLY_DIRECTIONAL:
            raise Phase2ValidationError("interest cannot support Current Fit")
        if self.inference_type in {InferenceType.BOUNDED_SEMANTIC, InferenceType.ADJACENT_TRANSFER} and not self.review_required:
            raise Phase2ValidationError("semantic or transferable mapping requires review")
        if self.relationship == ContributionRelationship.SECONDARY and self.evidence_strength == EvidenceStrength.STRONG:
            raise Phase2ValidationError("secondary mapping cannot claim strong evidence")
        for reference in self.profile_fact_references:
            reference.validate(profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_id": self.mapping_id, "role_id": self.role_id,
            "dimension_id": self.dimension_id, "match_status": self.match_status.value,
            "profile_fact_references": [item.to_dict() for item in self.profile_fact_references],
            "reasoning": self.reasoning, "inference_type": self.inference_type.value,
            "evidence_strength": self.evidence_strength.value,
            "provider_confidence": self.provider_confidence.value,
            "review_required": self.review_required, "relationship": self.relationship.value,
            "suggested_follow_up": self.suggested_follow_up,
        }


@dataclass(frozen=True, eq=True)
class DirectionalSignalCandidate:
    role_id: str
    signal_type: DirectionalSignalType
    status: DirectionalStatus
    profile_fact_references: tuple[ProfileReference, ...]
    reasoning: str
    provider_confidence: ProviderConfidence
    review_required: bool
    suggested_follow_up: str | None = None

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric) -> None:
        if self.role_id not in rubric.supported_role_ids:
            raise Phase2ValidationError(f"unsupported directional role: {self.role_id}")
        if self.status in {DirectionalStatus.ALIGNED, DirectionalStatus.PARTIAL, DirectionalStatus.MISALIGNED} and not self.profile_fact_references:
            raise Phase2ValidationError("assessed directional signal requires Profile evidence")
        for reference in self.profile_fact_references:
            reference.validate(profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id, "signal_type": self.signal_type.value,
            "status": self.status.value,
            "profile_fact_references": [item.to_dict() for item in self.profile_fact_references],
            "reasoning": self.reasoning, "provider_confidence": self.provider_confidence.value,
            "review_required": self.review_required, "suggested_follow_up": self.suggested_follow_up,
        }


@dataclass(frozen=True, eq=True)
class ConstraintCompatibilityCandidate:
    role_id: str
    status: MappingConstraintStatus
    profile_fact_references: tuple[ProfileReference, ...]
    reasoning: str
    provider_confidence: ProviderConfidence
    review_required: bool
    unknown_constraint_ids: tuple[str, ...] = ()
    suggested_follow_up: str | None = None

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric) -> None:
        if self.role_id not in rubric.supported_role_ids:
            raise Phase2ValidationError(f"unsupported constraint role: {self.role_id}")
        if self.status in {MappingConstraintStatus.COMPATIBLE, MappingConstraintStatus.INCOMPATIBLE} and not self.profile_fact_references:
            raise Phase2ValidationError("constraint conclusion requires Profile evidence")
        for reference in self.profile_fact_references:
            reference.validate(profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id, "status": self.status.value,
            "profile_fact_references": [item.to_dict() for item in self.profile_fact_references],
            "reasoning": self.reasoning, "provider_confidence": self.provider_confidence.value,
            "review_required": self.review_required,
            "unknown_constraint_ids": list(self.unknown_constraint_ids),
            "suggested_follow_up": self.suggested_follow_up,
        }


@dataclass(frozen=True, eq=True)
class ProfileDimensionMappingCandidateSet:
    rubric_version: str
    role_catalog_version: str
    profile_fingerprint: str
    mappings: tuple[ProfileDimensionMappingCandidate, ...]
    directional_signals: tuple[DirectionalSignalCandidate, ...]
    constraints: tuple[ConstraintCompatibilityCandidate, ...]
    conflict_warnings: tuple[str, ...] = ()
    provider_name: str = "offline"
    provider_model: str = "offline"
    schema: str = MAPPING_SCHEMA
    schema_version: int = MAPPING_SCHEMA_VERSION

    @classmethod
    def from_provider_payload(
        cls, payload: Mapping[str, Any], *, profile: CareerProfile,
        rubric: CapabilityRubric, catalog: RoleCatalog,
        provider_name: str, provider_model: str,
    ) -> ProfileDimensionMappingCandidateSet:
        data = _mapping(payload, "mapping_candidates")
        allowed = {"mappings", "directional_signals", "constraints", "conflict_warnings"}
        _reject_unknown(data, allowed, "mapping_candidates")
        raw_mappings = data.get("mappings")
        raw_directional = data.get("directional_signals")
        raw_constraints = data.get("constraints")
        if not all(isinstance(item, list) for item in (raw_mappings, raw_directional, raw_constraints)):
            raise Phase2ValidationError("mapping candidate collections must be lists")
        mappings = []
        for index, raw in enumerate(raw_mappings):
            path = f"mapping_candidates.mappings[{index}]"
            item = _mapping(raw, path)
            _reject_unknown(item, {
                "role_id", "dimension_id", "match_status", "profile_fact_references",
                "reasoning", "inference_type", "evidence_strength", "provider_confidence",
                "review_required", "relationship", "suggested_follow_up",
            }, path)
            refs = _provider_references(item.get("profile_fact_references"), path=f"{path}.profile_fact_references", profile=profile)
            status = _enum(item.get("match_status"), CurrentMatchStatus, f"{path}.match_status")
            relationship = _enum(item.get("relationship"), ContributionRelationship, f"{path}.relationship")
            role_id = _stable_id(item.get("role_id"), f"{path}.role_id")
            dimension_id = _stable_id(item.get("dimension_id"), f"{path}.dimension_id")
            mappings.append(ProfileDimensionMappingCandidate(
                mapping_id=generate_mapping_id(role_id=role_id, dimension_id=dimension_id, status=status, relationship=relationship, references=refs),
                role_id=role_id, dimension_id=dimension_id, match_status=status,
                profile_fact_references=refs,
                reasoning=_text(item.get("reasoning"), f"{path}.reasoning"),
                inference_type=_enum(item.get("inference_type"), InferenceType, f"{path}.inference_type"),
                evidence_strength=_enum(item.get("evidence_strength"), EvidenceStrength, f"{path}.evidence_strength"),
                provider_confidence=_enum(item.get("provider_confidence"), ProviderConfidence, f"{path}.provider_confidence"),
                review_required=_boolean(item.get("review_required"), f"{path}.review_required"),
                relationship=relationship,
                suggested_follow_up=_text(item.get("suggested_follow_up"), f"{path}.suggested_follow_up", required=False),
            ))
        directional = tuple(
            _directional_from_provider(item, index=index, profile=profile)
            for index, item in enumerate(raw_directional)
        )
        constraints = tuple(
            _constraint_from_provider(item, index=index, profile=profile)
            for index, item in enumerate(raw_constraints)
        )
        result = cls(
            rubric_version=rubric.rubric_version,
            role_catalog_version=catalog.catalog_version,
            profile_fingerprint=profile_fingerprint(profile),
            mappings=tuple(mappings), directional_signals=directional,
            constraints=constraints,
            conflict_warnings=_string_tuple(data.get("conflict_warnings"), "mapping_candidates.conflict_warnings"),
            provider_name=_text(provider_name, "provider_name"),
            provider_model=_text(provider_model, "provider_model"),
        )
        result.validate(profile=profile, rubric=rubric, catalog=catalog)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProfileDimensionMappingCandidateSet:
        data = _mapping(value, "mapping_candidates")
        allowed = {
            "schema", "schema_version", "rubric_version", "role_catalog_version",
            "profile_fingerprint", "mappings", "directional_signals", "constraints",
            "conflict_warnings", "provider_name", "provider_model",
        }
        _reject_unknown(data, allowed, "mapping_candidates")
        if data.get("schema") != MAPPING_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Profile mapping schema version")
        # Typed JSON contains local IDs/fingerprints, unlike provider payloads.
        mappings = tuple(_mapping_from_dict(item, index) for index, item in enumerate(_list(data.get("mappings"), "mapping_candidates.mappings")))
        directional = tuple(_directional_from_dict(item, index) for index, item in enumerate(_list(data.get("directional_signals"), "mapping_candidates.directional_signals")))
        constraints = tuple(_constraint_from_dict(item, index) for index, item in enumerate(_list(data.get("constraints"), "mapping_candidates.constraints")))
        return cls(
            rubric_version=_text(data.get("rubric_version"), "mapping_candidates.rubric_version"),
            role_catalog_version=_text(data.get("role_catalog_version"), "mapping_candidates.role_catalog_version"),
            profile_fingerprint=_text(data.get("profile_fingerprint"), "mapping_candidates.profile_fingerprint"),
            mappings=mappings, directional_signals=directional, constraints=constraints,
            conflict_warnings=_string_tuple(data.get("conflict_warnings"), "mapping_candidates.conflict_warnings"),
            provider_name=_text(data.get("provider_name"), "mapping_candidates.provider_name"),
            provider_model=_text(data.get("provider_model"), "mapping_candidates.provider_model"),
        )

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> None:
        rubric.validate(catalog)
        if self.rubric_version != rubric.rubric_version or self.role_catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("mapping candidate version context mismatch")
        if self.profile_fingerprint != profile_fingerprint(profile):
            raise Phase2ValidationError("mapping candidate Profile fingerprint mismatch")
        ids = [item.mapping_id for item in self.mappings]
        if len(ids) != len(set(ids)):
            raise Phase2ValidationError("mapping candidates contain duplicate mapping IDs")
        contribution_keys: set[tuple[str, str]] = set()
        primary_by_fact: dict[tuple[str, str], str] = {}
        for item in self.mappings:
            item.validate(profile=profile, rubric=rubric)
            for reference in item.profile_fact_references:
                fact = _fact_identity(reference)
                key = (fact, item.dimension_id)
                if key in contribution_keys:
                    raise Phase2ValidationError("duplicate Profile fact contribution to a dimension")
                contribution_keys.add(key)
                if item.relationship == ContributionRelationship.PRIMARY:
                    role_fact = (item.role_id, fact)
                    previous = primary_by_fact.get(role_fact)
                    if previous is not None and previous != item.dimension_id:
                        raise Phase2ValidationError("one broad Profile fact cannot be primary for multiple dimensions")
                    primary_by_fact[role_fact] = item.dimension_id
        signal_keys = [(item.role_id, item.signal_type) for item in self.directional_signals]
        if len(signal_keys) != len(set(signal_keys)):
            raise Phase2ValidationError("duplicate directional signal")
        for item in self.directional_signals:
            item.validate(profile=profile, rubric=rubric)
        constraint_roles = [item.role_id for item in self.constraints]
        if len(constraint_roles) != len(set(constraint_roles)):
            raise Phase2ValidationError("duplicate Role constraint candidate")
        for item in self.constraints:
            item.validate(profile=profile, rubric=rubric)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "rubric_version": self.rubric_version, "role_catalog_version": self.role_catalog_version,
            "profile_fingerprint": self.profile_fingerprint,
            "mappings": [item.to_dict() for item in self.mappings],
            "directional_signals": [item.to_dict() for item in self.directional_signals],
            "constraints": [item.to_dict() for item in self.constraints],
            "conflict_warnings": list(self.conflict_warnings),
            "provider_name": self.provider_name, "provider_model": self.provider_model,
        }


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise Phase2ValidationError(f"{path} must be a boolean")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return value


def _typed_references(value: Any, path: str) -> tuple[ProfileReference, ...]:
    return tuple(ProfileReference.from_dict(item, f"{path}[{index}]") for index, item in enumerate(_list(value, path)))


def _mapping_from_dict(value: Any, index: int) -> ProfileDimensionMappingCandidate:
    path = f"mapping_candidates.mappings[{index}]"; data = _mapping(value, path)
    allowed = {"mapping_id", "role_id", "dimension_id", "match_status", "profile_fact_references", "reasoning", "inference_type", "evidence_strength", "provider_confidence", "review_required", "relationship", "suggested_follow_up"}
    _reject_unknown(data, allowed, path)
    return ProfileDimensionMappingCandidate(
        mapping_id=_stable_id(data.get("mapping_id"), f"{path}.mapping_id"), role_id=_stable_id(data.get("role_id"), f"{path}.role_id"), dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
        match_status=_enum(data.get("match_status"), CurrentMatchStatus, f"{path}.match_status"),
        profile_fact_references=_typed_references(data.get("profile_fact_references"), f"{path}.profile_fact_references"),
        reasoning=_text(data.get("reasoning"), f"{path}.reasoning"), inference_type=_enum(data.get("inference_type"), InferenceType, f"{path}.inference_type"), evidence_strength=_enum(data.get("evidence_strength"), EvidenceStrength, f"{path}.evidence_strength"), provider_confidence=_enum(data.get("provider_confidence"), ProviderConfidence, f"{path}.provider_confidence"), review_required=_boolean(data.get("review_required"), f"{path}.review_required"), relationship=_enum(data.get("relationship"), ContributionRelationship, f"{path}.relationship"), suggested_follow_up=_text(data.get("suggested_follow_up"), f"{path}.suggested_follow_up", required=False),
    )


def _directional_from_provider(value: Any, *, index: int, profile: CareerProfile) -> DirectionalSignalCandidate:
    path=f"mapping_candidates.directional_signals[{index}]"; data=_mapping(value,path)
    _reject_unknown(data,{"role_id","signal_type","status","profile_fact_references","reasoning","provider_confidence","review_required","suggested_follow_up"},path)
    return DirectionalSignalCandidate(role_id=_stable_id(data.get("role_id"),f"{path}.role_id"),signal_type=_enum(data.get("signal_type"),DirectionalSignalType,f"{path}.signal_type"),status=_enum(data.get("status"),DirectionalStatus,f"{path}.status"),profile_fact_references=_provider_references(data.get("profile_fact_references"),path=f"{path}.profile_fact_references",profile=profile),reasoning=_text(data.get("reasoning"),f"{path}.reasoning"),provider_confidence=_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review_required=_boolean(data.get("review_required"),f"{path}.review_required"),suggested_follow_up=_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


def _directional_from_dict(value: Any, index: int) -> DirectionalSignalCandidate:
    path=f"mapping_candidates.directional_signals[{index}]"; data=_mapping(value,path)
    _reject_unknown(data,{"role_id","signal_type","status","profile_fact_references","reasoning","provider_confidence","review_required","suggested_follow_up"},path)
    return DirectionalSignalCandidate(role_id=_stable_id(data.get("role_id"),f"{path}.role_id"),signal_type=_enum(data.get("signal_type"),DirectionalSignalType,f"{path}.signal_type"),status=_enum(data.get("status"),DirectionalStatus,f"{path}.status"),profile_fact_references=_typed_references(data.get("profile_fact_references"),f"{path}.profile_fact_references"),reasoning=_text(data.get("reasoning"),f"{path}.reasoning"),provider_confidence=_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review_required=_boolean(data.get("review_required"),f"{path}.review_required"),suggested_follow_up=_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


def _constraint_from_provider(value: Any, *, index: int, profile: CareerProfile) -> ConstraintCompatibilityCandidate:
    path=f"mapping_candidates.constraints[{index}]"; data=_mapping(value,path)
    _reject_unknown(data,{"role_id","status","profile_fact_references","reasoning","provider_confidence","review_required","unknown_constraint_ids","suggested_follow_up"},path)
    return ConstraintCompatibilityCandidate(role_id=_stable_id(data.get("role_id"),f"{path}.role_id"),status=_enum(data.get("status"),MappingConstraintStatus,f"{path}.status"),profile_fact_references=_provider_references(data.get("profile_fact_references"),path=f"{path}.profile_fact_references",profile=profile),reasoning=_text(data.get("reasoning"),f"{path}.reasoning"),provider_confidence=_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review_required=_boolean(data.get("review_required"),f"{path}.review_required"),unknown_constraint_ids=_string_tuple(data.get("unknown_constraint_ids"),f"{path}.unknown_constraint_ids",ids=True),suggested_follow_up=_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


def _constraint_from_dict(value: Any, index: int) -> ConstraintCompatibilityCandidate:
    path=f"mapping_candidates.constraints[{index}]"; data=_mapping(value,path)
    _reject_unknown(data,{"role_id","status","profile_fact_references","reasoning","provider_confidence","review_required","unknown_constraint_ids","suggested_follow_up"},path)
    return ConstraintCompatibilityCandidate(role_id=_stable_id(data.get("role_id"),f"{path}.role_id"),status=_enum(data.get("status"),MappingConstraintStatus,f"{path}.status"),profile_fact_references=_typed_references(data.get("profile_fact_references"),f"{path}.profile_fact_references"),reasoning=_text(data.get("reasoning"),f"{path}.reasoning"),provider_confidence=_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review_required=_boolean(data.get("review_required"),f"{path}.review_required"),unknown_constraint_ids=_string_tuple(data.get("unknown_constraint_ids"),f"{path}.unknown_constraint_ids",ids=True),suggested_follow_up=_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


PROVIDER_MAPPING_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "mappings": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "role_id": {"type":"string"}, "dimension_id":{"type":"string"}, "match_status":{"type":"string","enum":[x.value for x in CurrentMatchStatus]},
            "profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"path":{"type":"string"},"value_snapshot":{}},"required":["path","value_snapshot"]}},
            "reasoning":{"type":"string"},"inference_type":{"type":"string","enum":[x.value for x in InferenceType]},"evidence_strength":{"type":"string","enum":[x.value for x in EvidenceStrength]},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"relationship":{"type":"string","enum":[x.value for x in ContributionRelationship]},"suggested_follow_up":{"type":["string","null"]}},
            "required":["role_id","dimension_id","match_status","profile_fact_references","reasoning","inference_type","evidence_strength","provider_confidence","review_required","relationship","suggested_follow_up"]}},
        "directional_signals":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"role_id":{"type":"string"},"signal_type":{"type":"string","enum":[x.value for x in DirectionalSignalType]},"status":{"type":"string","enum":[x.value for x in DirectionalStatus]},"profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"path":{"type":"string"},"value_snapshot":{}},"required":["path","value_snapshot"]}},"reasoning":{"type":"string"},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"suggested_follow_up":{"type":["string","null"]}},"required":["role_id","signal_type","status","profile_fact_references","reasoning","provider_confidence","review_required","suggested_follow_up"]}},
        "constraints":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"role_id":{"type":"string"},"status":{"type":"string","enum":[x.value for x in MappingConstraintStatus]},"profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"path":{"type":"string"},"value_snapshot":{}},"required":["path","value_snapshot"]}},"reasoning":{"type":"string"},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"unknown_constraint_ids":{"type":"array","items":{"type":"string"}},"suggested_follow_up":{"type":["string","null"]}},"required":["role_id","status","profile_fact_references","reasoning","provider_confidence","review_required","unknown_constraint_ids","suggested_follow_up"]}},
        "conflict_warnings":{"type":"array","items":{"type":"string"}},
    },
    "required":["mappings","directional_signals","constraints","conflict_warnings"],
}


MAPPING_INSTRUCTIONS = """Map only facts present in the supplied CareerProfile to the supplied capability dimensions.
Every assessed mapping or directional/constraint conclusion must cite exact Profile paths and value snapshots.
Preserve unknowns. Interest and career goals may affect directional signals only, never Current Fit.
Education does not prove tool proficiency, and a project title does not prove unstated project content.
Do not calculate scores, bands, ranks, weights, confidence results, decisions, gaps, IDs, fingerprints, artifact status, or review provenance.
Do not add fields outside the JSON Schema. Return one JSON object only."""


class ProfileDimensionMapper(Protocol):
    def map(self, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> ProfileDimensionMappingCandidateSet: ...


class OpenAIProfileDimensionMapper:
    def __init__(self, *, settings: LLMSettings | None = None, client: Any | None = None, max_attempts: int = 2) -> None:
        self.settings = settings or LLMSettings.from_environment()
        self.client = client or create_openai_client(self.settings)
        self.max_attempts = max_attempts

    def map(self, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> ProfileDimensionMappingCandidateSet:
        payload = json.dumps({"career_profile":profile.to_dict(),"rubric":{"rubric_version":rubric.rubric_version,"roles":[{"role_id":role,"dimensions":[{"dimension_id":d.dimension_id,"name":d.name,"description":d.description,"inclusion_criteria":list(d.inclusion_criteria),"exclusion_criteria":list(d.exclusion_criteria)} for d in rubric.role_dimensions(role)]} for role in rubric.supported_role_ids]}},ensure_ascii=False)
        last: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                raw = self._request(payload)
                parsed = parse_provider_output(raw)
                return ProfileDimensionMappingCandidateSet.from_provider_payload(parsed,profile=profile,rubric=rubric,catalog=catalog,provider_name="openai_compatible",provider_model=self.settings.model)
            except (json.JSONDecodeError, TypeError, Phase2ValidationError) as error:
                last = error
        raise LLMRequestError(f"Role mapping provider output was invalid after {self.max_attempts} attempts: {last}")

    def _request(self, payload: str) -> str:
        try:
            if is_bailian_endpoint(self.settings.base_url):
                completion=self.client.chat.completions.create(model=self.settings.model,messages=[{"role":"system","content":MAPPING_INSTRUCTIONS},{"role":"user","content":payload}],response_format={"type":"json_schema","json_schema":{"name":"profile_dimension_mapping_candidates","strict":True,"schema":PROVIDER_MAPPING_SCHEMA}},extra_body={"enable_thinking":False})
                return completion.choices[0].message.content
            response=self.client.responses.create(model=self.settings.model,instructions=MAPPING_INSTRUCTIONS,input=payload,text={"format":{"type":"json_schema","name":"profile_dimension_mapping_candidates","strict":True,"schema":PROVIDER_MAPPING_SCHEMA}},store=False)
            return response.output_text
        except Exception as error:
            raise safe_llm_error(error) from error

    @property
    def base_url_host(self) -> str:
        return urlparse(self.settings.base_url).hostname if self.settings.base_url else "default OpenAI endpoint"


def save_mapping_candidates(
    value: ProfileDimensionMappingCandidateSet,
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
) -> Path:
    if not isinstance(value, ProfileDimensionMappingCandidateSet):
        raise TypeError("value must be a ProfileDimensionMappingCandidateSet")
    value.validate(profile=profile, rubric=rubric, catalog=catalog)
    return save_phase2_json(ProfileDimensionMappingCandidateSet.from_dict(value.to_dict()).to_dict(), path)


def load_mapping_candidates(
    path: str | Path,
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
) -> ProfileDimensionMappingCandidateSet:
    value = ProfileDimensionMappingCandidateSet.from_dict(load_phase2_json(path))
    value.validate(profile=profile, rubric=rubric, catalog=catalog)
    return value
