"""Deterministic, read-only-source Evidence Bank contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .capability_rubric import CapabilityRubric, EvidenceClass, capability_rubric_fingerprint
from .career_direction import UserRoleDecision, profile_fingerprint
from .career_gap_analysis import (
    CareerGapAnalysis,
    CareerGapDimensionItem,
    GapClassification,
    GapPriority,
    RoleSelectionType,
)
from .evidence_binding_review import (
    BindingReviewDecision,
    EvidenceBindingReviewArtifact,
)
from .evidence_group_allocation_review import EvidenceGroupAllocationReviewArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import (
    AllocatedProfileCriterionEvidenceBinding,
    ContributionRelationship,
    EvidenceTrustLevel,
    ProfileDimensionMappingCandidateSet,
    generate_mapping_artifact_id,
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
from .role_recommendation import RoleRecommendationArtifact


EVIDENCE_BANK_SCHEMA = "aarvia.evidence_bank"
EVIDENCE_BANK_SCHEMA_VERSION = 1
EVIDENCE_BANK_ID_VERSION = "evidence-bank-v1"


class EvidenceSourceRecordType(str, Enum):
    EDUCATION = "education"
    EXPERIENCE = "experience"
    PROJECT = "project"
    SKILL = "skill"


class FactConfirmationState(str, Enum):
    CONFIRMED_PROFILE_FACT = "confirmed_profile_fact"


class ResumeEvidenceUse(str, Enum):
    CONFIRMED_CAPABILITY_EVIDENCE = "confirmed_capability_evidence"
    CONFIRMED_FACTUAL_EVIDENCE = "confirmed_factual_evidence"
    STRUCTURAL_INFORMATION = "structural_information"
    NOT_ELIGIBLE = "not_eligible"


class ResumeEligibilityReason(str, Enum):
    CONFIRMED_CAPABILITY_SUPPORT = "confirmed_capability_support"
    CONFIRMED_EXACT_PROFILE_FACT = "confirmed_exact_profile_fact"
    STRUCTURAL_INFORMATION_ONLY = "structural_information_only"
    PROVISIONAL_SEMANTIC_UNCONFIRMED = "provisional_semantic_unconfirmed"


class EffectiveBindingStatus(str, Enum):
    EFFECTIVE = "effective"


class EvidenceLinkReviewState(str, Enum):
    CONFIRMED = "confirmed"
    DEFERRED = "deferred"
    REVIEW_REQUIRED = "review_required"
    NOT_REQUIRED = "not_required"


class CapabilitySupportState(str, Enum):
    CONFIRMED_CAPABILITY_SUPPORT = "confirmed_capability_support"
    DEVELOPING_CAPABILITY_SUPPORT = "developing_capability_support"
    TRANSFERABLE_FOUNDATION_SUPPORT = "transferable_foundation_support"
    STRUCTURAL_FACT_ONLY = "structural_fact_only"
    PROVISIONAL_SUPPORT = "provisional_support"


class EvidenceNeedType(str, Enum):
    CONFIRM_UNKNOWN_EVIDENCE = "confirm_unknown_evidence"
    STRENGTHEN_DEVELOPING_CAPABILITY = "strengthen_developing_capability"
    VALIDATE_TRANSFERABLE_FOUNDATION = "validate_transferable_foundation"
    DEVELOP_CAPABILITY = "develop_capability"


class EvidenceNeedReason(str, Enum):
    UNKNOWN_IS_NOT_A_GAP = "unknown_is_not_a_gap"
    UNCONFIRMED_CRITERION = "unconfirmed_criterion"
    PARTIAL_EVIDENCE_ONLY = "partial_evidence_only"
    TRANSFERABLE_IS_NOT_TARGET_CAPABILITY = "transferable_is_not_target_capability"
    EXPLICIT_NOT_DEMONSTRATED = "explicit_not_demonstrated"


_SOURCE_PATH = re.compile(r"^(education|experience_overview|skills)\[(\d+)\](?:\..+)?$")


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
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
        raise Phase2ValidationError(f"{path} must be a SHA-256 fingerprint")
    return result


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_hash(prefix: str, *values: str) -> str:
    payload = "|".join((prefix, *values)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


@dataclass(frozen=True, eq=True)
class EvidenceSourceRecord:
    source_record_id: str
    record_type: EvidenceSourceRecordType
    canonical_profile_path: str
    profile_fingerprint: str
    identity: tuple[tuple[str, str], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceSourceRecord:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "source_record_id", "record_type", "canonical_profile_path",
                "profile_fingerprint", "identity",
            },
            path,
        )
        raw_identity = _mapping(data.get("identity"), f"{path}.identity")
        identity = tuple(
            sorted(
                (
                    _stable_id(key, f"{path}.identity key"),
                    _text(item, f"{path}.identity.{key}"),
                )
                for key, item in raw_identity.items()
            )
        )
        result = cls(
            source_record_id=_stable_id(
                data.get("source_record_id"), f"{path}.source_record_id"
            ),
            record_type=_enum(
                data.get("record_type"), EvidenceSourceRecordType,
                f"{path}.record_type",
            ),
            canonical_profile_path=_text(
                data.get("canonical_profile_path"),
                f"{path}.canonical_profile_path",
            ),
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), f"{path}.profile_fingerprint"
            ),
            identity=identity,
        )
        if not result.identity:
            raise Phase2ValidationError(f"{path}.identity cannot be empty")
        expected = generate_source_record_id(
            record_type=result.record_type,
            canonical_profile_path=result.canonical_profile_path,
            profile_fingerprint_value=result.profile_fingerprint,
            identity=result.identity,
        )
        if result.source_record_id != expected:
            raise Phase2ValidationError(f"{path}.source_record_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "record_type": self.record_type.value,
            "canonical_profile_path": self.canonical_profile_path,
            "profile_fingerprint": self.profile_fingerprint,
            "identity": {key: value for key, value in self.identity},
        }


@dataclass(frozen=True, eq=True)
class EvidenceItem:
    evidence_item_id: str
    evidence_fingerprint: str
    source_record_id: str
    evidence_class: EvidenceClass
    canonical_profile_path: str
    exact_excerpt: str
    start_offset: int
    end_offset: int
    fact_confirmation_state: FactConfirmationState
    resume_eligible: bool
    resume_use: ResumeEvidenceUse
    resume_eligibility_reason_codes: tuple[ResumeEligibilityReason, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceItem:
        data = _mapping(value, path)
        allowed = {
            "evidence_item_id", "evidence_fingerprint", "source_record_id",
            "evidence_class", "canonical_profile_path", "exact_excerpt",
            "start_offset", "end_offset", "fact_confirmation_state",
            "resume_eligible", "resume_use", "resume_eligibility_reason_codes",
        }
        _reject_unknown(data, allowed, path)
        start, end = data.get("start_offset"), data.get("end_offset")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in (start, end)):
            raise Phase2ValidationError(f"{path} offsets must be integers")
        eligible = data.get("resume_eligible")
        if not isinstance(eligible, bool):
            raise Phase2ValidationError(f"{path}.resume_eligible must be boolean")
        reasons = tuple(
            _enum(item, ResumeEligibilityReason, f"{path}.resume_eligibility_reason_codes[{index}]")
            for index, item in enumerate(
                _string_tuple(
                    data.get("resume_eligibility_reason_codes"),
                    f"{path}.resume_eligibility_reason_codes",
                )
            )
        )
        result = cls(
            evidence_item_id=_stable_id(
                data.get("evidence_item_id"), f"{path}.evidence_item_id"
            ),
            evidence_fingerprint=_fingerprint(
                data.get("evidence_fingerprint"), f"{path}.evidence_fingerprint"
            ),
            source_record_id=_stable_id(
                data.get("source_record_id"), f"{path}.source_record_id"
            ),
            evidence_class=_enum(
                data.get("evidence_class"), EvidenceClass, f"{path}.evidence_class"
            ),
            canonical_profile_path=_text(
                data.get("canonical_profile_path"), f"{path}.canonical_profile_path"
            ),
            exact_excerpt=_text(data.get("exact_excerpt"), f"{path}.exact_excerpt"),
            start_offset=start,
            end_offset=end,
            fact_confirmation_state=_enum(
                data.get("fact_confirmation_state"), FactConfirmationState,
                f"{path}.fact_confirmation_state",
            ),
            resume_eligible=eligible,
            resume_use=_enum(
                data.get("resume_use"), ResumeEvidenceUse, f"{path}.resume_use"
            ),
            resume_eligibility_reason_codes=reasons,
        )
        if not reasons or len(reasons) != len(set(reasons)):
            raise Phase2ValidationError(
                f"{path}.resume_eligibility_reason_codes must be unique and non-empty"
            )
        if result.resume_eligible != (result.resume_use != ResumeEvidenceUse.NOT_ELIGIBLE):
            raise Phase2ValidationError(f"{path} resume eligibility is inconsistent")
        expected = generate_evidence_item_id(result.evidence_fingerprint)
        if result.evidence_item_id != expected:
            raise Phase2ValidationError(f"{path}.evidence_item_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_item_id": self.evidence_item_id,
            "evidence_fingerprint": self.evidence_fingerprint,
            "source_record_id": self.source_record_id,
            "evidence_class": self.evidence_class.value,
            "canonical_profile_path": self.canonical_profile_path,
            "exact_excerpt": self.exact_excerpt,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "fact_confirmation_state": self.fact_confirmation_state.value,
            "resume_eligible": self.resume_eligible,
            "resume_use": self.resume_use.value,
            "resume_eligibility_reason_codes": [
                item.value for item in self.resume_eligibility_reason_codes
            ],
        }


@dataclass(frozen=True, eq=True)
class CapabilityEvidenceLink:
    link_id: str
    evidence_item_id: str
    binding_id: str
    role_id: str
    dimension_id: str
    criterion_id: str
    gap_classification: GapClassification
    binding_status: EffectiveBindingStatus
    review_state: EvidenceLinkReviewState
    trust_level: EvidenceTrustLevel
    support_state: CapabilitySupportState
    contribution_relationship: ContributionRelationship
    contribution_weight: float

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> CapabilityEvidenceLink:
        data = _mapping(value, path)
        allowed = {
            "link_id", "evidence_item_id", "binding_id", "role_id",
            "dimension_id", "criterion_id", "gap_classification",
            "binding_status", "review_state", "trust_level", "support_state",
            "contribution_relationship", "contribution_weight",
        }
        _reject_unknown(data, allowed, path)
        weight = data.get("contribution_weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise Phase2ValidationError(f"{path}.contribution_weight must be numeric")
        result = cls(
            link_id=_stable_id(data.get("link_id"), f"{path}.link_id"),
            evidence_item_id=_stable_id(
                data.get("evidence_item_id"), f"{path}.evidence_item_id"
            ),
            binding_id=_stable_id(data.get("binding_id"), f"{path}.binding_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            dimension_id=_stable_id(
                data.get("dimension_id"), f"{path}.dimension_id"
            ),
            criterion_id=_stable_id(
                data.get("criterion_id"), f"{path}.criterion_id"
            ),
            gap_classification=_enum(
                data.get("gap_classification"), GapClassification,
                f"{path}.gap_classification",
            ),
            binding_status=_enum(
                data.get("binding_status"), EffectiveBindingStatus,
                f"{path}.binding_status",
            ),
            review_state=_enum(
                data.get("review_state"), EvidenceLinkReviewState,
                f"{path}.review_state",
            ),
            trust_level=_enum(
                data.get("trust_level"), EvidenceTrustLevel, f"{path}.trust_level"
            ),
            support_state=_enum(
                data.get("support_state"), CapabilitySupportState,
                f"{path}.support_state",
            ),
            contribution_relationship=_enum(
                data.get("contribution_relationship"), ContributionRelationship,
                f"{path}.contribution_relationship",
            ),
            contribution_weight=float(weight),
        )
        if result.review_state == EvidenceLinkReviewState.CONFIRMED and (
            result.trust_level != EvidenceTrustLevel.PROVISIONAL_SEMANTIC
        ):
            raise Phase2ValidationError(
                f"{path} structural evidence cannot be semantically confirmed"
            )
        expected = generate_capability_link_id(result)
        if result.link_id != expected:
            raise Phase2ValidationError(f"{path}.link_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "evidence_item_id": self.evidence_item_id,
            "binding_id": self.binding_id,
            "role_id": self.role_id,
            "dimension_id": self.dimension_id,
            "criterion_id": self.criterion_id,
            "gap_classification": self.gap_classification.value,
            "binding_status": self.binding_status.value,
            "review_state": self.review_state.value,
            "trust_level": self.trust_level.value,
            "support_state": self.support_state.value,
            "contribution_relationship": self.contribution_relationship.value,
            "contribution_weight": self.contribution_weight,
        }


@dataclass(frozen=True, eq=True)
class EvidenceNeed:
    need_id: str
    role_id: str
    selection_type: RoleSelectionType
    dimension_id: str
    criterion_id: str | None
    need_type: EvidenceNeedType
    priority: GapPriority
    reason_codes: tuple[str, ...]
    prompt: str
    is_profile_fact: bool = False
    resume_eligible: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceNeed:
        data = _mapping(value, path)
        allowed = {
            "need_id", "role_id", "selection_type", "dimension_id",
            "criterion_id", "need_type", "priority", "reason_codes", "prompt",
            "is_profile_fact", "resume_eligible",
        }
        _reject_unknown(data, allowed, path)
        criterion = data.get("criterion_id")
        is_fact, eligible = data.get("is_profile_fact"), data.get("resume_eligible")
        if is_fact is not False or eligible is not False:
            raise Phase2ValidationError(
                f"{path} must remain a non-fact, non-resume evidence need"
            )
        result = cls(
            need_id=_stable_id(data.get("need_id"), f"{path}.need_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            selection_type=_enum(
                data.get("selection_type"), RoleSelectionType,
                f"{path}.selection_type",
            ),
            dimension_id=_stable_id(
                data.get("dimension_id"), f"{path}.dimension_id"
            ),
            criterion_id=(
                None if criterion is None
                else _stable_id(criterion, f"{path}.criterion_id")
            ),
            need_type=_enum(
                data.get("need_type"), EvidenceNeedType, f"{path}.need_type"
            ),
            priority=_enum(data.get("priority"), GapPriority, f"{path}.priority"),
            reason_codes=_string_tuple(
                data.get("reason_codes"), f"{path}.reason_codes", ids=True
            ),
            prompt=_text(data.get("prompt"), f"{path}.prompt"),
        )
        if not result.reason_codes:
            raise Phase2ValidationError(f"{path}.reason_codes cannot be empty")
        expected = generate_evidence_need_id(
            role_id=result.role_id,
            selection_type=result.selection_type,
            dimension_id=result.dimension_id,
            criterion_id=result.criterion_id,
            need_type=result.need_type,
        )
        if result.need_id != expected:
            raise Phase2ValidationError(f"{path}.need_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "role_id": self.role_id,
            "selection_type": self.selection_type.value,
            "dimension_id": self.dimension_id,
            "criterion_id": self.criterion_id,
            "need_type": self.need_type.value,
            "priority": self.priority.value,
            "reason_codes": list(self.reason_codes),
            "prompt": self.prompt,
            "is_profile_fact": False,
            "resume_eligible": False,
        }


@dataclass(frozen=True, eq=True)
class EvidenceBankSummary:
    source_count: int
    item_count: int
    link_count: int
    confirmed_profile_fact_count: int
    resume_eligible_item_count: int
    structural_information_item_count: int
    confirmed_capability_support_count: int
    developing_support_count: int
    transferable_support_count: int
    unknown_evidence_need_count: int
    explicit_development_need_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> EvidenceBankSummary:
        data = _mapping(value, path)
        names = {
            "source_count", "item_count", "link_count",
            "confirmed_profile_fact_count", "resume_eligible_item_count",
            "structural_information_item_count",
            "confirmed_capability_support_count", "developing_support_count",
            "transferable_support_count", "unknown_evidence_need_count",
            "explicit_development_need_count",
        }
        _reject_unknown(data, names, path)
        return cls(**{name: _count(data.get(name), f"{path}.{name}") for name in names})

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class EvidenceBank:
    evidence_bank_id: str
    profile_fingerprint: str
    mapping_artifact_id: str
    binding_review_artifact_id: str | None
    allocation_review_artifact_id: str | None
    recommendation_set_id: str
    recommendation_schema_version: int
    decision_id: str
    decision_schema_version: int
    gap_analysis_id: str
    gap_analysis_schema_version: int
    rubric_version: str
    rubric_fingerprint: str
    catalog_version: str
    supersedes_evidence_bank_id: str | None
    created_at: str
    updated_at: str
    sources: tuple[EvidenceSourceRecord, ...]
    items: tuple[EvidenceItem, ...]
    links: tuple[CapabilityEvidenceLink, ...]
    needs: tuple[EvidenceNeed, ...]
    summary: EvidenceBankSummary
    schema: str = EVIDENCE_BANK_SCHEMA
    schema_version: int = EVIDENCE_BANK_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceBank:
        data = _mapping(value, "evidence_bank")
        allowed = {
            "schema", "schema_version", "evidence_bank_id", "profile_fingerprint",
            "mapping_artifact_id", "binding_review_artifact_id",
            "allocation_review_artifact_id", "recommendation_set_id",
            "recommendation_schema_version", "decision_id",
            "decision_schema_version", "gap_analysis_id",
            "gap_analysis_schema_version", "rubric_version", "rubric_fingerprint",
            "catalog_version", "supersedes_evidence_bank_id", "created_at",
            "updated_at", "sources", "items", "links", "needs", "summary",
        }
        _reject_unknown(data, allowed, "evidence_bank")
        if (
            data.get("schema") != EVIDENCE_BANK_SCHEMA
            or data.get("schema_version") != EVIDENCE_BANK_SCHEMA_VERSION
        ):
            raise Phase2ValidationError("unsupported Evidence Bank schema")
        result = cls(
            evidence_bank_id=_stable_id(
                data.get("evidence_bank_id"), "evidence_bank.evidence_bank_id"
            ),
            profile_fingerprint=_fingerprint(
                data.get("profile_fingerprint"), "evidence_bank.profile_fingerprint"
            ),
            mapping_artifact_id=_stable_id(
                data.get("mapping_artifact_id"), "evidence_bank.mapping_artifact_id"
            ),
            binding_review_artifact_id=_optional_id(
                data.get("binding_review_artifact_id"),
                "evidence_bank.binding_review_artifact_id",
            ),
            allocation_review_artifact_id=_optional_id(
                data.get("allocation_review_artifact_id"),
                "evidence_bank.allocation_review_artifact_id",
            ),
            recommendation_set_id=_stable_id(
                data.get("recommendation_set_id"),
                "evidence_bank.recommendation_set_id",
            ),
            recommendation_schema_version=_schema_version(
                data.get("recommendation_schema_version"),
                "evidence_bank.recommendation_schema_version", 7,
            ),
            decision_id=_stable_id(data.get("decision_id"), "evidence_bank.decision_id"),
            decision_schema_version=_schema_version(
                data.get("decision_schema_version"),
                "evidence_bank.decision_schema_version", 2,
            ),
            gap_analysis_id=_stable_id(
                data.get("gap_analysis_id"), "evidence_bank.gap_analysis_id"
            ),
            gap_analysis_schema_version=_schema_version(
                data.get("gap_analysis_schema_version"),
                "evidence_bank.gap_analysis_schema_version", 2,
            ),
            rubric_version=_version(
                data.get("rubric_version"), "evidence_bank.rubric_version"
            ),
            rubric_fingerprint=_fingerprint(
                data.get("rubric_fingerprint"), "evidence_bank.rubric_fingerprint"
            ),
            catalog_version=_version(
                data.get("catalog_version"), "evidence_bank.catalog_version"
            ),
            supersedes_evidence_bank_id=_optional_id(
                data.get("supersedes_evidence_bank_id"),
                "evidence_bank.supersedes_evidence_bank_id",
            ),
            created_at=_iso_datetime(data.get("created_at"), "evidence_bank.created_at"),
            updated_at=_iso_datetime(data.get("updated_at"), "evidence_bank.updated_at"),
            sources=tuple(
                EvidenceSourceRecord.from_dict(item, f"evidence_bank.sources[{index}]")
                for index, item in enumerate(_list(data.get("sources"), "evidence_bank.sources"))
            ),
            items=tuple(
                EvidenceItem.from_dict(item, f"evidence_bank.items[{index}]")
                for index, item in enumerate(_list(data.get("items"), "evidence_bank.items"))
            ),
            links=tuple(
                CapabilityEvidenceLink.from_dict(item, f"evidence_bank.links[{index}]")
                for index, item in enumerate(_list(data.get("links"), "evidence_bank.links"))
            ),
            needs=tuple(
                EvidenceNeed.from_dict(item, f"evidence_bank.needs[{index}]")
                for index, item in enumerate(_list(data.get("needs"), "evidence_bank.needs"))
            ),
            summary=EvidenceBankSummary.from_dict(
                data.get("summary"), "evidence_bank.summary"
            ),
        )
        result._validate_shape()
        if result.evidence_bank_id != generate_evidence_bank_id(result):
            raise Phase2ValidationError("Evidence Bank ID is not deterministic")
        return result

    def _validate_shape(self) -> None:
        if _parsed_datetime(self.updated_at) < _parsed_datetime(self.created_at):
            raise Phase2ValidationError("Evidence Bank updated_at cannot precede created_at")
        for name, values, key in (
            ("sources", self.sources, lambda item: item.source_record_id),
            ("items", self.items, lambda item: item.evidence_item_id),
            ("links", self.links, lambda item: item.link_id),
            ("needs", self.needs, lambda item: item.need_id),
        ):
            ids = tuple(key(item) for item in values)
            if ids != tuple(sorted(set(ids))):
                raise Phase2ValidationError(
                    f"Evidence Bank {name} must be unique and deterministically sorted"
                )
        fingerprints = tuple(item.evidence_fingerprint for item in self.items)
        if len(fingerprints) != len(set(fingerprints)):
            raise Phase2ValidationError(
                "Evidence Bank allows one Evidence Item per evidence fingerprint"
            )
        source_ids = {item.source_record_id for item in self.sources}
        item_ids = {item.evidence_item_id for item in self.items}
        if any(item.source_record_id not in source_ids for item in self.items):
            raise Phase2ValidationError("Evidence Item references an unknown source record")
        if any(item.evidence_item_id not in item_ids for item in self.links):
            raise Phase2ValidationError("Capability link references an unknown Evidence Item")
        if self.summary != _summary(self.items, self.links, self.needs, self.sources):
            raise Phase2ValidationError("Evidence Bank summary does not match its contents")

    def validate(
        self,
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
        catalog: RoleCatalog,
        mapping: ProfileDimensionMappingCandidateSet,
        recommendation: RoleRecommendationArtifact,
        decision: UserRoleDecision,
        gap_analysis: CareerGapAnalysis,
        evidence_reviews: EvidenceBindingReviewArtifact | None = None,
        allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
        superseded_bank: EvidenceBank | None = None,
        superseded_decision: UserRoleDecision | None = None,
    ) -> None:
        _validate_context(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision,
            gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
            allocation_reviews=allocation_reviews,
            superseded_decision=superseded_decision,
        )
        _validate_revision(self, superseded_bank)
        expected = _build_evidence_bank(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision,
            gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
            allocation_reviews=allocation_reviews,
            created_at=self.created_at, updated_at=self.updated_at,
            supersedes_evidence_bank_id=self.supersedes_evidence_bank_id,
        )
        if self != expected:
            raise Phase2ValidationError(
                "Evidence Bank does not match deterministic source artifacts"
            )
        if superseded_bank is not None:
            _validate_bank_contents_against_current_context(
                superseded_bank,
                profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
                recommendation=recommendation, decision=decision,
                gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
                allocation_reviews=allocation_reviews,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "evidence_bank_id": self.evidence_bank_id,
            "profile_fingerprint": self.profile_fingerprint,
            "mapping_artifact_id": self.mapping_artifact_id,
            "binding_review_artifact_id": self.binding_review_artifact_id,
            "allocation_review_artifact_id": self.allocation_review_artifact_id,
            "recommendation_set_id": self.recommendation_set_id,
            "recommendation_schema_version": self.recommendation_schema_version,
            "decision_id": self.decision_id,
            "decision_schema_version": self.decision_schema_version,
            "gap_analysis_id": self.gap_analysis_id,
            "gap_analysis_schema_version": self.gap_analysis_schema_version,
            "rubric_version": self.rubric_version,
            "rubric_fingerprint": self.rubric_fingerprint,
            "catalog_version": self.catalog_version,
            "supersedes_evidence_bank_id": self.supersedes_evidence_bank_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources": [item.to_dict() for item in self.sources],
            "items": [item.to_dict() for item in self.items],
            "links": [item.to_dict() for item in self.links],
            "needs": [item.to_dict() for item in self.needs],
            "summary": self.summary.to_dict(),
        }


def _schema_version(value: Any, path: str, expected: int) -> int:
    if value != expected:
        raise Phase2ValidationError(f"{path} must equal {expected}")
    return expected


def generate_source_record_id(
    *, record_type: EvidenceSourceRecordType, canonical_profile_path: str,
    profile_fingerprint_value: str, identity: tuple[tuple[str, str], ...],
) -> str:
    payload = json.dumps(dict(identity), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "source_record_" + _stable_hash(
        "evidence-source-record-v1", record_type.value, canonical_profile_path,
        profile_fingerprint_value, payload,
    )


def generate_evidence_item_id(evidence_fingerprint: str) -> str:
    return "evidence_item_" + _stable_hash(
        "evidence-item-v1", _fingerprint(evidence_fingerprint, "evidence_fingerprint")
    )


def generate_capability_link_id(value: CapabilityEvidenceLink) -> str:
    return "evidence_link_" + _stable_hash(
        "capability-evidence-link-v1", value.evidence_item_id, value.binding_id,
        value.role_id, value.dimension_id, value.criterion_id,
        value.gap_classification.value, value.binding_status.value,
        value.review_state.value, value.trust_level.value, value.support_state.value,
        value.contribution_relationship.value, str(value.contribution_weight),
    )


def generate_evidence_need_id(
    *, role_id: str, selection_type: RoleSelectionType, dimension_id: str,
    criterion_id: str | None, need_type: EvidenceNeedType,
) -> str:
    return "evidence_need_" + _stable_hash(
        "evidence-need-v1", role_id, selection_type.value, dimension_id,
        criterion_id or "dimension_level", need_type.value,
    )


def generate_evidence_bank_id(value: EvidenceBank) -> str:
    if not isinstance(value, EvidenceBank):
        raise TypeError("value must be an EvidenceBank")
    payload = value.to_dict()
    payload.pop("evidence_bank_id", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "evidence_bank_" + hashlib.sha256(
        EVIDENCE_BANK_ID_VERSION.encode("utf-8") + b"|" + encoded
    ).hexdigest()[:24]


def _source_record(
    profile: CareerProfile,
    binding: AllocatedProfileCriterionEvidenceBinding,
) -> EvidenceSourceRecord:
    path = binding.atomic_evidence.profile_reference.path
    match = _SOURCE_PATH.fullmatch(path)
    if match is None:
        raise Phase2ValidationError("Evidence Bank binding uses an unsupported Profile record")
    root, raw_index = match.groups()
    index = int(raw_index)
    parent_path = f"{root}[{index}]"
    if root == "skills":
        record_type = EvidenceSourceRecordType.SKILL
        record = profile.skills[index]
        identity_values = {
            "skill_name": record.skill_name,
            "category": record.category,
        }
    elif root == "education":
        record_type = EvidenceSourceRecordType.EDUCATION
        record = profile.education[index]
        identity_values = {
            "institution": record.institution,
            "degree": record.degree,
            "field_of_study": record.field_of_study,
            "start_date": record.start_date,
            "expected_graduation_date": record.expected_graduation_date,
        }
    else:
        record = profile.experience_overview[index]
        record_type = (
            EvidenceSourceRecordType.PROJECT
            if binding.evidence_class == EvidenceClass.PROJECT_SUMMARY
            else EvidenceSourceRecordType.EXPERIENCE
        )
        identity_values = {
            "experience_type": record.experience_type,
            "organization_or_project_name": record.organization_or_project_name,
            "title_or_role": record.title_or_role,
            "start_date": record.start_date,
            "end_date": record.end_date,
        }
    identity = tuple(
        sorted((key, value) for key, value in identity_values.items() if value is not None)
    )
    fingerprint = profile_fingerprint(profile)
    source_id = generate_source_record_id(
        record_type=record_type, canonical_profile_path=parent_path,
        profile_fingerprint_value=fingerprint, identity=identity,
    )
    return EvidenceSourceRecord(source_id, record_type, parent_path, fingerprint, identity)


def _review_state(
    binding: AllocatedProfileCriterionEvidenceBinding,
    review_decisions: Mapping[str, BindingReviewDecision],
) -> EvidenceLinkReviewState:
    if binding.trust_level == EvidenceTrustLevel.STRUCTURAL_ONLY:
        return EvidenceLinkReviewState.NOT_REQUIRED
    decision = review_decisions.get(binding.binding_id)
    if decision == BindingReviewDecision.REJECTED:
        raise Phase2ValidationError("rejected binding cannot enter Evidence Bank")
    if decision == BindingReviewDecision.CONFIRMED:
        return EvidenceLinkReviewState.CONFIRMED
    if decision == BindingReviewDecision.DEFERRED:
        return EvidenceLinkReviewState.DEFERRED
    return EvidenceLinkReviewState.REVIEW_REQUIRED


def _support_state(
    classification: GapClassification,
    binding: AllocatedProfileCriterionEvidenceBinding,
    review_state: EvidenceLinkReviewState,
) -> CapabilitySupportState:
    if binding.trust_level == EvidenceTrustLevel.STRUCTURAL_ONLY:
        return CapabilitySupportState.STRUCTURAL_FACT_ONLY
    if classification == GapClassification.CONFIRMED_STRENGTH:
        if (
            binding.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
            and review_state == EvidenceLinkReviewState.CONFIRMED
        ):
            return CapabilitySupportState.CONFIRMED_CAPABILITY_SUPPORT
        raise Phase2ValidationError(
            "confirmed strength cannot rely on unconfirmed semantic evidence"
        )
    if classification == GapClassification.DEVELOPING_CAPABILITY:
        return CapabilitySupportState.DEVELOPING_CAPABILITY_SUPPORT
    if classification == GapClassification.TRANSFERABLE_FOUNDATION:
        return CapabilitySupportState.TRANSFERABLE_FOUNDATION_SUPPORT
    return CapabilitySupportState.PROVISIONAL_SUPPORT


def _need_type(classification: GapClassification) -> EvidenceNeedType | None:
    return {
        GapClassification.UNKNOWN_EVIDENCE: EvidenceNeedType.CONFIRM_UNKNOWN_EVIDENCE,
        GapClassification.DEVELOPING_CAPABILITY: EvidenceNeedType.STRENGTHEN_DEVELOPING_CAPABILITY,
        GapClassification.TRANSFERABLE_FOUNDATION: EvidenceNeedType.VALIDATE_TRANSFERABLE_FOUNDATION,
        GapClassification.CAPABILITY_GAP: EvidenceNeedType.DEVELOP_CAPABILITY,
        GapClassification.CONFIRMED_STRENGTH: None,
        GapClassification.NOT_APPLICABLE: None,
    }[classification]


def _need_reason(classification: GapClassification) -> EvidenceNeedReason:
    return {
        GapClassification.UNKNOWN_EVIDENCE: EvidenceNeedReason.UNKNOWN_IS_NOT_A_GAP,
        GapClassification.DEVELOPING_CAPABILITY: EvidenceNeedReason.PARTIAL_EVIDENCE_ONLY,
        GapClassification.TRANSFERABLE_FOUNDATION: EvidenceNeedReason.TRANSFERABLE_IS_NOT_TARGET_CAPABILITY,
        GapClassification.CAPABILITY_GAP: EvidenceNeedReason.EXPLICIT_NOT_DEMONSTRATED,
    }[classification]


def _need_prompt(
    *, dimension_name: str, criterion_text: str | None,
    need_type: EvidenceNeedType,
) -> str:
    subject = dimension_name if criterion_text is None else f"{dimension_name}: {criterion_text}"
    return {
        EvidenceNeedType.CONFIRM_UNKNOWN_EVIDENCE: (
            f"Confirm whether existing evidence supports {subject}; no evidence is not treated as a gap."
        ),
        EvidenceNeedType.STRENGTHEN_DEVELOPING_CAPABILITY: (
            f"Add or confirm independent evidence that strengthens {subject}."
        ),
        EvidenceNeedType.VALIDATE_TRANSFERABLE_FOUNDATION: (
            f"Confirm where the transferable foundation has been applied to {subject}."
        ),
        EvidenceNeedType.DEVELOP_CAPABILITY: (
            f"Develop and document evidence for {subject}."
        ),
    }[need_type]


def _summary(
    items: tuple[EvidenceItem, ...], links: tuple[CapabilityEvidenceLink, ...],
    needs: tuple[EvidenceNeed, ...], sources: tuple[EvidenceSourceRecord, ...],
) -> EvidenceBankSummary:
    return EvidenceBankSummary(
        source_count=len(sources),
        item_count=len(items),
        link_count=len(links),
        confirmed_profile_fact_count=len(items),
        resume_eligible_item_count=sum(item.resume_eligible for item in items),
        structural_information_item_count=sum(
            item.resume_use == ResumeEvidenceUse.STRUCTURAL_INFORMATION for item in items
        ),
        confirmed_capability_support_count=sum(
            link.support_state == CapabilitySupportState.CONFIRMED_CAPABILITY_SUPPORT
            for link in links
        ),
        developing_support_count=sum(
            link.support_state == CapabilitySupportState.DEVELOPING_CAPABILITY_SUPPORT
            for link in links
        ),
        transferable_support_count=sum(
            link.support_state == CapabilitySupportState.TRANSFERABLE_FOUNDATION_SUPPORT
            for link in links
        ),
        unknown_evidence_need_count=sum(
            need.need_type == EvidenceNeedType.CONFIRM_UNKNOWN_EVIDENCE for need in needs
        ),
        explicit_development_need_count=sum(
            need.need_type == EvidenceNeedType.DEVELOP_CAPABILITY for need in needs
        ),
    )


def _validate_context(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None,
    superseded_decision: UserRoleDecision | None,
) -> None:
    if mapping.schema_version != 5 or recommendation.schema_version != 7:
        raise Phase2ValidationError(
            "Evidence Bank schema 1 requires Mapping 5 and Recommendation 7"
        )
    if decision.schema_version != 2 or gap_analysis.schema_version != 2:
        raise Phase2ValidationError(
            "Evidence Bank schema 1 requires Decision 2 and Career Gap Analysis 2"
        )
    gap_analysis.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )


def _build_evidence_bank(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None,
    created_at: str, updated_at: str,
    supersedes_evidence_bank_id: str | None,
) -> EvidenceBank:
    review_decisions = (
        {} if evidence_reviews is None else evidence_reviews.decision_by_binding()
    )
    binding_by_id: dict[str, AllocatedProfileCriterionEvidenceBinding] = {}
    for result in recommendation.role_results:
        for axis in (result.core_current_fit, result.extended_current_fit):
            for assessment in axis.dimension_assessments:
                for binding in assessment.supporting_bindings:
                    if not isinstance(binding, AllocatedProfileCriterionEvidenceBinding):
                        raise Phase2ValidationError(
                            "Evidence Bank requires allocated Mapping 5 bindings"
                        )
                    previous = binding_by_id.setdefault(binding.binding_id, binding)
                    if previous != binding:
                        raise Phase2ValidationError(
                            "Recommendation contains conflicting binding snapshots"
                        )

    sources_by_id: dict[str, EvidenceSourceRecord] = {}
    locator_by_fingerprint: dict[str, tuple[Any, EvidenceClass, str]] = {}
    links: list[CapabilityEvidenceLink] = []
    needs: list[EvidenceNeed] = []
    dimensions = {item.dimension_id: item for item in rubric.dimensions}
    roles = (
        gap_analysis.primary_role_analysis,
        *gap_analysis.secondary_role_analyses,
    )
    for role in roles:
        for gap_item in role.dimensions:
            dimension = dimensions[gap_item.dimension_id]
            if dimension.role_id != role.role_id:
                raise Phase2ValidationError("Gap Dimension belongs to another Role")
            if gap_item.gap_classification not in {
                GapClassification.UNKNOWN_EVIDENCE,
                GapClassification.CAPABILITY_GAP,
                GapClassification.NOT_APPLICABLE,
            }:
                for binding_id in gap_item.supporting_binding_ids:
                    binding = binding_by_id.get(binding_id)
                    if binding is None:
                        raise Phase2ValidationError(
                            "Gap Analysis references a missing Recommendation binding"
                        )
                    if (
                        binding.role_id != role.role_id
                        or binding.dimension_id != gap_item.dimension_id
                    ):
                        raise Phase2ValidationError(
                            "Gap Analysis binding belongs to another Role or Dimension"
                        )
                    source = _source_record(profile, binding)
                    sources_by_id[source.source_record_id] = source
                    fingerprint = binding.atomic_evidence.evidence_fingerprint
                    locator_value = (
                        binding.atomic_evidence,
                        binding.evidence_class,
                        source.source_record_id,
                    )
                    previous_locator = locator_by_fingerprint.setdefault(
                        fingerprint, locator_value
                    )
                    if previous_locator != locator_value:
                        raise Phase2ValidationError(
                            "one evidence fingerprint cannot describe multiple facts"
                        )
                    review_state = _review_state(binding, review_decisions)
                    support_state = _support_state(
                        gap_item.gap_classification, binding, review_state
                    )
                    provisional_link = CapabilityEvidenceLink(
                        link_id="evidence_link_pending",
                        evidence_item_id=generate_evidence_item_id(fingerprint),
                        binding_id=binding.binding_id,
                        role_id=binding.role_id,
                        dimension_id=binding.dimension_id,
                        criterion_id=binding.criterion_id,
                        gap_classification=gap_item.gap_classification,
                        binding_status=EffectiveBindingStatus.EFFECTIVE,
                        review_state=review_state,
                        trust_level=binding.trust_level,
                        support_state=support_state,
                        contribution_relationship=binding.contribution_relationship,
                        contribution_weight=binding.contribution_weight,
                    )
                    links.append(
                        CapabilityEvidenceLink(
                            **{
                                **provisional_link.__dict__,
                                "link_id": generate_capability_link_id(provisional_link),
                            }
                        )
                    )

            need_type = _need_type(gap_item.gap_classification)
            if need_type is not None:
                criterion_ids = gap_item.unresolved_criterion_ids or (None,)
                criteria = {item.criterion_id: item.text for item in dimension.criteria}
                for criterion_id in criterion_ids:
                    reason_codes = tuple(
                        sorted(
                            {
                                *(item.value for item in gap_item.priority_reason_codes),
                                _need_reason(gap_item.gap_classification).value,
                                *(
                                    () if criterion_id is None
                                    else (EvidenceNeedReason.UNCONFIRMED_CRITERION.value,)
                                ),
                            }
                        )
                    )
                    needs.append(
                        EvidenceNeed(
                            need_id=generate_evidence_need_id(
                                role_id=role.role_id,
                                selection_type=role.selection_type,
                                dimension_id=gap_item.dimension_id,
                                criterion_id=criterion_id,
                                need_type=need_type,
                            ),
                            role_id=role.role_id,
                            selection_type=role.selection_type,
                            dimension_id=gap_item.dimension_id,
                            criterion_id=criterion_id,
                            need_type=need_type,
                            priority=gap_item.priority,
                            reason_codes=reason_codes,
                            prompt=_need_prompt(
                                dimension_name=dimension.name,
                                criterion_text=(
                                    None if criterion_id is None
                                    else criteria[criterion_id]
                                ),
                                need_type=need_type,
                            ),
                        )
                    )

    ordered_links = tuple(sorted(links, key=lambda item: item.link_id))
    links_by_item: dict[str, list[CapabilityEvidenceLink]] = {}
    for link in ordered_links:
        links_by_item.setdefault(link.evidence_item_id, []).append(link)
    items: list[EvidenceItem] = []
    structural_classes = {
        EvidenceClass.SKILL_NAME,
        EvidenceClass.SKILL_PROFICIENCY,
        EvidenceClass.EDUCATION_FIELD,
    }
    for fingerprint, (locator, evidence_class, source_id) in locator_by_fingerprint.items():
        item_id = generate_evidence_item_id(fingerprint)
        item_links = links_by_item[item_id]
        if any(
            link.support_state == CapabilitySupportState.CONFIRMED_CAPABILITY_SUPPORT
            for link in item_links
        ):
            resume_use = ResumeEvidenceUse.CONFIRMED_CAPABILITY_EVIDENCE
            reasons = (ResumeEligibilityReason.CONFIRMED_CAPABILITY_SUPPORT,)
        elif any(link.review_state == EvidenceLinkReviewState.CONFIRMED for link in item_links):
            resume_use = ResumeEvidenceUse.CONFIRMED_FACTUAL_EVIDENCE
            reasons = (ResumeEligibilityReason.CONFIRMED_EXACT_PROFILE_FACT,)
        elif evidence_class in structural_classes:
            resume_use = ResumeEvidenceUse.STRUCTURAL_INFORMATION
            reasons = (ResumeEligibilityReason.STRUCTURAL_INFORMATION_ONLY,)
        else:
            resume_use = ResumeEvidenceUse.NOT_ELIGIBLE
            reasons = (ResumeEligibilityReason.PROVISIONAL_SEMANTIC_UNCONFIRMED,)
        items.append(
            EvidenceItem(
                evidence_item_id=item_id,
                evidence_fingerprint=fingerprint,
                source_record_id=source_id,
                evidence_class=evidence_class,
                canonical_profile_path=locator.profile_reference.path,
                exact_excerpt=locator.exact_excerpt,
                start_offset=locator.start_offset,
                end_offset=locator.end_offset,
                fact_confirmation_state=FactConfirmationState.CONFIRMED_PROFILE_FACT,
                resume_eligible=resume_use != ResumeEvidenceUse.NOT_ELIGIBLE,
                resume_use=resume_use,
                resume_eligibility_reason_codes=reasons,
            )
        )
    ordered_sources = tuple(sorted(sources_by_id.values(), key=lambda item: item.source_record_id))
    ordered_items = tuple(sorted(items, key=lambda item: item.evidence_item_id))
    ordered_needs = tuple(sorted(needs, key=lambda item: item.need_id))
    created = _iso_datetime(created_at, "evidence_bank.created_at")
    updated = _iso_datetime(updated_at, "evidence_bank.updated_at")
    provisional = EvidenceBank(
        evidence_bank_id="evidence_bank_pending",
        profile_fingerprint=profile_fingerprint(profile),
        mapping_artifact_id=generate_mapping_artifact_id(mapping),
        binding_review_artifact_id=(
            None if evidence_reviews is None else evidence_reviews.review_artifact_id
        ),
        allocation_review_artifact_id=(
            None if allocation_reviews is None else allocation_reviews.review_artifact_id
        ),
        recommendation_set_id=recommendation.recommendation_set_id,
        recommendation_schema_version=recommendation.schema_version,
        decision_id=decision.decision_id,
        decision_schema_version=decision.schema_version,
        gap_analysis_id=gap_analysis.analysis_id,
        gap_analysis_schema_version=gap_analysis.schema_version,
        rubric_version=rubric.rubric_version,
        rubric_fingerprint=capability_rubric_fingerprint(rubric),
        catalog_version=catalog.catalog_version,
        supersedes_evidence_bank_id=supersedes_evidence_bank_id,
        created_at=created,
        updated_at=updated,
        sources=ordered_sources,
        items=ordered_items,
        links=ordered_links,
        needs=ordered_needs,
        summary=_summary(ordered_items, ordered_links, ordered_needs, ordered_sources),
    )
    return EvidenceBank(
        **{
            **provisional.__dict__,
            "evidence_bank_id": generate_evidence_bank_id(provisional),
        }
    )


def _validate_revision(bank: EvidenceBank, superseded_bank: EvidenceBank | None) -> None:
    if bank.supersedes_evidence_bank_id is None:
        if superseded_bank is not None:
            raise Phase2ValidationError(
                "supplied prior Evidence Bank is not referenced by this revision"
            )
        return
    if superseded_bank is None:
        raise Phase2ValidationError("Evidence Bank revision requires the Bank it supersedes")
    if bank.supersedes_evidence_bank_id != superseded_bank.evidence_bank_id:
        raise Phase2ValidationError("Evidence Bank revision references another prior Bank")
    if bank.evidence_bank_id == superseded_bank.evidence_bank_id:
        raise Phase2ValidationError("Evidence Bank cannot supersede itself")
    if superseded_bank.supersedes_evidence_bank_id == bank.evidence_bank_id:
        raise Phase2ValidationError("Evidence Bank revision lineage contains a cycle")
    if bank.created_at != superseded_bank.created_at:
        raise Phase2ValidationError("Evidence Bank revision must preserve created_at")
    if _parsed_datetime(bank.updated_at) < _parsed_datetime(superseded_bank.updated_at):
        raise Phase2ValidationError("Evidence Bank revision cannot predate its predecessor")


def _validate_bank_contents_against_current_context(
    bank: EvidenceBank,
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None,
) -> None:
    expected = _build_evidence_bank(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, created_at=bank.created_at,
        updated_at=bank.updated_at,
        supersedes_evidence_bank_id=bank.supersedes_evidence_bank_id,
    )
    if bank != expected:
        raise Phase2ValidationError(
            "superseded Evidence Bank does not match complete current provenance"
        )


def build_evidence_bank(
    *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis, created_at: str,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_bank: EvidenceBank | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> EvidenceBank:
    _validate_context(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    if superseded_bank is not None:
        _validate_bank_contents_against_current_context(
            superseded_bank,
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision,
            gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
            allocation_reviews=allocation_reviews,
        )
    result = _build_evidence_bank(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        created_at=(superseded_bank.created_at if superseded_bank else created_at),
        updated_at=created_at,
        supersedes_evidence_bank_id=(
            None if superseded_bank is None else superseded_bank.evidence_bank_id
        ),
    )
    result.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_bank=superseded_bank,
        superseded_decision=superseded_decision,
    )
    return result


def save_evidence_bank(
    value: EvidenceBank, path: str | Path, *, profile: CareerProfile,
    rubric: CapabilityRubric, catalog: RoleCatalog,
    mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_bank: EvidenceBank | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> Path:
    if not isinstance(value, EvidenceBank):
        raise TypeError("value must be an EvidenceBank")
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Evidence Bank artifacts are immutable: {target}")
    value.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_bank=superseded_bank,
        superseded_decision=superseded_decision,
    )
    return save_phase2_json(EvidenceBank.from_dict(value.to_dict()).to_dict(), target)


def load_evidence_bank(
    path: str | Path, *, profile: CareerProfile, rubric: CapabilityRubric,
    catalog: RoleCatalog, mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_bank: EvidenceBank | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> EvidenceBank:
    value = EvidenceBank.from_dict(load_phase2_json(path))
    value.validate(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_bank=superseded_bank,
        superseded_decision=superseded_decision,
    )
    return value


def load_evidence_bank_revision_source(
    path: str | Path, *, profile: CareerProfile, rubric: CapabilityRubric,
    catalog: RoleCatalog, mapping: ProfileDimensionMappingCandidateSet,
    recommendation: RoleRecommendationArtifact, decision: UserRoleDecision,
    gap_analysis: CareerGapAnalysis,
    evidence_reviews: EvidenceBindingReviewArtifact | None = None,
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None,
    superseded_decision: UserRoleDecision | None = None,
) -> EvidenceBank:
    """Load a prior immutable Bank as revision lineage under the current source chain."""
    value = EvidenceBank.from_dict(load_phase2_json(path))
    _validate_context(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    _validate_bank_contents_against_current_context(
        value,
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        gap_analysis=gap_analysis, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    return value


def select_resume_evidence_items(
    value: EvidenceBank, *, achievements_only: bool = False
) -> tuple[EvidenceItem, ...]:
    """Return conservative future Resume inputs without generating Resume text."""
    if not isinstance(value, EvidenceBank):
        raise TypeError("value must be an EvidenceBank")
    allowed = (
        {ResumeEvidenceUse.CONFIRMED_CAPABILITY_EVIDENCE}
        if achievements_only
        else {
            ResumeEvidenceUse.CONFIRMED_CAPABILITY_EVIDENCE,
            ResumeEvidenceUse.CONFIRMED_FACTUAL_EVIDENCE,
            ResumeEvidenceUse.STRUCTURAL_INFORMATION,
        }
    )
    return tuple(item for item in value.items if item.resume_use in allowed)
