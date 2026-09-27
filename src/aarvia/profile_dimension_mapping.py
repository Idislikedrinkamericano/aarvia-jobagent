"""Strict provider-candidate boundary for Profile-to-capability mapping."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from .capability_rubric import (
    CapabilityRubric,
    EvidenceClass,
    EvidenceStatusCap,
)
from .career_direction import ProfileReference, profile_fingerprint
from .llm_client import LLMRequestError, LLMSettings, create_openai_client, is_bailian_endpoint, safe_llm_error
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .provider_diagnostics import ProviderDiagnosticsWriter
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
MAPPING_SCHEMA_VERSION = 3
SUPPORTED_MAPPING_SCHEMA_VERSIONS = frozenset({1, 2, 3})
MAPPING_ID_VERSION = "profile-dimension-mapping-v1"
ATOMIC_MAPPING_ID_VERSION = "profile-dimension-mapping-v2"
ATOMIC_EVIDENCE_VERSION = "atomic-profile-evidence-v1"
CANONICAL_EVIDENCE_SPAN_VERSION = "canonical-profile-evidence-span-v1"
EVIDENCE_BINDING_ID_VERSION = "profile-criterion-evidence-binding-v1"
MAPPING_REJECTION_WARNING_PREFIX = "provider_mapping_rejection:"
MAPPING_REJECTION_SUMMARY_PREFIX = "provider_mapping_rejection_summary:"


@dataclass(frozen=True, eq=True)
class ProviderCapabilities:
    protocol: str
    response_format_mode: str
    allow_prompt_only_fallback: bool
    disable_thinking: bool

    def __post_init__(self) -> None:
        if self.protocol not in {"responses", "chat_completions"}:
            raise ValueError("unsupported Provider protocol")
        if self.response_format_mode not in {"json_schema", "json_object", "prompt_only"}:
            raise ValueError("unsupported Provider response format mode")
        if self.protocol == "responses" and self.response_format_mode != "json_schema":
            raise ValueError("Responses protocol requires json_schema mode")


def provider_capabilities(settings: LLMSettings) -> ProviderCapabilities:
    host = (urlparse(settings.base_url).hostname or "").casefold() if settings.base_url else ""
    if not settings.base_url or host in {"api.openai.com", "www.api.openai.com"}:
        return ProviderCapabilities("responses", "json_schema", False, False)
    return ProviderCapabilities(
        "chat_completions",
        "json_object",
        True,
        is_bailian_endpoint(settings.base_url),
    )


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


CURRENT_FIT_INFERENCE_TYPES = (
    InferenceType.DIRECT,
    InferenceType.BOUNDED_SEMANTIC,
    InferenceType.ADJACENT_TRANSFER,
)


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


class ProposedBindingType(str, Enum):
    DIRECT = "direct"
    BOUNDED_SEMANTIC = "bounded_semantic"
    ADJACENT_TRANSFER = "adjacent_transfer"


class EvidenceTrustLevel(str, Enum):
    STRUCTURAL_ONLY = "structural_only"
    PROVISIONAL_SEMANTIC = "provisional_semantic_binding"


class BindingDerivationReason(str, Enum):
    STRUCTURAL_POLICY_CAP = "structural_policy_cap_applied"
    PROVISIONAL_POLICY_CAP = "provisional_policy_cap_applied"
    PROVIDER_ADJACENT_LIMIT = "provider_adjacent_limit_applied"
    SKILL_PROFICIENCY_PAIRED = "skill_proficiency_paired_with_skill_name"
    CONFIRMED_BINDING_UNAVAILABLE = "confirmed_semantic_binding_unavailable"
    HUMAN_REVIEW_REQUIRED = "provisional_semantic_binding_requires_review"


class _MappingValidationCode(str, Enum):
    INVALID_CURRENT_FIT_STRUCTURE = "invalid_current_fit_structure"
    INVALID_PROFILE_REFERENCE = "invalid_profile_reference"
    PROFILE_VALUE_MISMATCH = "profile_value_mismatch"
    INVALID_EXACT_EXCERPT = "invalid_exact_excerpt"
    INVALID_EVIDENCE_TOKEN_BOUNDARY = "invalid_evidence_token_boundary"
    DUPLICATE_ATOMIC_EVIDENCE = "duplicate_atomic_evidence"
    OVERLAPPING_ATOMIC_EVIDENCE = "overlapping_atomic_evidence"
    CROSS_DIMENSION_EVIDENCE_REUSE = "cross_dimension_evidence_reuse"
    INVALID_MATCH_STATUS = "invalid_match_status"
    INVALID_STATUS_EVIDENCE = "invalid_status_evidence_relationship"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    INVALID_INFERENCE_REVIEW = "invalid_inference_review_relationship"
    INTEREST_CURRENT_FIT = "interest_cannot_support_current_fit"
    INVALID_CONTRIBUTION = "invalid_contribution_relationship"
    INVALID_EVIDENCE_STRENGTH = "invalid_evidence_strength"
    INVALID_PROVIDER_CONFIDENCE = "invalid_provider_confidence"
    INVALID_REVIEW_FLAG = "invalid_review_flag"
    INVALID_MAPPING_PROVENANCE = "invalid_mapping_provenance"
    ROLE_DIMENSION_MISMATCH = "role_dimension_mismatch"
    DUPLICATE_MAPPING = "duplicate_mapping"
    PROVIDER_FIELD_INJECTION = "provider_field_injection"
    UNKNOWN_DIMENSION = "unknown_dimension"
    UNKNOWN_CRITERION = "unknown_criterion"
    CRITERION_DIMENSION_MISMATCH = "criterion_dimension_mismatch"
    UNSUPPORTED_EVIDENCE_CLASS = "unsupported_evidence_class"
    FORBIDDEN_EVIDENCE_CLASS = "forbidden_evidence_class"
    SKILL_PROFICIENCY_WITHOUT_SKILL_NAME = "skill_proficiency_without_skill_name"
    INVALID_BINDING_TYPE = "invalid_binding_type"
    INVALID_BINDING_PROVENANCE = "invalid_binding_provenance"


class _CodedMappingValidationError(Phase2ValidationError):
    """Internal validation failure whose classification is independent of prose."""

    def __init__(self, code: _MappingValidationCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, eq=True)
class AtomicEvidenceLocator:
    """Locally verified, immutable span within one confirmed Profile string fact."""

    profile_reference: ProfileReference
    exact_excerpt: str
    start_offset: int
    end_offset: int
    evidence_fingerprint: str

    @classmethod
    def capture(
        cls, profile: CareerProfile, path: str, exact_excerpt: str
    ) -> AtomicEvidenceLocator:
        reference = ProfileReference.capture(profile, path)
        if not isinstance(reference.value_snapshot, str):
            raise Phase2ValidationError("atomic evidence must reference a Profile string leaf")
        if not isinstance(exact_excerpt, str) or not exact_excerpt or exact_excerpt.strip() != exact_excerpt:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_EXACT_EXCERPT,
                "atomic evidence excerpt must be a non-empty exact string",
            )
        source = reference.value_snapshot
        starts: list[int] = []
        cursor = 0
        while True:
            found = source.find(exact_excerpt, cursor)
            if found < 0:
                break
            starts.append(found)
            cursor = found + 1
        if len(starts) != 1:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_EXACT_EXCERPT,
                "atomic evidence excerpt must occur exactly once in the Profile field",
            )
        start = starts[0]
        end = start + len(exact_excerpt)
        if (
            start > 0
            and source[start - 1].isalnum()
            and exact_excerpt[0].isalnum()
        ) or (
            end < len(source)
            and source[end].isalnum()
            and exact_excerpt[-1].isalnum()
        ):
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_EVIDENCE_TOKEN_BOUNDARY,
                "atomic evidence excerpt must use complete token boundaries",
            )
        return cls.capture_offsets(profile, path, start, end)

    @classmethod
    def capture_offsets(
        cls, profile: CareerProfile, path: str, start: int, end: int
    ) -> AtomicEvidenceLocator:
        reference = ProfileReference.capture(profile, path)
        source = reference.value_snapshot
        if not isinstance(source, str):
            raise Phase2ValidationError("atomic evidence must reference a Profile string leaf")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (start, end)):
            raise Phase2ValidationError("atomic evidence offsets must be integers")
        if start < 0 or end <= start or end > len(source):
            raise Phase2ValidationError("atomic evidence offsets are outside the Profile field")
        exact_excerpt = source[start:end]
        if not exact_excerpt or exact_excerpt.strip() != exact_excerpt:
            raise Phase2ValidationError(
                "atomic evidence offsets must select a non-empty trimmed excerpt"
            )
        fingerprint = _atomic_evidence_fingerprint(
            reference, exact_excerpt, start, end
        )
        return cls(reference, exact_excerpt, start, end, fingerprint)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> AtomicEvidenceLocator:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "profile_reference", "exact_excerpt", "start_offset", "end_offset",
                "evidence_fingerprint",
            },
            path,
        )
        start = data.get("start_offset")
        end = data.get("end_offset")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in (start, end)):
            raise Phase2ValidationError(f"{path} offsets must be integers")
        return cls(
            profile_reference=ProfileReference.from_dict(
                data.get("profile_reference"), f"{path}.profile_reference"
            ),
            exact_excerpt=_text(data.get("exact_excerpt"), f"{path}.exact_excerpt"),
            start_offset=start,
            end_offset=end,
            evidence_fingerprint=_text(
                data.get("evidence_fingerprint"), f"{path}.evidence_fingerprint"
            ),
        )

    def validate(self, profile: CareerProfile) -> None:
        self.profile_reference.validate(profile)
        expected = AtomicEvidenceLocator.capture_offsets(
            profile,
            self.profile_reference.path,
            self.start_offset,
            self.end_offset,
        )
        if self != expected:
            raise Phase2ValidationError("atomic evidence locator does not match the current Profile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_reference": self.profile_reference.to_dict(),
            "exact_excerpt": self.exact_excerpt,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "evidence_fingerprint": self.evidence_fingerprint,
        }


def _atomic_evidence_fingerprint(
    reference: ProfileReference, excerpt: str, start: int, end: int
) -> str:
    payload = json.dumps(
        {
            "version": ATOMIC_EVIDENCE_VERSION,
            "path": reference.path,
            "value_snapshot": reference.value_snapshot,
            "profile_fingerprint": reference.profile_fingerprint,
            "exact_excerpt": excerpt,
            "start_offset": start,
            "end_offset": end,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, eq=True)
class CanonicalEvidenceSpan:
    """Transport-only, locally generated selection boundary for Provider evidence."""

    span_id: str
    path: str
    start_offset: int
    end_offset: int
    text: str

    def materialize(self, profile: CareerProfile) -> AtomicEvidenceLocator:
        expected_id = _canonical_evidence_span_id(
            profile,
            self.path,
            self.start_offset,
            self.end_offset,
        )
        if self.span_id != expected_id:
            raise Phase2ValidationError("canonical evidence span ID is not deterministic")
        locator = AtomicEvidenceLocator.capture_offsets(
            profile, self.path, self.start_offset, self.end_offset
        )
        if locator.exact_excerpt != self.text:
            raise Phase2ValidationError("canonical evidence span text does not match the Profile")
        return locator

    def to_transport_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "path": self.path,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "text": self.text,
        }


def _canonical_evidence_span_id(
    profile: CareerProfile, path: str, start: int, end: int
) -> str:
    reference = ProfileReference.capture(profile, path)
    if not isinstance(reference.value_snapshot, str):
        raise Phase2ValidationError("canonical evidence span requires a string Profile leaf")
    if start < 0 or end <= start or end > len(reference.value_snapshot):
        raise Phase2ValidationError("canonical evidence span offsets are invalid")
    payload = json.dumps(
        {
            "version": CANONICAL_EVIDENCE_SPAN_VERSION,
            "path": path,
            "value_snapshot": reference.value_snapshot,
            "start_offset": start,
            "end_offset": end,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "span_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class MappingCandidateKind(str, Enum):
    CURRENT_FIT = "current_fit_mapping"
    DIRECTIONAL = "directional_signal"
    CONSTRAINT = "constraint_signal"


class MappingRejectionReason(str, Enum):
    INTEREST_CURRENT_FIT = "interest_cannot_support_current_fit"
    INVALID_PROFILE_REFERENCE = "invalid_profile_reference"
    PROFILE_VALUE_MISMATCH = "profile_value_mismatch"
    UNKNOWN_ROLE = "unknown_role"
    UNKNOWN_DIMENSION = "unknown_dimension"
    ROLE_DIMENSION_MISMATCH = "role_dimension_mismatch"
    DUPLICATE_MAPPING = "duplicate_mapping"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    INVALID_DIRECTIONAL = "invalid_directional_signal"
    INVALID_CONSTRAINT = "invalid_constraint_signal"
    PROVIDER_FIELD_INJECTION = "provider_field_injection"
    INVALID_CONTRIBUTION = "invalid_contribution_relationship"
    INVALID_EXACT_EXCERPT = "invalid_exact_excerpt"
    INVALID_EVIDENCE_TOKEN_BOUNDARY = "invalid_evidence_token_boundary"
    DUPLICATE_ATOMIC_EVIDENCE = "duplicate_atomic_evidence"
    OVERLAPPING_ATOMIC_EVIDENCE = "overlapping_atomic_evidence"
    CROSS_DIMENSION_EVIDENCE_REUSE = "cross_dimension_evidence_reuse"
    INVALID_MATCH_STATUS = "invalid_match_status"
    INVALID_STATUS_EVIDENCE = "invalid_status_evidence_relationship"
    INVALID_INFERENCE_REVIEW = "invalid_inference_review_relationship"
    INVALID_CURRENT_FIT_STRUCTURE = "invalid_current_fit_structure"
    INVALID_EVIDENCE_STRENGTH = "invalid_evidence_strength"
    INVALID_PROVIDER_CONFIDENCE = "invalid_provider_confidence"
    INVALID_REVIEW_FLAG = "invalid_review_flag"
    INVALID_MAPPING_PROVENANCE = "invalid_mapping_provenance"
    INVALID_CURRENT_FIT = "invalid_current_fit_mapping"
    UNKNOWN_CRITERION = "unknown_criterion"
    CRITERION_DIMENSION_MISMATCH = "criterion_dimension_mismatch"
    UNSUPPORTED_EVIDENCE_CLASS = "unsupported_evidence_class"
    FORBIDDEN_EVIDENCE_CLASS = "forbidden_evidence_class"
    SKILL_PROFICIENCY_WITHOUT_SKILL_NAME = "skill_proficiency_without_skill_name"
    INVALID_BINDING_TYPE = "invalid_binding_type"
    INVALID_BINDING_PROVENANCE = "invalid_binding_provenance"


_CODED_REJECTION_REASONS = {
    _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE: MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE,
    _MappingValidationCode.INVALID_PROFILE_REFERENCE: MappingRejectionReason.INVALID_PROFILE_REFERENCE,
    _MappingValidationCode.PROFILE_VALUE_MISMATCH: MappingRejectionReason.PROFILE_VALUE_MISMATCH,
    _MappingValidationCode.INVALID_EXACT_EXCERPT: MappingRejectionReason.INVALID_EXACT_EXCERPT,
    _MappingValidationCode.INVALID_EVIDENCE_TOKEN_BOUNDARY: MappingRejectionReason.INVALID_EVIDENCE_TOKEN_BOUNDARY,
    _MappingValidationCode.DUPLICATE_ATOMIC_EVIDENCE: MappingRejectionReason.DUPLICATE_ATOMIC_EVIDENCE,
    _MappingValidationCode.OVERLAPPING_ATOMIC_EVIDENCE: MappingRejectionReason.OVERLAPPING_ATOMIC_EVIDENCE,
    _MappingValidationCode.CROSS_DIMENSION_EVIDENCE_REUSE: MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE,
    _MappingValidationCode.INVALID_MATCH_STATUS: MappingRejectionReason.INVALID_MATCH_STATUS,
    _MappingValidationCode.INVALID_STATUS_EVIDENCE: MappingRejectionReason.INVALID_STATUS_EVIDENCE,
    _MappingValidationCode.UNSUPPORTED_INFERENCE: MappingRejectionReason.UNSUPPORTED_INFERENCE,
    _MappingValidationCode.INVALID_INFERENCE_REVIEW: MappingRejectionReason.INVALID_INFERENCE_REVIEW,
    _MappingValidationCode.INTEREST_CURRENT_FIT: MappingRejectionReason.INTEREST_CURRENT_FIT,
    _MappingValidationCode.INVALID_CONTRIBUTION: MappingRejectionReason.INVALID_CONTRIBUTION,
    _MappingValidationCode.INVALID_EVIDENCE_STRENGTH: MappingRejectionReason.INVALID_EVIDENCE_STRENGTH,
    _MappingValidationCode.INVALID_PROVIDER_CONFIDENCE: MappingRejectionReason.INVALID_PROVIDER_CONFIDENCE,
    _MappingValidationCode.INVALID_REVIEW_FLAG: MappingRejectionReason.INVALID_REVIEW_FLAG,
    _MappingValidationCode.INVALID_MAPPING_PROVENANCE: MappingRejectionReason.INVALID_MAPPING_PROVENANCE,
    _MappingValidationCode.ROLE_DIMENSION_MISMATCH: MappingRejectionReason.ROLE_DIMENSION_MISMATCH,
    _MappingValidationCode.DUPLICATE_MAPPING: MappingRejectionReason.DUPLICATE_MAPPING,
    _MappingValidationCode.PROVIDER_FIELD_INJECTION: MappingRejectionReason.PROVIDER_FIELD_INJECTION,
    _MappingValidationCode.UNKNOWN_DIMENSION: MappingRejectionReason.UNKNOWN_DIMENSION,
    _MappingValidationCode.UNKNOWN_CRITERION: MappingRejectionReason.UNKNOWN_CRITERION,
    _MappingValidationCode.CRITERION_DIMENSION_MISMATCH: MappingRejectionReason.CRITERION_DIMENSION_MISMATCH,
    _MappingValidationCode.UNSUPPORTED_EVIDENCE_CLASS: MappingRejectionReason.UNSUPPORTED_EVIDENCE_CLASS,
    _MappingValidationCode.FORBIDDEN_EVIDENCE_CLASS: MappingRejectionReason.FORBIDDEN_EVIDENCE_CLASS,
    _MappingValidationCode.SKILL_PROFICIENCY_WITHOUT_SKILL_NAME: MappingRejectionReason.SKILL_PROFICIENCY_WITHOUT_SKILL_NAME,
    _MappingValidationCode.INVALID_BINDING_TYPE: MappingRejectionReason.INVALID_BINDING_TYPE,
    _MappingValidationCode.INVALID_BINDING_PROVENANCE: MappingRejectionReason.INVALID_BINDING_PROVENANCE,
}


@dataclass(frozen=True, eq=True)
class MappingCandidateRejection:
    candidate_kind: MappingCandidateKind
    candidate_index: int
    role_id: str | None
    dimension_id: str | None
    canonical_profile_path: str | None
    reason_code: MappingRejectionReason
    attempt_number: int
    recoverable: bool
    response_hash_reference: str

    def validate(self) -> None:
        if (
            not isinstance(self.candidate_index, int)
            or isinstance(self.candidate_index, bool)
            or self.candidate_index < 0
            or not isinstance(self.attempt_number, int)
            or isinstance(self.attempt_number, bool)
            or self.attempt_number < 1
        ):
            raise Phase2ValidationError("mapping rejection position is invalid")
        if not self.recoverable:
            raise Phase2ValidationError("candidate rejection report may contain only recoverable items")
        if not self.response_hash_reference.startswith("sha256:") or len(self.response_hash_reference) != 71:
            raise Phase2ValidationError("mapping rejection response hash is invalid")

    def warning_token(self) -> str:
        self.validate()
        payload = {
            "attempt": self.attempt_number,
            "candidate_index": self.candidate_index,
            "candidate_kind": self.candidate_kind.value,
            "dimension_id": self.dimension_id,
            "profile_path": self.canonical_profile_path,
            "reason_code": self.reason_code.value,
            "response_hash_reference": self.response_hash_reference,
            "role_id": self.role_id,
        }
        return MAPPING_REJECTION_WARNING_PREFIX + json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True, eq=True)
class MappingValidationReport:
    attempt_number: int
    total_candidate_count: int
    response_hash_reference: str
    rejections: tuple[MappingCandidateRejection, ...] = ()

    def validate(self) -> None:
        if (
            not isinstance(self.attempt_number, int)
            or isinstance(self.attempt_number, bool)
            or self.attempt_number < 1
            or not isinstance(self.total_candidate_count, int)
            or isinstance(self.total_candidate_count, bool)
            or self.total_candidate_count < len(self.rejections)
        ):
            raise Phase2ValidationError("mapping validation report counts are invalid")
        if not self.response_hash_reference.startswith("sha256:") or len(self.response_hash_reference) != 71:
            raise Phase2ValidationError("mapping validation response hash is invalid")
        positions = [(item.candidate_kind, item.candidate_index) for item in self.rejections]
        if len(positions) != len(set(positions)):
            raise Phase2ValidationError("mapping validation report contains duplicate rejection positions")
        for item in self.rejections:
            item.validate()
            if item.attempt_number != self.attempt_number or item.response_hash_reference != self.response_hash_reference:
                raise Phase2ValidationError("mapping rejection context does not match its report")

    @property
    def rejected_count(self) -> int:
        return len(self.rejections)

    @property
    def all_candidates_rejected(self) -> bool:
        return self.total_candidate_count > 0 and self.rejected_count == self.total_candidate_count

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(sorted({item.reason_code.value for item in self.rejections}))

    def warning_tokens(self) -> tuple[str, ...]:
        self.validate()
        if not self.rejections:
            return ()
        summary = MAPPING_REJECTION_SUMMARY_PREFIX + json.dumps(
            {
                "attempt": self.attempt_number,
                "rejected_count": self.rejected_count,
                "response_hash_reference": self.response_hash_reference,
                "total_candidate_count": self.total_candidate_count,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            summary,
            *(item.warning_token() for item in sorted(
                self.rejections,
                key=lambda value: (value.candidate_kind.value, value.candidate_index),
            )),
        )


def _fact_identity(reference: ProfileReference) -> str:
    snapshot = json.dumps(reference.value_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{reference.path}|{snapshot}|{reference.profile_fingerprint}"


def mapping_validation_report_from_warnings(
    warnings: tuple[str, ...],
) -> MappingValidationReport | None:
    summaries = [
        value[len(MAPPING_REJECTION_SUMMARY_PREFIX):]
        for value in warnings
        if value.startswith(MAPPING_REJECTION_SUMMARY_PREFIX)
    ]
    details = [
        value[len(MAPPING_REJECTION_WARNING_PREFIX):]
        for value in warnings
        if value.startswith(MAPPING_REJECTION_WARNING_PREFIX)
    ]
    if not summaries and not details:
        return None
    if len(summaries) != 1:
        raise Phase2ValidationError("mapping warnings require exactly one rejection summary")
    if not details:
        raise Phase2ValidationError("mapping rejection summary requires rejection details")
    try:
        summary = json.loads(summaries[0])
    except (TypeError, json.JSONDecodeError) as error:
        raise Phase2ValidationError("mapping rejection summary is invalid JSON") from error
    if not isinstance(summary, Mapping) or set(summary) != {
        "attempt", "rejected_count", "response_hash_reference", "total_candidate_count"
    }:
        raise Phase2ValidationError("mapping rejection summary fields are invalid")
    rejections: list[MappingCandidateRejection] = []
    expected_fields = {
        "attempt", "candidate_index", "candidate_kind", "dimension_id", "profile_path",
        "reason_code", "response_hash_reference", "role_id",
    }
    for encoded in details:
        try:
            item = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as error:
            raise Phase2ValidationError("mapping rejection detail is invalid JSON") from error
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            raise Phase2ValidationError("mapping rejection detail fields are invalid")
        role_id = item.get("role_id")
        dimension_id = item.get("dimension_id")
        profile_path = item.get("profile_path")
        if any(value is not None and not isinstance(value, str) for value in (role_id, dimension_id, profile_path)):
            raise Phase2ValidationError("mapping rejection identifiers are invalid")
        rejections.append(MappingCandidateRejection(
            candidate_kind=_enum(item.get("candidate_kind"), MappingCandidateKind, "mapping_rejection.candidate_kind"),
            candidate_index=item.get("candidate_index"),
            role_id=role_id,
            dimension_id=dimension_id,
            canonical_profile_path=profile_path,
            reason_code=_enum(item.get("reason_code"), MappingRejectionReason, "mapping_rejection.reason_code"),
            attempt_number=item.get("attempt"),
            recoverable=True,
            response_hash_reference=_text(item.get("response_hash_reference"), "mapping_rejection.response_hash_reference"),
        ))
    report = MappingValidationReport(
        attempt_number=summary.get("attempt"),
        total_candidate_count=summary.get("total_candidate_count"),
        response_hash_reference=_text(
            summary.get("response_hash_reference"),
            "mapping_rejection_summary.response_hash_reference",
        ),
        rejections=tuple(rejections),
    )
    if summary.get("rejected_count") != len(rejections):
        raise Phase2ValidationError("mapping rejection summary count is invalid")
    report.validate()
    return report


def _profile_leaf_values(profile: CareerProfile) -> tuple[tuple[str, Any], ...]:
    leaves: list[tuple[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value):
                child = f"{path}.{key}" if path else key
                visit(value[key], child)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if value not in (None, ""):
            leaves.append((path, value))

    data = profile.to_dict()
    for key in sorted(data):
        if key != "open_questions":
            visit(data[key], key)
    return tuple(sorted(leaves, key=lambda item: item[0]))


def allowed_profile_reference_paths(profile: CareerProfile) -> tuple[str, ...]:
    """Return deterministic, confirmed leaf paths available to the Provider."""
    return tuple(path for path, _ in _profile_leaf_values(profile))


_ACTION_VERBS = (
    "analyzed", "automated", "built", "created", "deployed", "designed",
    "developed", "evaluated", "implemented", "integrated", "led", "maintained",
    "modeled", "optimized", "processed", "researched", "supported", "taught",
    "tested", "trained", "used", "validated",
)
_ACTION_PATTERN = "(?:" + "|".join(_ACTION_VERBS) + ")"
_PARALLEL_BOUNDARY = re.compile(
    rf",\s+(?=(?:and\s+)?{_ACTION_PATTERN}\b)"
    rf"|\s+(?=(?:and|but|while|then)\s+{_ACTION_PATTERN}\b)",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?:\s+|$)|;\s*|\n+")


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w+#.-]+\b", value, flags=re.UNICODE))


def _trimmed_offsets(value: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and value[start].isspace():
        start += 1
    while end > start and value[end - 1].isspace():
        end -= 1
    return None if start == end else (start, end)


def _sentence_span_offsets(value: str) -> tuple[tuple[int, int], ...]:
    primary: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY.finditer(value):
        boundary_end = (
            match.start() + 1
            if value[match.start()] in ".!?;"
            else match.start()
        )
        trimmed = _trimmed_offsets(value, cursor, boundary_end)
        if trimmed is not None:
            primary.append(trimmed)
        cursor = match.end()
    tail = _trimmed_offsets(value, cursor, len(value))
    if tail is not None:
        primary.append(tail)

    result: list[tuple[int, int]] = []
    for segment_start, segment_end in primary:
        segment = value[segment_start:segment_end]
        local_start = 0
        for match in _PARALLEL_BOUNDARY.finditer(segment):
            left = segment[local_start:match.start()]
            remainder = segment[match.end():]
            if _word_count(left) < 3 or _word_count(remainder) < 3:
                continue
            trimmed = _trimmed_offsets(
                value, segment_start + local_start, segment_start + match.start()
            )
            if trimmed is not None:
                result.append(trimmed)
            local_start = match.end()
        trimmed = _trimmed_offsets(
            value, segment_start + local_start, segment_end
        )
        if trimmed is not None:
            result.append(trimmed)
    return tuple(result)


def canonical_evidence_span_inventory(
    profile: CareerProfile,
) -> tuple[CanonicalEvidenceSpan, ...]:
    """Build deterministic Provider-selectable spans without changing Profile text."""
    spans: list[CanonicalEvidenceSpan] = []
    for path, value in _profile_leaf_values(profile):
        if not isinstance(value, str):
            continue
        for start, end in _sentence_span_offsets(value):
            span = CanonicalEvidenceSpan(
                span_id=_canonical_evidence_span_id(profile, path, start, end),
                path=path,
                start_offset=start,
                end_offset=end,
                text=value[start:end],
            )
            span.materialize(profile)
            spans.append(span)
    return tuple(sorted(spans, key=lambda item: (item.path, item.start_offset, item.end_offset)))


def _canonical_provider_reference_path(path: str) -> str:
    if path in {"career_profile", "career_profile."}:
        raise Phase2ValidationError(
            "Profile reference must identify a leaf path relative to career_profile"
        )
    prefix = "career_profile."
    if path.startswith(prefix):
        canonical = path[len(prefix):]
        if not canonical or canonical.startswith(prefix):
            raise Phase2ValidationError(
                "Profile reference may remove exactly one leading career_profile. transport prefix"
            )
        return canonical
    return path


def generate_mapping_id(
    *, role_id: str, dimension_id: str, status: CurrentMatchStatus,
    relationship: ContributionRelationship, references: tuple[ProfileReference, ...]
) -> str:
    values = (
        MAPPING_ID_VERSION, role_id, dimension_id, status.value, relationship.value,
        *sorted(_fact_identity(item) for item in references),
    )
    return f"mapping_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


def generate_atomic_mapping_id(
    *, role_id: str, dimension_id: str, status: CurrentMatchStatus,
    relationship: ContributionRelationship,
    evidence_locators: tuple[AtomicEvidenceLocator, ...],
) -> str:
    values = (
        ATOMIC_MAPPING_ID_VERSION, role_id, dimension_id, status.value,
        relationship.value,
        *sorted(item.evidence_fingerprint for item in evidence_locators),
    )
    return f"mapping_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


def deterministic_mapping_reasoning(
    *, dimension_name: str, status: CurrentMatchStatus,
    inference_type: InferenceType,
    evidence_locators: tuple[AtomicEvidenceLocator, ...],
) -> str:
    if not evidence_locators:
        return f"{dimension_name}: {status.value.replace('_', ' ')} with no validated atomic evidence."
    excerpts = "; ".join(
        f'"{item.exact_excerpt}"'
        for item in sorted(
            evidence_locators, key=lambda value: value.evidence_fingerprint
        )
    )
    return (
        f"{dimension_name}: {status.value.replace('_', ' ')} from "
        f"{inference_type.value.replace('_', ' ')} Profile evidence {excerpts}."
    )


def _provider_references(
    value: Any, *, path: str, profile: CareerProfile
) -> tuple[ProfileReference, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    references: list[ProfileReference] = []
    allowed_paths = set(allowed_profile_reference_paths(profile))
    for index, item in enumerate(value):
        data = _mapping(item, f"{path}[{index}]")
        _reject_unknown(data, {"path", "value_snapshot"}, f"{path}[{index}]")
        raw_path = _text(data.get("path"), f"{path}[{index}].path")
        canonical_path = _canonical_provider_reference_path(raw_path)
        if canonical_path not in allowed_paths:
            raise Phase2ValidationError(
                f"{path}[{index}].path does not exist in allowed_profile_references: "
                f"{canonical_path}"
            )
        reference = ProfileReference.capture(profile, canonical_path)
        if data.get("value_snapshot") != reference.value_snapshot:
            raise Phase2ValidationError(
                f"{path}[{index}].value_snapshot does not match the Career Profile"
            )
        references.append(reference)
    identities = [_fact_identity(item) for item in references]
    if len(identities) != len(set(identities)):
        raise Phase2ValidationError(f"{path} contains duplicate Profile references")
    return tuple(references)


def _provider_atomic_evidence(
    value: Any, *, path: str, profile: CareerProfile
) -> tuple[AtomicEvidenceLocator, ...]:
    if not isinstance(value, list):
        raise _CodedMappingValidationError(
            _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE,
            f"{path} must be a list",
        )
    evidence: list[AtomicEvidenceLocator] = []
    allowed_paths = set(allowed_profile_reference_paths(profile))
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        try:
            data = _mapping(item, item_path)
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE, str(error)
            ) from error
        try:
            _reject_unknown(
                data, {"path", "value_snapshot", "exact_excerpt"}, item_path
            )
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.PROVIDER_FIELD_INJECTION, str(error)
            ) from error
        try:
            raw_path = _text(data.get("path"), f"{item_path}.path")
            canonical_path = _canonical_provider_reference_path(raw_path)
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_PROFILE_REFERENCE, str(error)
            ) from error
        if canonical_path not in allowed_paths:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_PROFILE_REFERENCE,
                f"{item_path}.path does not exist in allowed_profile_references: {canonical_path}",
            )
        reference = ProfileReference.capture(profile, canonical_path)
        if data.get("value_snapshot") != reference.value_snapshot:
            raise _CodedMappingValidationError(
                _MappingValidationCode.PROFILE_VALUE_MISMATCH,
                f"{item_path}.value_snapshot does not match the Career Profile",
            )
        try:
            exact_excerpt = _text(
                data.get("exact_excerpt"), f"{item_path}.exact_excerpt"
            )
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_EXACT_EXCERPT, str(error)
            ) from error
        evidence.append(AtomicEvidenceLocator.capture(
            profile,
            canonical_path,
            exact_excerpt,
        ))
    fingerprints = [item.evidence_fingerprint for item in evidence]
    if len(fingerprints) != len(set(fingerprints)):
        raise _CodedMappingValidationError(
            _MappingValidationCode.DUPLICATE_ATOMIC_EVIDENCE,
            f"{path} contains duplicate atomic evidence",
        )
    return tuple(evidence)


def _materialize_current_fit_transport_candidate(
    raw: Any,
    *,
    profile: CareerProfile,
    spans_by_id: Mapping[str, CanonicalEvidenceSpan],
) -> Any:
    if not isinstance(raw, Mapping):
        return raw
    candidate = dict(raw)
    references = candidate.get("profile_fact_references")
    if not isinstance(references, list):
        raise _CodedMappingValidationError(
            _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE,
            "Current Fit span selections must be a list",
        )
    materialized: list[dict[str, Any]] = []
    for index, raw_reference in enumerate(references):
        item_path = f"profile_fact_references[{index}]"
        try:
            data = _mapping(raw_reference, item_path)
            _reject_unknown(data, {"span_id"}, item_path)
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.PROVIDER_FIELD_INJECTION, str(error)
            ) from error
        span_id = data.get("span_id")
        if not isinstance(span_id, str) or span_id not in spans_by_id:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_PROFILE_REFERENCE,
                f"{item_path}.span_id is not in allowed_evidence_spans",
            )
        locator = spans_by_id[span_id].materialize(profile)
        materialized.append({
            "path": locator.profile_reference.path,
            "value_snapshot": locator.profile_reference.value_snapshot,
            "exact_excerpt": locator.exact_excerpt,
        })
    candidate["profile_fact_references"] = materialized
    return candidate


def _materialize_profile_reference_transport_candidate(
    raw: Any,
    *,
    profile: CareerProfile,
    spans_by_id: Mapping[str, CanonicalEvidenceSpan],
) -> Any:
    if not isinstance(raw, Mapping):
        return raw
    candidate = dict(raw)
    references = candidate.get("profile_fact_references")
    if not isinstance(references, list):
        raise Phase2ValidationError("Profile span selections must be a list")
    materialized: list[dict[str, Any]] = []
    for index, raw_reference in enumerate(references):
        item_path = f"profile_fact_references[{index}]"
        try:
            data = _mapping(raw_reference, item_path)
            _reject_unknown(data, {"span_id"}, item_path)
        except Phase2ValidationError as error:
            raise _CodedMappingValidationError(
                _MappingValidationCode.PROVIDER_FIELD_INJECTION, str(error)
            ) from error
        span_id = data.get("span_id")
        if not isinstance(span_id, str) or span_id not in spans_by_id:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_PROFILE_REFERENCE,
                f"{item_path}.span_id is not in allowed_evidence_spans"
            )
        locator = spans_by_id[span_id].materialize(profile)
        materialized.append({
            "path": locator.profile_reference.path,
            "value_snapshot": locator.profile_reference.value_snapshot,
        })
    candidate["profile_fact_references"] = materialized
    return candidate


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
    atomic_evidence: tuple[AtomicEvidenceLocator, ...] = ()

    def validate(
        self, *, profile: CareerProfile, rubric: CapabilityRubric,
        require_atomic: bool = True,
    ) -> None:
        dimension = rubric.dimension(self.dimension_id)
        if dimension.role_id != self.role_id:
            raise _CodedMappingValidationError(
                _MappingValidationCode.ROLE_DIMENSION_MISMATCH,
                "mapping dimension belongs to another Role Family",
            )
        if require_atomic:
            if tuple(item.profile_reference for item in self.atomic_evidence) != self.profile_fact_references:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_MAPPING_PROVENANCE,
                    "Current Fit references must match atomic evidence locators",
                )
            expected = generate_atomic_mapping_id(
                role_id=self.role_id,
                dimension_id=self.dimension_id,
                status=self.match_status,
                relationship=self.relationship,
                evidence_locators=self.atomic_evidence,
            )
        else:
            if self.atomic_evidence:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_MAPPING_PROVENANCE,
                    "legacy mapping schema cannot contain atomic evidence",
                )
            expected = generate_mapping_id(
                role_id=self.role_id, dimension_id=self.dimension_id, status=self.match_status,
                relationship=self.relationship, references=self.profile_fact_references,
            )
        if self.mapping_id != expected:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_MAPPING_PROVENANCE,
                "mapping ID is not deterministic",
            )
        if require_atomic and self.reasoning != deterministic_mapping_reasoning(
            dimension_name=dimension.name,
            status=self.match_status,
            inference_type=self.inference_type,
            evidence_locators=self.atomic_evidence,
        ):
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_MAPPING_PROVENANCE,
                "Current Fit reasoning is not evidence-derived",
            )
        if self.match_status in {
            CurrentMatchStatus.DEMONSTRATED,
            CurrentMatchStatus.PARTIAL,
            CurrentMatchStatus.ADJACENT,
        } and not self.profile_fact_references:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_STATUS_EVIDENCE,
                "positive capability mapping requires Profile evidence",
            )
        if self.inference_type == InferenceType.INTEREST_ONLY_DIRECTIONAL or any(
            reference.path.startswith("career_preferences.")
            for reference in self.profile_fact_references
        ):
            raise _CodedMappingValidationError(
                _MappingValidationCode.INTEREST_CURRENT_FIT,
                "interest cannot support Current Fit",
            )
        if self.inference_type in {InferenceType.BOUNDED_SEMANTIC, InferenceType.ADJACENT_TRANSFER} and not self.review_required:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_INFERENCE_REVIEW,
                "semantic or transferable mapping requires review",
            )
        if self.relationship == ContributionRelationship.SECONDARY and self.evidence_strength == EvidenceStrength.STRONG:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_CONTRIBUTION,
                "secondary mapping cannot claim strong evidence",
            )
        for reference in self.profile_fact_references:
            try:
                reference.validate(profile)
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_PROFILE_REFERENCE, str(error)
                ) from error
        for locator in self.atomic_evidence:
            try:
                locator.validate(profile)
            except _CodedMappingValidationError:
                raise
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_MAPPING_PROVENANCE, str(error)
                ) from error

    def to_dict(self, *, include_atomic: bool = True) -> dict[str, Any]:
        result = {
            "mapping_id": self.mapping_id, "role_id": self.role_id,
            "dimension_id": self.dimension_id, "match_status": self.match_status.value,
            "profile_fact_references": [item.to_dict() for item in self.profile_fact_references],
            "reasoning": self.reasoning, "inference_type": self.inference_type.value,
            "evidence_strength": self.evidence_strength.value,
            "provider_confidence": self.provider_confidence.value,
            "review_required": self.review_required, "relationship": self.relationship.value,
            "suggested_follow_up": self.suggested_follow_up,
        }
        if include_atomic:
            result["atomic_evidence"] = [item.to_dict() for item in self.atomic_evidence]
        return result


_SKILL_NAME_PATH = re.compile(r"^skills\[(\d+)\]\.skill_name$")
_SKILL_PROFICIENCY_PATH = re.compile(r"^skills\[(\d+)\]\.self_reported_proficiency$")
_EDUCATION_FIELD_PATH = re.compile(r"^education\[(\d+)\]\.field_of_study$")
_EXPERIENCE_SUMMARY_PATH = re.compile(
    r"^experience_overview\[(\d+)\]\.short_factual_summary$"
)


def evidence_class_for_locator(
    profile: CareerProfile, locator: AtomicEvidenceLocator
) -> EvidenceClass:
    """Classify evidence from its validated Profile object and leaf path only."""
    locator.validate(profile)
    path = locator.profile_reference.path
    if _SKILL_NAME_PATH.fullmatch(path):
        return EvidenceClass.SKILL_NAME
    if _SKILL_PROFICIENCY_PATH.fullmatch(path):
        return EvidenceClass.SKILL_PROFICIENCY
    if _EDUCATION_FIELD_PATH.fullmatch(path):
        return EvidenceClass.EDUCATION_FIELD
    match = _EXPERIENCE_SUMMARY_PATH.fullmatch(path)
    if match:
        index = int(match.group(1))
        experience = profile.experience_overview[index]
        if experience.experience_type.strip().casefold() == "project":
            return EvidenceClass.PROJECT_SUMMARY
        return EvidenceClass.EXPERIENCE_SUMMARY
    raise _CodedMappingValidationError(
        _MappingValidationCode.UNSUPPORTED_EVIDENCE_CLASS,
        "the selected Profile leaf is not a supported capability evidence class",
    )


def generate_evidence_binding_id(
    *,
    role_id: str,
    dimension_id: str,
    criterion_id: str,
    locator: AtomicEvidenceLocator,
    proposed_binding_type: ProposedBindingType,
) -> str:
    values = (
        EVIDENCE_BINDING_ID_VERSION,
        _stable_id(role_id, "role_id"),
        _stable_id(dimension_id, "dimension_id"),
        _stable_id(criterion_id, "criterion_id"),
        locator.evidence_fingerprint,
        proposed_binding_type.value,
    )
    return f"binding_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


def _status_from_cap(cap: EvidenceStatusCap) -> CurrentMatchStatus:
    return {
        EvidenceStatusCap.UNKNOWN: CurrentMatchStatus.UNKNOWN,
        EvidenceStatusCap.ADJACENT_TRANSFERABLE: CurrentMatchStatus.ADJACENT,
        EvidenceStatusCap.PARTIALLY_DEMONSTRATED: CurrentMatchStatus.PARTIAL,
        EvidenceStatusCap.DEMONSTRATED: CurrentMatchStatus.DEMONSTRATED,
    }[cap]


def _derive_binding_outcome(
    *,
    evidence_class: EvidenceClass,
    proposed_binding_type: ProposedBindingType,
    structural_cap: EvidenceStatusCap,
    provisional_cap: EvidenceStatusCap,
) -> tuple[
    EvidenceTrustLevel,
    CurrentMatchStatus,
    EvidenceStrength,
    InferenceType,
    bool,
    tuple[BindingDerivationReason, ...],
]:
    structural = evidence_class in {
        EvidenceClass.SKILL_NAME,
        EvidenceClass.SKILL_PROFICIENCY,
        EvidenceClass.EDUCATION_FIELD,
    }
    if structural:
        status = _status_from_cap(structural_cap)
        reasons = [BindingDerivationReason.STRUCTURAL_POLICY_CAP]
        if evidence_class == EvidenceClass.SKILL_PROFICIENCY:
            reasons.append(BindingDerivationReason.SKILL_PROFICIENCY_PAIRED)
        return (
            EvidenceTrustLevel.STRUCTURAL_ONLY,
            status,
            EvidenceStrength.WEAK,
            InferenceType.ADJACENT_TRANSFER,
            False,
            tuple(reasons),
        )

    status_cap = provisional_cap
    reasons = [BindingDerivationReason.PROVISIONAL_POLICY_CAP]
    if proposed_binding_type == ProposedBindingType.ADJACENT_TRANSFER:
        status_cap = EvidenceStatusCap.ADJACENT_TRANSFERABLE
        reasons.append(BindingDerivationReason.PROVIDER_ADJACENT_LIMIT)
    status = _status_from_cap(status_cap)
    inference = (
        InferenceType.ADJACENT_TRANSFER
        if status == CurrentMatchStatus.ADJACENT
        else InferenceType.BOUNDED_SEMANTIC
    )
    reasons.extend((
        BindingDerivationReason.CONFIRMED_BINDING_UNAVAILABLE,
        BindingDerivationReason.HUMAN_REVIEW_REQUIRED,
    ))
    return (
        EvidenceTrustLevel.PROVISIONAL_SEMANTIC,
        status,
        EvidenceStrength.SUPPORTING
        if status == CurrentMatchStatus.PARTIAL
        else EvidenceStrength.WEAK,
        inference,
        True,
        tuple(reasons),
    )


@dataclass(frozen=True, eq=True)
class ProfileCriterionEvidenceBinding:
    binding_id: str
    role_id: str
    dimension_id: str
    criterion_id: str
    atomic_evidence: AtomicEvidenceLocator
    evidence_class: EvidenceClass
    trust_level: EvidenceTrustLevel
    proposed_binding_type: ProposedBindingType
    provider_confidence: ProviderConfidence
    derived_match_status: CurrentMatchStatus
    derived_evidence_strength: EvidenceStrength
    derived_inference_type: InferenceType
    derived_review_required: bool
    derivation_reason_codes: tuple[BindingDerivationReason, ...]

    def validate(
        self,
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
    ) -> None:
        enum_fields = (
            (self.evidence_class, EvidenceClass, "evidence_class"),
            (self.trust_level, EvidenceTrustLevel, "trust_level"),
            (self.proposed_binding_type, ProposedBindingType, "proposed_binding_type"),
            (self.provider_confidence, ProviderConfidence, "provider_confidence"),
            (self.derived_match_status, CurrentMatchStatus, "derived_match_status"),
            (
                self.derived_evidence_strength,
                EvidenceStrength,
                "derived_evidence_strength",
            ),
            (self.derived_inference_type, InferenceType, "derived_inference_type"),
        )
        for value, enum_type, field_name in enum_fields:
            if not isinstance(value, enum_type):
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                    f"binding {field_name} must use {enum_type.__name__}",
                )
        if not isinstance(self.derived_review_required, bool):
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "binding derived_review_required must be boolean",
            )
        if (
            not isinstance(self.derivation_reason_codes, tuple)
            or any(
                not isinstance(item, BindingDerivationReason)
                for item in self.derivation_reason_codes
            )
        ):
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "binding derivation reasons must use BindingDerivationReason",
            )
        if rubric.schema_version != 2:
            raise Phase2ValidationError("Mapping schema 3 requires Capability Rubric schema 2")
        dimension = rubric.dimension(self.dimension_id)
        if dimension.role_id != self.role_id:
            raise _CodedMappingValidationError(
                _MappingValidationCode.ROLE_DIMENSION_MISMATCH,
                "binding Dimension belongs to another Role Family",
            )
        criterion_ids = set(dimension.criterion_ids)
        if self.criterion_id not in criterion_ids:
            raise _CodedMappingValidationError(
                _MappingValidationCode.CRITERION_DIMENSION_MISMATCH,
                "binding criterion does not belong to its Dimension",
            )
        policy = dimension.evidence_support_policy
        if policy is None:
            raise Phase2ValidationError("Mapping schema 3 requires an Evidence Support Policy")
        actual_class = evidence_class_for_locator(profile, self.atomic_evidence)
        if self.evidence_class != actual_class:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "binding evidence class does not match its Profile path",
            )
        if actual_class in policy.forbidden_evidence_classes:
            raise _CodedMappingValidationError(
                _MappingValidationCode.FORBIDDEN_EVIDENCE_CLASS,
                "the Dimension policy forbids this evidence class",
            )
        if actual_class not in policy.allowed_evidence_classes:
            raise _CodedMappingValidationError(
                _MappingValidationCode.UNSUPPORTED_EVIDENCE_CLASS,
                "the Dimension policy does not allow this evidence class",
            )
        expected_id = generate_evidence_binding_id(
            role_id=self.role_id,
            dimension_id=self.dimension_id,
            criterion_id=self.criterion_id,
            locator=self.atomic_evidence,
            proposed_binding_type=self.proposed_binding_type,
        )
        if self.binding_id != expected_id:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "binding ID is not deterministic",
            )
        expected = _derive_binding_outcome(
            evidence_class=actual_class,
            proposed_binding_type=self.proposed_binding_type,
            structural_cap=policy.structural_caps[actual_class],
            provisional_cap=policy.provisional_status_cap,
        )
        actual = (
            self.trust_level,
            self.derived_match_status,
            self.derived_evidence_strength,
            self.derived_inference_type,
            self.derived_review_required,
            self.derivation_reason_codes,
        )
        if actual != expected:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "binding derivation does not match the active Dimension policy",
            )
        if self.derived_match_status == CurrentMatchStatus.DEMONSTRATED:
            raise _CodedMappingValidationError(
                _MappingValidationCode.INVALID_BINDING_PROVENANCE,
                "Mapping schema 3 cannot establish demonstrated without confirmed review",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "role_id": self.role_id,
            "dimension_id": self.dimension_id,
            "criterion_id": self.criterion_id,
            "atomic_evidence": self.atomic_evidence.to_dict(),
            "evidence_class": self.evidence_class.value,
            "trust_level": self.trust_level.value,
            "proposed_binding_type": self.proposed_binding_type.value,
            "provider_confidence": self.provider_confidence.value,
            "derived_match_status": self.derived_match_status.value,
            "derived_evidence_strength": self.derived_evidence_strength.value,
            "derived_inference_type": self.derived_inference_type.value,
            "derived_review_required": self.derived_review_required,
            "derivation_reason_codes": [item.value for item in self.derivation_reason_codes],
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


def _binding_from_provider(
    value: Any,
    *,
    index: int,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    spans_by_id: Mapping[str, CanonicalEvidenceSpan],
) -> ProfileCriterionEvidenceBinding:
    path = f"mapping_candidates.mappings[{index}]"
    data = _mapping(value, path)
    _reject_unknown(
        data,
        {
            "role_id", "dimension_id", "criterion_id", "span_id",
            "proposed_binding_type", "provider_confidence",
        },
        path,
    )
    role_id = _stable_id(data.get("role_id"), f"{path}.role_id")
    dimension_id = _stable_id(data.get("dimension_id"), f"{path}.dimension_id")
    criterion_id = _stable_id(data.get("criterion_id"), f"{path}.criterion_id")
    try:
        dimension = rubric.dimension(dimension_id)
    except Phase2ValidationError as error:
        raise _CodedMappingValidationError(
            _MappingValidationCode.UNKNOWN_DIMENSION, str(error)
        ) from error
    if dimension.role_id != role_id:
        raise _CodedMappingValidationError(
            _MappingValidationCode.ROLE_DIMENSION_MISMATCH,
            "binding Dimension belongs to another Role Family",
        )
    all_criteria = {
        item for rubric_dimension in rubric.dimensions for item in rubric_dimension.criterion_ids
    }
    if criterion_id not in all_criteria:
        raise _CodedMappingValidationError(
            _MappingValidationCode.UNKNOWN_CRITERION,
            "binding criterion is not present in the Capability Rubric",
        )
    if criterion_id not in set(dimension.criterion_ids):
        raise _CodedMappingValidationError(
            _MappingValidationCode.CRITERION_DIMENSION_MISMATCH,
            "binding criterion belongs to another Dimension",
        )
    span_id = data.get("span_id")
    if not isinstance(span_id, str) or span_id not in spans_by_id:
        raise _CodedMappingValidationError(
            _MappingValidationCode.INVALID_PROFILE_REFERENCE,
            "binding span_id is not in allowed_evidence_spans",
        )
    locator = spans_by_id[span_id].materialize(profile)
    evidence_class = evidence_class_for_locator(profile, locator)
    policy = dimension.evidence_support_policy
    if policy is None:
        raise Phase2ValidationError("Mapping schema 3 requires an Evidence Support Policy")
    if evidence_class in policy.forbidden_evidence_classes:
        raise _CodedMappingValidationError(
            _MappingValidationCode.FORBIDDEN_EVIDENCE_CLASS,
            "the Dimension policy forbids this evidence class",
        )
    if evidence_class not in policy.allowed_evidence_classes:
        raise _CodedMappingValidationError(
            _MappingValidationCode.UNSUPPORTED_EVIDENCE_CLASS,
            "the Dimension policy does not allow this evidence class",
        )
    try:
        proposed_type = _enum(
            data.get("proposed_binding_type"),
            ProposedBindingType,
            f"{path}.proposed_binding_type",
        )
    except Phase2ValidationError as error:
        raise _CodedMappingValidationError(
            _MappingValidationCode.INVALID_BINDING_TYPE, str(error)
        ) from error
    try:
        provider_confidence = _enum(
            data.get("provider_confidence"),
            ProviderConfidence,
            f"{path}.provider_confidence",
        )
    except Phase2ValidationError as error:
        raise _CodedMappingValidationError(
            _MappingValidationCode.INVALID_PROVIDER_CONFIDENCE, str(error)
        ) from error
    derived = _derive_binding_outcome(
        evidence_class=evidence_class,
        proposed_binding_type=proposed_type,
        structural_cap=policy.structural_caps[evidence_class],
        provisional_cap=policy.provisional_status_cap,
    )
    binding = ProfileCriterionEvidenceBinding(
        binding_id=generate_evidence_binding_id(
            role_id=role_id,
            dimension_id=dimension_id,
            criterion_id=criterion_id,
            locator=locator,
            proposed_binding_type=proposed_type,
        ),
        role_id=role_id,
        dimension_id=dimension_id,
        criterion_id=criterion_id,
        atomic_evidence=locator,
        evidence_class=evidence_class,
        trust_level=derived[0],
        proposed_binding_type=proposed_type,
        provider_confidence=provider_confidence,
        derived_match_status=derived[1],
        derived_evidence_strength=derived[2],
        derived_inference_type=derived[3],
        derived_review_required=derived[4],
        derivation_reason_codes=derived[5],
    )
    binding.validate(profile=profile, rubric=rubric)
    return binding


@dataclass(frozen=True, eq=True)
class ProfileDimensionMappingCandidateSet:
    rubric_version: str
    role_catalog_version: str
    profile_fingerprint: str
    mappings: tuple[ProfileDimensionMappingCandidate | ProfileCriterionEvidenceBinding, ...]
    directional_signals: tuple[DirectionalSignalCandidate, ...]
    constraints: tuple[ConstraintCompatibilityCandidate, ...]
    conflict_warnings: tuple[str, ...] = ()
    provider_name: str = "offline"
    provider_model: str = "offline"
    schema: str = MAPPING_SCHEMA
    schema_version: int = MAPPING_SCHEMA_VERSION
    validation_report: MappingValidationReport | None = field(
        default=None, compare=False, repr=False
    )

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
            evidence = _provider_atomic_evidence(
                item.get("profile_fact_references"),
                path=f"{path}.profile_fact_references",
                profile=profile,
            )
            refs = tuple(locator.profile_reference for locator in evidence)
            try:
                status = _enum(
                    item.get("match_status"), CurrentMatchStatus, f"{path}.match_status"
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_MATCH_STATUS, str(error)
                ) from error
            try:
                relationship = _enum(
                    item.get("relationship"),
                    ContributionRelationship,
                    f"{path}.relationship",
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_CONTRIBUTION, str(error)
                ) from error
            role_id = _stable_id(item.get("role_id"), f"{path}.role_id")
            dimension_id = _stable_id(item.get("dimension_id"), f"{path}.dimension_id")
            try:
                inference_type = _enum(
                    item.get("inference_type"), InferenceType, f"{path}.inference_type"
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.UNSUPPORTED_INFERENCE, str(error)
                ) from error
            try:
                _text(item.get("reasoning"), f"{path}.reasoning")
                suggested_follow_up = _text(
                    item.get("suggested_follow_up"),
                    f"{path}.suggested_follow_up",
                    required=False,
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE, str(error)
                ) from error
            try:
                evidence_strength = _enum(
                    item.get("evidence_strength"),
                    EvidenceStrength,
                    f"{path}.evidence_strength",
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_EVIDENCE_STRENGTH, str(error)
                ) from error
            try:
                provider_confidence = _enum(
                    item.get("provider_confidence"),
                    ProviderConfidence,
                    f"{path}.provider_confidence",
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_PROVIDER_CONFIDENCE, str(error)
                ) from error
            try:
                review_required = _boolean(
                    item.get("review_required"), f"{path}.review_required"
                )
            except Phase2ValidationError as error:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.INVALID_REVIEW_FLAG, str(error)
                ) from error
            mappings.append(ProfileDimensionMappingCandidate(
                mapping_id=generate_atomic_mapping_id(
                    role_id=role_id, dimension_id=dimension_id, status=status,
                    relationship=relationship, evidence_locators=evidence,
                ),
                role_id=role_id, dimension_id=dimension_id, match_status=status,
                profile_fact_references=refs,
                reasoning=deterministic_mapping_reasoning(
                    dimension_name=rubric.dimension(dimension_id).name,
                    status=status,
                    inference_type=inference_type,
                    evidence_locators=evidence,
                ),
                inference_type=inference_type,
                evidence_strength=evidence_strength,
                provider_confidence=provider_confidence,
                review_required=review_required,
                relationship=relationship,
                suggested_follow_up=suggested_follow_up,
                atomic_evidence=evidence,
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
            schema_version=2,
        )
        result.validate(profile=profile, rubric=rubric, catalog=catalog)
        return result

    @classmethod
    def from_provider_payload_v3(
        cls,
        payload: Mapping[str, Any],
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
        catalog: RoleCatalog,
        provider_name: str,
        provider_model: str,
        evidence_spans: tuple[CanonicalEvidenceSpan, ...],
    ) -> ProfileDimensionMappingCandidateSet:
        if rubric.schema_version != 2:
            raise Phase2ValidationError("Mapping schema 3 requires Capability Rubric schema 2")
        data = _mapping(payload, "mapping_candidates")
        _reject_unknown(
            data,
            {"mappings", "directional_signals", "constraints", "conflict_warnings"},
            "mapping_candidates",
        )
        raw_mappings = _list(data.get("mappings"), "mapping_candidates.mappings")
        raw_directional = _list(
            data.get("directional_signals"), "mapping_candidates.directional_signals"
        )
        raw_constraints = _list(data.get("constraints"), "mapping_candidates.constraints")
        spans_by_id = {span.span_id: span for span in evidence_spans}
        if len(spans_by_id) != len(evidence_spans):
            raise Phase2ValidationError("allowed_evidence_spans contains duplicate span IDs")
        mappings = tuple(
            _binding_from_provider(
                item,
                index=index,
                profile=profile,
                rubric=rubric,
                spans_by_id=spans_by_id,
            )
            for index, item in enumerate(raw_mappings)
        )
        directional = tuple(
            _directional_from_provider(
                _materialize_profile_reference_transport_candidate(
                    item, profile=profile, spans_by_id=spans_by_id
                ),
                index=index,
                profile=profile,
            )
            for index, item in enumerate(raw_directional)
        )
        constraints = tuple(
            _constraint_from_provider(
                _materialize_profile_reference_transport_candidate(
                    item, profile=profile, spans_by_id=spans_by_id
                ),
                index=index,
                profile=profile,
            )
            for index, item in enumerate(raw_constraints)
        )
        result = cls(
            rubric_version=rubric.rubric_version,
            role_catalog_version=catalog.catalog_version,
            profile_fingerprint=profile_fingerprint(profile),
            mappings=mappings,
            directional_signals=directional,
            constraints=constraints,
            conflict_warnings=_string_tuple(
                data.get("conflict_warnings"), "mapping_candidates.conflict_warnings"
            ),
            provider_name=_text(provider_name, "provider_name"),
            provider_model=_text(provider_model, "provider_model"),
            schema_version=3,
        )
        result.validate(profile=profile, rubric=rubric, catalog=catalog)
        return result

    @classmethod
    def from_provider_payload_isolated(
        cls,
        payload: Mapping[str, Any],
        *,
        profile: CareerProfile,
        rubric: CapabilityRubric,
        catalog: RoleCatalog,
        provider_name: str,
        provider_model: str,
        attempt_number: int,
        response_hash_reference: str,
        evidence_spans: tuple[CanonicalEvidenceSpan, ...] | None = None,
        mapping_schema_version: int = 2,
    ) -> ProfileDimensionMappingCandidateSet:
        return _isolate_provider_candidates(
            payload,
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            provider_name=provider_name,
            provider_model=provider_model,
            attempt_number=attempt_number,
            response_hash_reference=response_hash_reference,
            evidence_spans=evidence_spans,
            mapping_schema_version=mapping_schema_version,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProfileDimensionMappingCandidateSet:
        data = _mapping(value, "mapping_candidates")
        allowed = {
            "schema", "schema_version", "rubric_version", "role_catalog_version",
            "profile_fingerprint", "mappings", "directional_signals", "constraints",
            "conflict_warnings", "provider_name", "provider_model",
        }
        _reject_unknown(data, allowed, "mapping_candidates")
        schema_version = data.get("schema_version")
        if data.get("schema") != MAPPING_SCHEMA or schema_version not in SUPPORTED_MAPPING_SCHEMA_VERSIONS:
            raise Phase2ValidationError("unsupported Profile mapping schema version")
        # Typed JSON contains local IDs/fingerprints, unlike provider payloads.
        mappings = tuple(
            _mapping_from_dict(item, index, schema_version=schema_version)
            if schema_version in {1, 2}
            else _binding_from_dict(item, index)
            for index, item in enumerate(
                _list(data.get("mappings"), "mapping_candidates.mappings")
            )
        )
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
            schema_version=schema_version,
        )

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> None:
        rubric.validate(catalog)
        if (
            self.schema != MAPPING_SCHEMA
            or not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version not in SUPPORTED_MAPPING_SCHEMA_VERSIONS
        ):
            raise Phase2ValidationError("unsupported Profile mapping schema version")
        if self.schema_version == 3 and rubric.schema_version != 2:
            raise Phase2ValidationError("Mapping schema 3 requires Capability Rubric schema 2")
        if self.rubric_version != rubric.rubric_version or self.role_catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("mapping candidate version context mismatch")
        if self.profile_fingerprint != profile_fingerprint(profile):
            raise Phase2ValidationError("mapping candidate Profile fingerprint mismatch")
        ids = [
            item.binding_id if isinstance(item, ProfileCriterionEvidenceBinding) else item.mapping_id
            for item in self.mappings
        ]
        if len(ids) != len(set(ids)):
            raise _CodedMappingValidationError(
                _MappingValidationCode.DUPLICATE_MAPPING,
                "mapping candidates contain duplicate mapping IDs",
            )
        if self.schema_version == 3:
            self._validate_schema_three_bindings(profile=profile, rubric=rubric)
        contribution_keys: set[tuple[str, str]] = set()
        primary_by_fact: dict[tuple[str, str], str] = {}
        for item in self.mappings:
            if self.schema_version == 3:
                continue
            assert isinstance(item, ProfileDimensionMappingCandidate)
            item.validate(
                profile=profile, rubric=rubric,
                require_atomic=self.schema_version == 2,
            )
            identities = (
                tuple(locator.evidence_fingerprint for locator in item.atomic_evidence)
                if self.schema_version == 2
                else tuple(_fact_identity(reference) for reference in item.profile_fact_references)
            )
            for fact in identities:
                key = (fact, item.dimension_id)
                if key in contribution_keys:
                    raise _CodedMappingValidationError(
                        _MappingValidationCode.CROSS_DIMENSION_EVIDENCE_REUSE,
                        "duplicate Profile fact contribution to a dimension",
                    )
                contribution_keys.add(key)
                if item.relationship == ContributionRelationship.PRIMARY:
                    role_fact = (item.role_id, fact)
                    previous = primary_by_fact.get(role_fact)
                    if previous is not None and previous != item.dimension_id:
                        raise _CodedMappingValidationError(
                            _MappingValidationCode.CROSS_DIMENSION_EVIDENCE_REUSE,
                            "one broad Profile fact cannot be primary for multiple dimensions",
                        )
                    primary_by_fact[role_fact] = item.dimension_id
        if self.schema_version == 2:
            locators = [
                (item.role_id, locator)
                for item in self.mappings
                for locator in item.atomic_evidence
            ]
            for index, (role_id, left) in enumerate(locators):
                for other_role_id, right in locators[index + 1:]:
                    if role_id != other_role_id or left.profile_reference.path != right.profile_reference.path:
                        continue
                    if max(left.start_offset, right.start_offset) < min(left.end_offset, right.end_offset):
                        raise _CodedMappingValidationError(
                            _MappingValidationCode.OVERLAPPING_ATOMIC_EVIDENCE,
                            "overlapping atomic evidence cannot contribute to multiple Current Fit mappings",
                        )
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
        persisted_report = mapping_validation_report_from_warnings(self.conflict_warnings)
        if self.validation_report is not None and self.validation_report != persisted_report:
            raise Phase2ValidationError("mapping rejection warnings do not match validation report")
        if persisted_report is not None:
            accepted_count = (
                len(self.mappings) + len(self.directional_signals) + len(self.constraints)
            )
            if (
                persisted_report.total_candidate_count
                != accepted_count + persisted_report.rejected_count
            ):
                raise Phase2ValidationError(
                    "mapping rejection counts do not match accepted candidates"
                )
            allowed_paths = set(allowed_profile_reference_paths(profile))
            for rejection in persisted_report.rejections:
                if (
                    rejection.canonical_profile_path is not None
                    and rejection.canonical_profile_path not in allowed_paths
                ):
                    raise Phase2ValidationError("mapping rejection contains an invalid canonical path")

    def _validate_schema_three_bindings(
        self, *, profile: CareerProfile, rubric: CapabilityRubric
    ) -> None:
        bindings: list[ProfileCriterionEvidenceBinding] = []
        for item in self.mappings:
            if not isinstance(item, ProfileCriterionEvidenceBinding):
                raise Phase2ValidationError(
                    "Mapping schema 3 may contain only criterion evidence bindings"
                )
            item.validate(profile=profile, rubric=rubric)
            bindings.append(item)
        evidence_dimension_keys: set[tuple[str, str, str]] = set()
        evidence_role_dimension: dict[tuple[str, str], str] = {}
        for binding in bindings:
            fingerprint = binding.atomic_evidence.evidence_fingerprint
            key = (binding.role_id, fingerprint, binding.dimension_id)
            if key in evidence_dimension_keys:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.DUPLICATE_ATOMIC_EVIDENCE,
                    "one evidence span cannot be bound twice to the same Dimension",
                )
            evidence_dimension_keys.add(key)
            role_fact = (binding.role_id, fingerprint)
            previous = evidence_role_dimension.get(role_fact)
            if previous is not None and previous != binding.dimension_id:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.CROSS_DIMENSION_EVIDENCE_REUSE,
                    "one evidence span cannot contribute across Dimensions for one Role",
                )
            evidence_role_dimension[role_fact] = binding.dimension_id
        for index, left in enumerate(bindings):
            left_locator = left.atomic_evidence
            for right in bindings[index + 1:]:
                right_locator = right.atomic_evidence
                if (
                    left.role_id == right.role_id
                    and left_locator.profile_reference.path
                    == right_locator.profile_reference.path
                    and max(left_locator.start_offset, right_locator.start_offset)
                    < min(left_locator.end_offset, right_locator.end_offset)
                ):
                    raise _CodedMappingValidationError(
                        _MappingValidationCode.OVERLAPPING_ATOMIC_EVIDENCE,
                        "overlapping evidence spans cannot contribute to multiple bindings",
                    )
        skill_names = {
            (
                binding.role_id,
                binding.dimension_id,
                binding.criterion_id,
                _SKILL_NAME_PATH.fullmatch(binding.atomic_evidence.profile_reference.path).group(1),
            )
            for binding in bindings
            if binding.evidence_class == EvidenceClass.SKILL_NAME
        }
        for binding in bindings:
            if binding.evidence_class != EvidenceClass.SKILL_PROFICIENCY:
                continue
            match = _SKILL_PROFICIENCY_PATH.fullmatch(
                binding.atomic_evidence.profile_reference.path
            )
            assert match is not None
            companion = (
                binding.role_id,
                binding.dimension_id,
                binding.criterion_id,
                match.group(1),
            )
            if companion not in skill_names:
                raise _CodedMappingValidationError(
                    _MappingValidationCode.SKILL_PROFICIENCY_WITHOUT_SKILL_NAME,
                    "skill proficiency requires the same skill-name binding",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "schema_version": self.schema_version,
            "rubric_version": self.rubric_version, "role_catalog_version": self.role_catalog_version,
            "profile_fingerprint": self.profile_fingerprint,
            "mappings": [
                item.to_dict()
                if isinstance(item, ProfileCriterionEvidenceBinding)
                else item.to_dict(include_atomic=self.schema_version == 2)
                for item in self.mappings
            ],
            "directional_signals": [item.to_dict() for item in self.directional_signals],
            "constraints": [item.to_dict() for item in self.constraints],
            "conflict_warnings": list(self.conflict_warnings),
            "provider_name": self.provider_name, "provider_model": self.provider_model,
        }


_PROVIDER_MAPPING_FIELDS = {
    "role_id", "dimension_id", "match_status", "profile_fact_references",
    "reasoning", "inference_type", "evidence_strength", "provider_confidence",
    "review_required", "relationship", "suggested_follow_up",
}
_PROVIDER_BINDING_FIELDS = {
    "role_id", "dimension_id", "criterion_id", "span_id",
    "proposed_binding_type", "provider_confidence",
}
_PROVIDER_DIRECTIONAL_FIELDS = {
    "role_id", "signal_type", "status", "profile_fact_references", "reasoning",
    "provider_confidence", "review_required", "suggested_follow_up",
}
_PROVIDER_CONSTRAINT_FIELDS = {
    "role_id", "status", "profile_fact_references", "reasoning",
    "provider_confidence", "review_required", "unknown_constraint_ids",
    "suggested_follow_up",
}


def _safe_context(
    raw: Any,
    *,
    profile: CareerProfile,
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(raw, Mapping):
        return None, None, None
    try:
        role_id = _stable_id(raw.get("role_id"), "rejection.role_id")
    except Phase2ValidationError:
        role_id = None
    try:
        dimension_id = _stable_id(raw.get("dimension_id"), "rejection.dimension_id")
    except Phase2ValidationError:
        dimension_id = None
    canonical_path = None
    references = raw.get("profile_fact_references")
    if isinstance(references, list) and references and isinstance(references[0], Mapping):
        candidate_path = references[0].get("path")
        if isinstance(candidate_path, str):
            try:
                normalized = _canonical_provider_reference_path(candidate_path.strip())
            except Phase2ValidationError:
                normalized = None
            if normalized in set(allowed_profile_reference_paths(profile)):
                canonical_path = normalized
    return role_id, dimension_id, canonical_path


def _precheck_rejection_reason(
    raw: Any,
    *,
    kind: MappingCandidateKind,
    rubric: CapabilityRubric,
    mapping_schema_version: int = 2,
) -> MappingRejectionReason | None:
    if not isinstance(raw, Mapping):
        return {
            MappingCandidateKind.CURRENT_FIT: MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE,
            MappingCandidateKind.DIRECTIONAL: MappingRejectionReason.INVALID_DIRECTIONAL,
            MappingCandidateKind.CONSTRAINT: MappingRejectionReason.INVALID_CONSTRAINT,
        }[kind]
    allowed = {
        MappingCandidateKind.CURRENT_FIT: (
            _PROVIDER_BINDING_FIELDS
            if mapping_schema_version == 3
            else _PROVIDER_MAPPING_FIELDS
        ),
        MappingCandidateKind.DIRECTIONAL: _PROVIDER_DIRECTIONAL_FIELDS,
        MappingCandidateKind.CONSTRAINT: _PROVIDER_CONSTRAINT_FIELDS,
    }[kind]
    if set(raw) - allowed:
        return MappingRejectionReason.PROVIDER_FIELD_INJECTION
    role_id = raw.get("role_id")
    if not isinstance(role_id, str) or role_id not in rubric.supported_role_ids:
        return MappingRejectionReason.UNKNOWN_ROLE
    if kind == MappingCandidateKind.CURRENT_FIT:
        dimension_id = raw.get("dimension_id")
        if not isinstance(dimension_id, str):
            return MappingRejectionReason.UNKNOWN_DIMENSION
        try:
            dimension = rubric.dimension(dimension_id)
        except (KeyError, Phase2ValidationError):
            return MappingRejectionReason.UNKNOWN_DIMENSION
        if dimension.role_id != role_id:
            return MappingRejectionReason.ROLE_DIMENSION_MISMATCH
        if mapping_schema_version == 3:
            return None
        references = raw.get("profile_fact_references")
        reference_paths = (
            tuple(
                item.get("path")
                for item in references
                if isinstance(item, Mapping) and isinstance(item.get("path"), str)
            )
            if isinstance(references, list)
            else ()
        )
        if raw.get("inference_type") == InferenceType.INTEREST_ONLY_DIRECTIONAL.value or any(
            path.removeprefix("career_profile.").startswith("career_preferences.")
            for path in reference_paths
        ):
            return MappingRejectionReason.INTEREST_CURRENT_FIT
    return None


def _reason_from_validation_error(
    error: Exception,
    *,
    raw: Any,
    kind: MappingCandidateKind,
) -> MappingRejectionReason:
    if isinstance(error, _CodedMappingValidationError):
        return _CODED_REJECTION_REASONS[error.code]
    message = str(error).casefold()
    if "interest cannot support current fit" in message:
        return MappingRejectionReason.INTEREST_CURRENT_FIT
    if "value_snapshot does not match" in message:
        return MappingRejectionReason.PROFILE_VALUE_MISMATCH
    if "allowed_profile_references" in message or "unsupported path" in message:
        return MappingRejectionReason.INVALID_PROFILE_REFERENCE
    if "unknown capability" in message:
        return MappingRejectionReason.UNKNOWN_DIMENSION
    if "another role" in message:
        return MappingRejectionReason.ROLE_DIMENSION_MISMATCH
    if "duplicate" in message or "cannot be primary" in message:
        return MappingRejectionReason.DUPLICATE_MAPPING
    if "inference_type" in message:
        return MappingRejectionReason.UNSUPPORTED_INFERENCE
    if "secondary mapping" in message or "relationship" in message:
        return MappingRejectionReason.INVALID_CONTRIBUTION
    if "unknown fields" in message:
        return MappingRejectionReason.PROVIDER_FIELD_INJECTION
    return {
        MappingCandidateKind.CURRENT_FIT: MappingRejectionReason.INVALID_CURRENT_FIT,
        MappingCandidateKind.DIRECTIONAL: MappingRejectionReason.INVALID_DIRECTIONAL,
        MappingCandidateKind.CONSTRAINT: MappingRejectionReason.INVALID_CONSTRAINT,
    }[kind]


def _isolate_provider_candidates(
    payload: Mapping[str, Any],
    *,
    profile: CareerProfile,
    rubric: CapabilityRubric,
    catalog: RoleCatalog,
    provider_name: str,
    provider_model: str,
    attempt_number: int,
    response_hash_reference: str,
    evidence_spans: tuple[CanonicalEvidenceSpan, ...] | None = None,
    mapping_schema_version: int = 2,
) -> ProfileDimensionMappingCandidateSet:
    if mapping_schema_version not in {2, 3}:
        raise Phase2ValidationError("Provider isolation supports Mapping schema 2 or 3")
    data = _mapping(payload, "mapping_candidates")
    _reject_unknown(
        data,
        {"mappings", "directional_signals", "constraints", "conflict_warnings"},
        "mapping_candidates",
    )
    raw_collections = {
        MappingCandidateKind.CURRENT_FIT: data.get("mappings"),
        MappingCandidateKind.DIRECTIONAL: data.get("directional_signals"),
        MappingCandidateKind.CONSTRAINT: data.get("constraints"),
    }
    if not all(isinstance(value, list) for value in raw_collections.values()):
        raise Phase2ValidationError("mapping candidate collections must be lists")
    warnings = _string_tuple(data.get("conflict_warnings"), "mapping_candidates.conflict_warnings")
    if any(
        warning.startswith((MAPPING_REJECTION_WARNING_PREFIX, MAPPING_REJECTION_SUMMARY_PREFIX))
        for warning in warnings
    ):
        raise Phase2ValidationError("Provider cannot inject mapping rejection metadata")

    accepted: dict[MappingCandidateKind, list[Any]] = {
        kind: [] for kind in MappingCandidateKind
    }
    rejections: list[MappingCandidateRejection] = []
    total = sum(len(value) for value in raw_collections.values())
    spans_by_id = (
        None
        if evidence_spans is None
        else {span.span_id: span for span in evidence_spans}
    )
    if spans_by_id is not None and len(spans_by_id) != len(evidence_spans):
        raise Phase2ValidationError("allowed_evidence_spans contains duplicate span IDs")
    for kind in MappingCandidateKind:
        indexed_values = list(enumerate(raw_collections[kind]))
        if (
            mapping_schema_version == 3
            and kind == MappingCandidateKind.CURRENT_FIT
            and spans_by_id is not None
        ):
            def binding_priority(value: tuple[int, Any]) -> tuple[int, int]:
                index, raw = value
                span = spans_by_id.get(raw.get("span_id")) if isinstance(raw, Mapping) else None
                is_skill_name = bool(
                    span is not None and _SKILL_NAME_PATH.fullmatch(span.path)
                )
                return (0 if is_skill_name else 1, index)

            indexed_values.sort(key=binding_priority)
        for index, raw in indexed_values:
            candidate = raw
            reason = None
            if (
                mapping_schema_version == 2
                and kind == MappingCandidateKind.CURRENT_FIT
                and spans_by_id is not None
            ):
                try:
                    candidate = _materialize_current_fit_transport_candidate(
                        raw, profile=profile, spans_by_id=spans_by_id
                    )
                except (TypeError, Phase2ValidationError) as error:
                    reason = _reason_from_validation_error(
                        error, raw=raw, kind=kind
                    )
            elif (
                mapping_schema_version == 2
                and spans_by_id is not None
                and kind != MappingCandidateKind.CURRENT_FIT
            ):
                try:
                    candidate = _materialize_profile_reference_transport_candidate(
                        raw, profile=profile, spans_by_id=spans_by_id
                    )
                except (TypeError, Phase2ValidationError) as error:
                    reason = _reason_from_validation_error(
                        error, raw=raw, kind=kind
                    )
            if reason is None:
                reason = _precheck_rejection_reason(
                    candidate,
                    kind=kind,
                    rubric=rubric,
                    mapping_schema_version=mapping_schema_version,
                )
            if reason is None:
                trial = {
                    "mappings": list(accepted[MappingCandidateKind.CURRENT_FIT]),
                    "directional_signals": list(accepted[MappingCandidateKind.DIRECTIONAL]),
                    "constraints": list(accepted[MappingCandidateKind.CONSTRAINT]),
                    "conflict_warnings": list(warnings),
                }
                key = {
                    MappingCandidateKind.CURRENT_FIT: "mappings",
                    MappingCandidateKind.DIRECTIONAL: "directional_signals",
                    MappingCandidateKind.CONSTRAINT: "constraints",
                }[kind]
                trial[key].append(candidate)
                try:
                    if mapping_schema_version == 3:
                        if evidence_spans is None:
                            raise Phase2ValidationError(
                                "Mapping schema 3 requires canonical evidence spans"
                            )
                        ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
                            trial,
                            profile=profile,
                            rubric=rubric,
                            catalog=catalog,
                            provider_name=provider_name,
                            provider_model=provider_model,
                            evidence_spans=evidence_spans,
                        )
                    else:
                        ProfileDimensionMappingCandidateSet.from_provider_payload(
                            trial,
                            profile=profile,
                            rubric=rubric,
                            catalog=catalog,
                            provider_name=provider_name,
                            provider_model=provider_model,
                        )
                except (TypeError, Phase2ValidationError) as error:
                    reason = _reason_from_validation_error(
                        error, raw=candidate, kind=kind
                    )
            if reason is None:
                accepted[kind].append(candidate)
                continue
            role_id, dimension_id, canonical_path = _safe_context(
                candidate, profile=profile
            )
            rejections.append(MappingCandidateRejection(
                candidate_kind=kind,
                candidate_index=index,
                role_id=role_id,
                dimension_id=dimension_id,
                canonical_profile_path=canonical_path,
                reason_code=reason,
                attempt_number=attempt_number,
                recoverable=True,
                response_hash_reference=response_hash_reference,
            ))

    report = MappingValidationReport(
        attempt_number=attempt_number,
        total_candidate_count=total,
        response_hash_reference=response_hash_reference,
        rejections=tuple(sorted(
            rejections,
            key=lambda value: (value.candidate_kind.value, value.candidate_index),
        )),
    )
    report.validate()
    accepted_payload = {
        "mappings": accepted[MappingCandidateKind.CURRENT_FIT],
        "directional_signals": accepted[MappingCandidateKind.DIRECTIONAL],
        "constraints": accepted[MappingCandidateKind.CONSTRAINT],
        "conflict_warnings": [*warnings, *report.warning_tokens()],
    }
    if mapping_schema_version == 3:
        if evidence_spans is None:
            raise Phase2ValidationError("Mapping schema 3 requires canonical evidence spans")
        result = ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
            accepted_payload,
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            provider_name=provider_name,
            provider_model=provider_model,
            evidence_spans=evidence_spans,
        )
    else:
        result = ProfileDimensionMappingCandidateSet.from_provider_payload(
            accepted_payload,
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            provider_name=provider_name,
            provider_model=provider_model,
        )
    result = replace(result, validation_report=report if report.rejections else None)
    result.validate(profile=profile, rubric=rubric, catalog=catalog)
    return result


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


def _mapping_from_dict(
    value: Any, index: int, *, schema_version: int
) -> ProfileDimensionMappingCandidate:
    path = f"mapping_candidates.mappings[{index}]"; data = _mapping(value, path)
    allowed = {"mapping_id", "role_id", "dimension_id", "match_status", "profile_fact_references", "reasoning", "inference_type", "evidence_strength", "provider_confidence", "review_required", "relationship", "suggested_follow_up"}
    if schema_version == 2:
        allowed.add("atomic_evidence")
    _reject_unknown(data, allowed, path)
    locators = () if schema_version == 1 else tuple(
        AtomicEvidenceLocator.from_dict(item, f"{path}.atomic_evidence[{item_index}]")
        for item_index, item in enumerate(
            _list(data.get("atomic_evidence"), f"{path}.atomic_evidence")
        )
    )
    return ProfileDimensionMappingCandidate(
        mapping_id=_stable_id(data.get("mapping_id"), f"{path}.mapping_id"), role_id=_stable_id(data.get("role_id"), f"{path}.role_id"), dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
        match_status=_enum(data.get("match_status"), CurrentMatchStatus, f"{path}.match_status"),
        profile_fact_references=_typed_references(data.get("profile_fact_references"), f"{path}.profile_fact_references"),
        reasoning=_text(data.get("reasoning"), f"{path}.reasoning"), inference_type=_enum(data.get("inference_type"), InferenceType, f"{path}.inference_type"), evidence_strength=_enum(data.get("evidence_strength"), EvidenceStrength, f"{path}.evidence_strength"), provider_confidence=_enum(data.get("provider_confidence"), ProviderConfidence, f"{path}.provider_confidence"), review_required=_boolean(data.get("review_required"), f"{path}.review_required"), relationship=_enum(data.get("relationship"), ContributionRelationship, f"{path}.relationship"), suggested_follow_up=_text(data.get("suggested_follow_up"), f"{path}.suggested_follow_up", required=False),
        atomic_evidence=locators,
    )


def _binding_from_dict(value: Any, index: int) -> ProfileCriterionEvidenceBinding:
    path = f"mapping_candidates.mappings[{index}]"
    data = _mapping(value, path)
    _reject_unknown(
        data,
        {
            "binding_id", "role_id", "dimension_id", "criterion_id",
            "atomic_evidence", "evidence_class", "trust_level",
            "proposed_binding_type", "provider_confidence", "derived_match_status",
            "derived_evidence_strength", "derived_inference_type",
            "derived_review_required", "derivation_reason_codes",
        },
        path,
    )
    return ProfileCriterionEvidenceBinding(
        binding_id=_stable_id(data.get("binding_id"), f"{path}.binding_id"),
        role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
        dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
        criterion_id=_stable_id(data.get("criterion_id"), f"{path}.criterion_id"),
        atomic_evidence=AtomicEvidenceLocator.from_dict(
            data.get("atomic_evidence"), f"{path}.atomic_evidence"
        ),
        evidence_class=_enum(
            data.get("evidence_class"), EvidenceClass, f"{path}.evidence_class"
        ),
        trust_level=_enum(
            data.get("trust_level"), EvidenceTrustLevel, f"{path}.trust_level"
        ),
        proposed_binding_type=_enum(
            data.get("proposed_binding_type"),
            ProposedBindingType,
            f"{path}.proposed_binding_type",
        ),
        provider_confidence=_enum(
            data.get("provider_confidence"),
            ProviderConfidence,
            f"{path}.provider_confidence",
        ),
        derived_match_status=_enum(
            data.get("derived_match_status"),
            CurrentMatchStatus,
            f"{path}.derived_match_status",
        ),
        derived_evidence_strength=_enum(
            data.get("derived_evidence_strength"),
            EvidenceStrength,
            f"{path}.derived_evidence_strength",
        ),
        derived_inference_type=_enum(
            data.get("derived_inference_type"),
            InferenceType,
            f"{path}.derived_inference_type",
        ),
        derived_review_required=_boolean(
            data.get("derived_review_required"), f"{path}.derived_review_required"
        ),
        derivation_reason_codes=tuple(
            _enum(item, BindingDerivationReason, f"{path}.derivation_reason_codes[{item_index}]")
            for item_index, item in enumerate(
                _string_tuple(
                    data.get("derivation_reason_codes"),
                    f"{path}.derivation_reason_codes",
                    ids=True,
                )
            )
        ),
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
            "profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"span_id":{"type":"string"}},"required":["span_id"]}},
            "reasoning":{"type":"string"},"inference_type":{"type":"string","enum":[x.value for x in CURRENT_FIT_INFERENCE_TYPES]},"evidence_strength":{"type":"string","enum":[x.value for x in EvidenceStrength]},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"relationship":{"type":"string","enum":[x.value for x in ContributionRelationship]},"suggested_follow_up":{"type":["string","null"]}},
            "required":["role_id","dimension_id","match_status","profile_fact_references","reasoning","inference_type","evidence_strength","provider_confidence","review_required","relationship","suggested_follow_up"]}},
        "directional_signals":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"role_id":{"type":"string"},"signal_type":{"type":"string","enum":[x.value for x in DirectionalSignalType]},"status":{"type":"string","enum":[x.value for x in DirectionalStatus]},"profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"span_id":{"type":"string"}},"required":["span_id"]}},"reasoning":{"type":"string"},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"suggested_follow_up":{"type":["string","null"]}},"required":["role_id","signal_type","status","profile_fact_references","reasoning","provider_confidence","review_required","suggested_follow_up"]}},
        "constraints":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"role_id":{"type":"string"},"status":{"type":"string","enum":[x.value for x in MappingConstraintStatus]},"profile_fact_references":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"span_id":{"type":"string"}},"required":["span_id"]}},"reasoning":{"type":"string"},"provider_confidence":{"type":"string","enum":[x.value for x in ProviderConfidence]},"review_required":{"type":"boolean"},"unknown_constraint_ids":{"type":"array","items":{"type":"string"}},"suggested_follow_up":{"type":["string","null"]}},"required":["role_id","status","profile_fact_references","reasoning","provider_confidence","review_required","unknown_constraint_ids","suggested_follow_up"]}},
        "conflict_warnings":{"type":"array","items":{"type":"string"}},
    },
    "required":["mappings","directional_signals","constraints","conflict_warnings"],
}

PROVIDER_BINDING_SCHEMA: dict[str, Any] = deepcopy(PROVIDER_MAPPING_SCHEMA)
PROVIDER_BINDING_SCHEMA["properties"]["mappings"] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "role_id": {"type": "string"},
            "dimension_id": {"type": "string"},
            "criterion_id": {"type": "string"},
            "span_id": {"type": "string"},
            "proposed_binding_type": {
                "type": "string",
                "enum": [item.value for item in ProposedBindingType],
            },
            "provider_confidence": {
                "type": "string",
                "enum": [item.value for item in ProviderConfidence],
            },
        },
        "required": [
            "role_id", "dimension_id", "criterion_id", "span_id",
            "proposed_binding_type", "provider_confidence",
        ],
    },
}


def provider_mapping_schema(
    rubric: CapabilityRubric,
    profile: CareerProfile,
    *,
    mapping_schema_version: int = 2,
) -> dict[str, Any]:
    """Return the Provider schema with role IDs constrained to this Rubric."""
    if mapping_schema_version not in {2, 3}:
        raise Phase2ValidationError("Provider schema supports Mapping schema 2 or 3")
    if mapping_schema_version == 3 and rubric.schema_version != 2:
        raise Phase2ValidationError("Mapping schema 3 requires Capability Rubric schema 2")
    role_ids = list(rubric.supported_role_ids)
    if not role_ids:
        raise Phase2ValidationError("Capability Rubric must support at least one role")
    schema = deepcopy(
        PROVIDER_BINDING_SCHEMA if mapping_schema_version == 3 else PROVIDER_MAPPING_SCHEMA
    )
    for collection in ("mappings", "directional_signals", "constraints"):
        schema["properties"][collection]["items"]["properties"]["role_id"] = {
            "type": "string",
            "enum": role_ids,
        }
    span_ids = [span.span_id for span in canonical_evidence_span_inventory(profile)]
    for collection in ("directional_signals", "constraints"):
        schema["properties"][collection]["items"]["properties"][
            "profile_fact_references"
        ]["items"]["properties"]["span_id"] = {
            "type": "string",
            "enum": span_ids,
        }
    if mapping_schema_version == 2:
        schema["properties"]["mappings"]["items"]["properties"][
            "profile_fact_references"
        ]["items"]["properties"]["span_id"] = {
            "type": "string",
            "enum": span_ids,
        }
    else:
        schema["properties"]["mappings"]["items"]["properties"]["span_id"] = {
            "type": "string",
            "enum": span_ids,
        }
        schema["properties"]["mappings"]["items"]["properties"]["dimension_id"] = {
            "type": "string",
            "enum": [dimension.dimension_id for dimension in rubric.dimensions],
        }
        schema["properties"]["mappings"]["items"]["properties"]["criterion_id"] = {
            "type": "string",
            "enum": [
                criterion_id
                for dimension in rubric.dimensions
                for criterion_id in dimension.criterion_ids
            ],
        }
    return schema


MAPPING_INSTRUCTIONS = """Map only facts present in the supplied CareerProfile to the supplied capability dimensions.
Values in career_profile.career_preferences.currently_considered_roles are user-entered job names and preferences, not canonical role IDs. Never copy those free-text values into role_id.
Every output role_id must be selected exactly from the supplied allowed_canonical_role_ids and the role IDs defined by the Rubric. Choose semantically among those canonical roles; do not invent, normalize, or alias a new role ID.
Every Profile fact reference must contain only one span_id selected from allowed_evidence_spans. Never copy or invent a path, value_snapshot, excerpt, offset, fingerprint, or custom span. Python materializes the selected canonical span into persisted evidence provenance.
The career_profile key is only the input JSON transport envelope. Paths shown in allowed_evidence_spans are canonical paths relative to that object, but Provider output must contain only the selected span_id.
Preserve unknowns. Interest and career goals may affect directional signals only, never Current Fit.
Career interests, target roles, desired growth, preferred work content, and statements about wanting to learn must never appear in a Current Fit mapping, regardless of match status. Remove such a mapping; do not convert it into a Directional Signal.
Current Fit inference_type and review_required must use exactly one of these combinations: direct with review_required false or true; bounded_semantic with review_required true; adjacent_transfer with review_required true. interest_only_directional is never a Current Fit inference_type.
Education does not prove tool proficiency, and a project title does not prove unstated project content.
Reasoning must not claim a concrete tool, workflow, retrieval/RAG capability, or achievement that the selected canonical span does not directly support.
Do not calculate scores, bands, ranks, weights, confidence results, decisions, gaps, IDs, fingerprints, artifact status, or review provenance.
Allowed top-level fields are mappings, directional_signals, constraints, and conflict_warnings.
Do not add fields outside the supplied JSON Schema. Return exactly one complete JSON object.
Use double-quoted JSON property names and strings. Do not emit trailing commas, comments, Markdown fences, or explanatory text."""

MAPPING_V3_INSTRUCTIONS = """Propose atomic Profile evidence bindings to the supplied Capability Rubric criteria.
For every Current Fit binding, output only role_id, dimension_id, criterion_id, span_id, proposed_binding_type, and provider_confidence. Select role, Dimension, criterion, and span IDs exactly from the supplied inventories.
The Provider proposes only whether the evidence appears direct, bounded_semantic, or adjacent_transfer. Python determines evidence_class, trust_level, final inference, match status, evidence strength, review requirement, stable IDs, fingerprints, and derivation reasons from the validated Profile and Rubric policy.
Never output binding_id, path, value_snapshot, excerpt, offset, fingerprint, evidence_class, trust_level, match_status, evidence_strength, inference_type, review_required, relationship, score, rank, recommendation, Decision, or review provenance in a Current Fit binding.
Career interests, target roles, desired growth, preferred work content, and statements about wanting to learn may support Directional Fit only. They must never be submitted as Current Fit evidence bindings.
Education does not prove implementation, systems, production, evaluation, or delivery capability. Select only criterion bindings directly supported by the chosen atomic span.
Directional signals and constraints retain the exact supplied schema and safety semantics. Their Profile references contain only one selected span_id.
Preserve unknowns. Do not calculate scores, bands, ranks, weights, recommendation confidence, decisions, gaps, stable IDs, artifact status, or review provenance.
Allowed top-level fields are mappings, directional_signals, constraints, and conflict_warnings.
Do not add fields outside the supplied JSON Schema. Return exactly one complete JSON object with double-quoted property names and no Markdown or explanatory text."""


class MappingProviderOutputError(TypeError):
    """Provider output is not exactly one parseable mapping object."""


def _single_json_fence(value: str) -> str | None:
    stripped = value.strip()
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].strip().casefold() == "```json" and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return None


def _balanced_json_objects(value: str) -> tuple[str, ...]:
    objects: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(value):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(value[start:index + 1])
                start = None
    return tuple(objects)


def parse_mapping_provider_output(output: Any) -> dict[str, Any]:
    """Parse one JSON object without repairing malformed Provider syntax."""
    if not isinstance(output, str):
        raise TypeError("Provider content must be a string")
    stripped = output.strip()
    if not stripped:
        raise MappingProviderOutputError("Provider returned empty content")
    fenced = _single_json_fence(stripped)
    candidate = fenced if fenced is not None else stripped
    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as direct_error:
        if fenced is not None:
            raise
        objects = _balanced_json_objects(stripped)
        if len(objects) != 1:
            if len(objects) > 1:
                raise MappingProviderOutputError(
                    "Provider returned multiple JSON objects"
                ) from direct_error
            raise
        result = json.loads(objects[0])
    if not isinstance(result, dict):
        raise MappingProviderOutputError("Provider mapping top level must be a JSON object")
    return result


def _json_mode_rejected(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    message = str(error).casefold()
    return status in {400, 422} and any(
        marker in message
        for marker in ("response_format", "json_object", "json mode", "response format")
    )


def _chat_message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text)
        if len(texts) == 1:
            return texts[0]
        if len(texts) > 1:
            raise MappingProviderOutputError("Provider returned multiple content blocks")
    raise MappingProviderOutputError("Provider returned empty content")


@dataclass(frozen=True)
class _ProviderResponse:
    content: str
    provider_path: str
    response_format_mode: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class _MappingAttemptQuality:
    total_candidate_count: int
    rejected_count: int
    accepted_candidate_keys: frozenset[tuple[Any, ...]]
    canonical_role_ids: frozenset[str]
    current_fit_dimensions: frozenset[tuple[str, str]]


def _accepted_candidate_keys(
    value: ProfileDimensionMappingCandidateSet,
) -> frozenset[tuple[Any, ...]]:
    keys: set[tuple[Any, ...]] = set()
    for item in value.mappings:
        if isinstance(item, ProfileCriterionEvidenceBinding):
            keys.add((
                "criterion_binding",
                item.role_id,
                item.dimension_id,
                item.criterion_id,
                item.atomic_evidence.evidence_fingerprint,
                item.proposed_binding_type.value,
                item.provider_confidence.value,
            ))
        else:
            keys.add((
                "current_fit", item.role_id, item.dimension_id, item.match_status.value,
                item.relationship.value, item.inference_type.value,
                item.evidence_strength.value, item.review_required,
                tuple(sorted(locator.evidence_fingerprint for locator in item.atomic_evidence)),
            ))
    for item in value.directional_signals:
        keys.add((
            "directional", item.role_id, item.signal_type.value, item.status.value,
            tuple(sorted(
                (reference.path, reference.profile_fingerprint)
                for reference in item.profile_fact_references
            )),
            item.review_required,
        ))
    for item in value.constraints:
        keys.add((
            "constraint", item.role_id, item.status.value,
            tuple(sorted(
                (reference.path, reference.profile_fingerprint)
                for reference in item.profile_fact_references
            )),
            tuple(sorted(item.unknown_constraint_ids)), item.review_required,
        ))
    return frozenset(keys)


def _mapping_attempt_quality(
    value: ProfileDimensionMappingCandidateSet,
) -> _MappingAttemptQuality:
    accepted_count = (
        len(value.mappings) + len(value.directional_signals) + len(value.constraints)
    )
    report = value.validation_report
    rejected_count = 0 if report is None else report.rejected_count
    total_count = (
        accepted_count if report is None else report.total_candidate_count
    )
    role_ids = {
        *(item.role_id for item in value.mappings),
        *(item.role_id for item in value.directional_signals),
        *(item.role_id for item in value.constraints),
    }
    return _MappingAttemptQuality(
        total_candidate_count=total_count,
        rejected_count=rejected_count,
        accepted_candidate_keys=_accepted_candidate_keys(value),
        canonical_role_ids=frozenset(role_ids),
        current_fit_dimensions=frozenset(
            (item.role_id, item.dimension_id) for item in value.mappings
        ),
    )


def _accepted_retry_candidates(
    payload: Mapping[str, Any], report: MappingValidationReport
) -> dict[str, list[Any]]:
    """Return the first attempt's accepted Provider candidates in transport form."""
    rejected: dict[MappingCandidateKind, set[int]] = {
        kind: set() for kind in MappingCandidateKind
    }
    for item in report.rejections:
        rejected[item.candidate_kind].add(item.candidate_index)
    collection_by_kind = {
        MappingCandidateKind.CURRENT_FIT: "mappings",
        MappingCandidateKind.DIRECTIONAL: "directional_signals",
        MappingCandidateKind.CONSTRAINT: "constraints",
    }
    accepted: dict[str, list[Any]] = {}
    for kind, collection in collection_by_kind.items():
        values = payload.get(collection)
        if not isinstance(values, list):
            raise Phase2ValidationError("mapping candidate collections must be lists")
        accepted[collection] = [
            deepcopy(value)
            for index, value in enumerate(values)
            if index not in rejected[kind]
        ]
    return accepted


def _select_retry_result(
    first: ProfileDimensionMappingCandidateSet,
    second: ProfileDimensionMappingCandidateSet,
) -> tuple[ProfileDimensionMappingCandidateSet, str, int]:
    """Select one whole attempt; never merge Provider responses."""
    first_quality = _mapping_attempt_quality(first)
    second_quality = _mapping_attempt_quality(second)
    if not first_quality.canonical_role_ids.issubset(second_quality.canonical_role_ids):
        return first, "attempt_1_retained_role_coverage_decreased", 1
    if not first_quality.current_fit_dimensions.issubset(
        second_quality.current_fit_dimensions
    ):
        return first, "attempt_1_retained_dimension_coverage_decreased", 1
    if not first_quality.accepted_candidate_keys.issubset(
        second_quality.accepted_candidate_keys
    ):
        return first, "attempt_1_retained_accepted_candidates_lost", 1
    if second_quality.total_candidate_count > first_quality.total_candidate_count:
        return first, "attempt_1_retained_candidate_volume_increased", 1
    if second_quality.rejected_count >= first_quality.rejected_count:
        return first, "attempt_1_retained_rejection_count_not_lower", 1
    first_denominator = max(first_quality.total_candidate_count, 1)
    second_denominator = max(second_quality.total_candidate_count, 1)
    if (
        second_quality.rejected_count * first_denominator
        >= first_quality.rejected_count * second_denominator
    ):
        return first, "attempt_1_retained_rejection_ratio_not_improved", 1
    return second, "attempt_2_selected_strictly_improved", 2


class ProfileDimensionMapper(Protocol):
    def map(self, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> ProfileDimensionMappingCandidateSet: ...


class OpenAIProfileDimensionMapper:
    def __init__(
        self,
        *,
        settings: LLMSettings | None = None,
        client: Any | None = None,
        max_attempts: int = 2,
        capabilities: ProviderCapabilities | None = None,
        diagnostics_dir: str | Path | None = None,
        mapping_schema_version: int = 2,
    ) -> None:
        self.settings = settings or LLMSettings.from_environment()
        self.client = client or create_openai_client(self.settings)
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts must be 1 or 2")
        self.max_attempts = max_attempts
        self.capabilities = capabilities or provider_capabilities(self.settings)
        if mapping_schema_version not in {2, 3}:
            raise ValueError("mapping_schema_version must be 2 or 3")
        self.mapping_schema_version = mapping_schema_version
        self.attempt_diagnostics: list[dict[str, Any]] = []
        self._active_response_format_mode = self.capabilities.response_format_mode
        self._active_fallback_reason: str | None = None
        self._json_object_rejected = False
        self.diagnostics = (
            None
            if diagnostics_dir is None
            else ProviderDiagnosticsWriter(diagnostics_dir, api_key=self.settings.api_key)
        )

    def map(self, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> ProfileDimensionMappingCandidateSet:
        self.attempt_diagnostics = []
        self._json_object_rejected = False
        allowed_role_ids = tuple(rubric.supported_role_ids)
        evidence_spans = canonical_evidence_span_inventory(profile)
        response_schema = provider_mapping_schema(
            rubric, profile, mapping_schema_version=self.mapping_schema_version
        )
        if self.mapping_schema_version == 3 and rubric.schema_version != 2:
            raise Phase2ValidationError("Mapping schema 3 requires Capability Rubric schema 2")
        rubric_roles = []
        for role in allowed_role_ids:
            dimensions = []
            for dimension in rubric.role_dimensions(role):
                item: dict[str, Any] = {
                    "dimension_id": dimension.dimension_id,
                    "name": dimension.name,
                    "description": dimension.description,
                    "exclusion_criteria": list(dimension.exclusion_criteria),
                }
                if self.mapping_schema_version == 3:
                    item["criteria"] = [criterion.to_dict() for criterion in dimension.criteria]
                else:
                    item["inclusion_criteria"] = list(dimension.inclusion_criteria)
                dimensions.append(item)
            rubric_roles.append({"role_id": role, "dimensions": dimensions})
        payload = json.dumps(
            {
                "career_profile": profile.to_dict(),
                "allowed_evidence_spans": [
                    span.to_transport_dict() for span in evidence_spans
                ],
                "allowed_canonical_role_ids": list(allowed_role_ids),
                "rubric": {
                    "rubric_version": rubric.rubric_version,
                    "roles": rubric_roles,
                },
            },
            ensure_ascii=False,
        )
        last: Exception | None = None
        repair_error: str | None = None
        retry_baseline: ProfileDimensionMappingCandidateSet | None = None
        retry_accepted_candidates: dict[str, list[Any]] | None = None
        for attempt in range(1, self.max_attempts + 1):
            response: _ProviderResponse | None = None
            validation_report: MappingValidationReport | None = None
            try:
                response = self._request(
                    payload,
                    repair_error=repair_error,
                    allowed_role_ids=allowed_role_ids,
                    response_schema=response_schema,
                    accepted_candidates=retry_accepted_candidates,
                )
                parsed = parse_mapping_provider_output(response.content)
                response_hash = "sha256:" + hashlib.sha256(
                    response.content.encode("utf-8")
                ).hexdigest()
                result = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
                    parsed,
                    profile=profile,
                    rubric=rubric,
                    catalog=catalog,
                    provider_name="openai_compatible",
                    provider_model=self.settings.model,
                    attempt_number=attempt,
                    response_hash_reference=response_hash,
                    evidence_spans=evidence_spans,
                    mapping_schema_version=self.mapping_schema_version,
                )
                validation_report = result.validation_report
                if validation_report and validation_report.rejections and attempt < self.max_attempts:
                    retry_baseline = result
                    retry_accepted_candidates = _accepted_retry_candidates(
                        parsed, validation_report
                    )
                    repair_error = (
                        "Recoverable candidate validation failed: "
                        + ", ".join(validation_report.reason_codes)
                    )
                    self._write_diagnostics(
                        attempt=attempt,
                        response=response,
                        error=repair_error,
                        validation_report=validation_report,
                    )
                    continue
                if retry_baseline is not None:
                    selected, selection_reason, selected_attempt = _select_retry_result(
                        retry_baseline, result
                    )
                    self._write_diagnostics(
                        attempt=attempt,
                        response=response,
                        error=None,
                        validation_report=validation_report,
                        retry_selection_reason=selection_reason,
                        selected_attempt=selected_attempt,
                    )
                    self._annotate_retry_selection(
                        reason=selection_reason, selected_attempt=selected_attempt
                    )
                    return selected
                self._write_diagnostics(
                    attempt=attempt,
                    response=response,
                    error=None,
                    validation_report=validation_report,
                )
                return result
            except (json.JSONDecodeError, MappingProviderOutputError, TypeError, Phase2ValidationError) as error:
                last = error
                repair_error = f"{type(error).__name__}: {error}"
                selection_reason = (
                    "attempt_1_retained_retry_invalid"
                    if retry_baseline is not None and attempt == self.max_attempts
                    else None
                )
                self._write_diagnostics(
                    attempt=attempt,
                    response=response,
                    error=repair_error,
                    validation_report=validation_report,
                    retry_selection_reason=selection_reason,
                    selected_attempt=1 if selection_reason else None,
                )
                if selection_reason is not None:
                    self._annotate_retry_selection(
                        reason=selection_reason, selected_attempt=1
                    )
                    return retry_baseline
            except LLMRequestError as error:
                selection_reason = (
                    "attempt_1_retained_retry_request_failed"
                    if retry_baseline is not None and attempt == self.max_attempts
                    else None
                )
                self._write_diagnostics(
                    attempt=attempt,
                    response=response,
                    error=str(error),
                    validation_report=validation_report,
                    retry_selection_reason=selection_reason,
                    selected_attempt=1 if selection_reason else None,
                )
                if selection_reason is not None:
                    self._annotate_retry_selection(
                        reason=selection_reason, selected_attempt=1
                    )
                    return retry_baseline
                raise
        raise LLMRequestError(
            "Role mapping provider output was invalid after "
            f"{self.max_attempts} attempts. The Provider must return one complete JSON object. "
            f"Last validation error: {last}"
        )

    def _request(
        self,
        payload: str,
        *,
        repair_error: str | None,
        allowed_role_ids: tuple[str, ...],
        response_schema: dict[str, Any],
        accepted_candidates: dict[str, list[Any]] | None,
    ) -> _ProviderResponse:
        instructions = self._instructions(
            repair_error,
            allowed_role_ids=allowed_role_ids,
            response_schema=response_schema,
            accepted_candidates=accepted_candidates,
        )
        self._active_response_format_mode = self.capabilities.response_format_mode
        self._active_fallback_reason = None
        try:
            if self.capabilities.protocol == "responses":
                response=self.client.responses.create(model=self.settings.model,instructions=instructions,input=payload,text={"format":{"type":"json_schema","name":"profile_dimension_mapping_candidates","strict":True,"schema":response_schema}},store=False)
                content=getattr(response,"output_text",None)
                if not isinstance(content,str) or not content.strip():
                    raise MappingProviderOutputError("Provider returned empty content")
                return _ProviderResponse(content,"responses","json_schema")
            return self._chat_request(payload,instructions)
        except (MappingProviderOutputError, LLMRequestError):
            raise
        except Exception as error:
            raise safe_llm_error(error) from error

    def _chat_request(self, payload: str, instructions: str) -> _ProviderResponse:
        messages=[{"role":"system","content":instructions},{"role":"user","content":payload}]
        kwargs: dict[str, Any] = {"model":self.settings.model,"messages":messages}
        if self.capabilities.response_format_mode == "json_object" and not self._json_object_rejected:
            kwargs["response_format"]={"type":"json_object"}
        elif self._json_object_rejected:
            self._active_response_format_mode="prompt_only"
            self._active_fallback_reason="provider_rejected_json_object_mode"
        if self.capabilities.disable_thinking:
            kwargs["extra_body"]={"enable_thinking":False}
        try:
            completion=self.client.chat.completions.create(**kwargs)
            content=_chat_message_content(completion.choices[0].message)
            return _ProviderResponse(
                content,
                "chat_completions",
                self._active_response_format_mode,
                self._active_fallback_reason,
            )
        except MappingProviderOutputError:
            raise
        except Exception as error:
            if not (
                self.capabilities.allow_prompt_only_fallback
                and self.capabilities.response_format_mode == "json_object"
                and not self._json_object_rejected
                and _json_mode_rejected(error)
            ):
                raise safe_llm_error(error) from error
            fallback_reason="provider_rejected_json_object_mode"
            self._json_object_rejected=True
            self._active_response_format_mode="prompt_only"
            self._active_fallback_reason=fallback_reason
            fallback_kwargs={"model":self.settings.model,"messages":messages}
            if self.capabilities.disable_thinking:
                fallback_kwargs["extra_body"]={"enable_thinking":False}
            try:
                completion=self.client.chat.completions.create(**fallback_kwargs)
                content=_chat_message_content(completion.choices[0].message)
            except MappingProviderOutputError:
                raise
            except Exception as fallback_error:
                raise safe_llm_error(fallback_error) from fallback_error
            return _ProviderResponse(content,"chat_completions","prompt_only",fallback_reason)

    def _instructions(
        self,
        repair_error: str | None,
        *,
        allowed_role_ids: tuple[str, ...],
        response_schema: dict[str, Any],
        accepted_candidates: dict[str, list[Any]] | None,
    ) -> str:
        schema=json.dumps(response_schema,ensure_ascii=False,separators=(",",":"))
        allowed_roles=json.dumps(list(allowed_role_ids),ensure_ascii=False,separators=(",",":"))
        instructions = (
            MAPPING_V3_INSTRUCTIONS
            if self.mapping_schema_version == 3
            else MAPPING_INSTRUCTIONS
        )
        base=(
            f"{instructions}\nAllowed canonical role IDs: {allowed_roles}. "
            f"The exact allowed JSON Schema is: {schema}"
        )
        if repair_error is None:
            return base
        accepted_guidance = (
            "\nThe following candidates from the first response already passed validation. "
            "The complete retry response must include every one of them unchanged. Correct only "
            "the rejected candidates; do not delete, replace, or weaken accepted candidates: "
            + json.dumps(
                accepted_candidates,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if accepted_candidates is not None
            else ""
        )
        interest_guidance = (
            "\nCareer interests and target roles may only support Directional Fit. "
            "They must not be cited by any Current Fit mapping. Delete that Current Fit "
            "candidate, or use a legal evidence span only when it independently demonstrates "
            "the capability. Never rewrite an interest as demonstrated ability."
            if MappingRejectionReason.INTEREST_CURRENT_FIT.value in repair_error
            else ""
        )
        unknown_role_guidance = (
            "\nThe previous response used an unsupported role_id. User-entered job names in "
            "currently_considered_roles are not canonical IDs. Resubmit the complete response "
            f"using only these canonical role IDs: {allowed_roles}."
            if MappingRejectionReason.UNKNOWN_ROLE.value in repair_error
            else ""
        )
        atomic_guidance: list[str] = []
        if MappingRejectionReason.INVALID_EXACT_EXCERPT.value in repair_error:
            atomic_guidance.append(
                "Select a valid span_id from allowed_evidence_spans; do not copy or invent evidence text."
            )
        if MappingRejectionReason.INVALID_EVIDENCE_TOKEN_BOUNDARY.value in repair_error:
            atomic_guidance.append(
                "Expand exact_excerpt to complete token boundaries; do not submit a substring cut from inside a word."
            )
        if MappingRejectionReason.DUPLICATE_ATOMIC_EVIDENCE.value in repair_error:
            atomic_guidance.append(
                "Include each atomic evidence excerpt only once within a mapping."
            )
        if any(
            code in repair_error
            for code in (
                MappingRejectionReason.OVERLAPPING_ATOMIC_EVIDENCE.value,
                MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE.value,
            )
        ):
            atomic_guidance.append(
                "Use independent, non-overlapping excerpts for different capability dimensions; do not reuse one fact."
            )
        if MappingRejectionReason.INVALID_MATCH_STATUS.value in repair_error:
            atomic_guidance.append(
                "Use only a match_status value allowed by the supplied JSON Schema."
            )
        if MappingRejectionReason.INVALID_STATUS_EVIDENCE.value in repair_error:
            atomic_guidance.append(
                "A positive Current Fit status requires validated Profile evidence; otherwise use unknown without invented evidence."
            )
        if MappingRejectionReason.INVALID_INFERENCE_REVIEW.value in repair_error:
            atomic_guidance.append(
                "bounded_semantic and adjacent_transfer must use review_required=true; direct may use review_required=false or review_required=true."
            )
        if MappingRejectionReason.UNSUPPORTED_INFERENCE.value in repair_error:
            atomic_guidance.append(
                "For Current Fit, select inference_type only from direct, bounded_semantic, or adjacent_transfer."
            )
        if MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE.value in repair_error:
            atomic_guidance.append(
                "Rebuild each Current Fit Candidate with every required field and the exact JSON types in the supplied schema."
            )
        if MappingRejectionReason.INVALID_EVIDENCE_STRENGTH.value in repair_error:
            atomic_guidance.append(
                "Use only an evidence_strength value allowed by the supplied JSON Schema."
            )
        if MappingRejectionReason.INVALID_PROVIDER_CONFIDENCE.value in repair_error:
            atomic_guidance.append(
                "Use only a provider_confidence value allowed by the supplied JSON Schema."
            )
        if MappingRejectionReason.INVALID_REVIEW_FLAG.value in repair_error:
            atomic_guidance.append("review_required must be a JSON boolean.")
        if MappingRejectionReason.INVALID_CONTRIBUTION.value in repair_error:
            atomic_guidance.append(
                "Use a valid primary or secondary relationship; secondary evidence cannot claim strong strength."
            )
        if MappingRejectionReason.DUPLICATE_MAPPING.value in repair_error:
            atomic_guidance.append(
                "Do not submit the same Role, Dimension, status, relationship, and evidence mapping more than once."
            )
        if MappingRejectionReason.INVALID_MAPPING_PROVENANCE.value in repair_error:
            atomic_guidance.append(
                "Resubmit the complete Candidate from the supplied Profile and schema without local IDs or derived provenance."
            )
        if MappingRejectionReason.UNKNOWN_CRITERION.value in repair_error:
            atomic_guidance.append(
                "Select criterion_id only from the criteria supplied in the Capability Rubric inventory."
            )
        if MappingRejectionReason.CRITERION_DIMENSION_MISMATCH.value in repair_error:
            atomic_guidance.append(
                "Select a criterion_id that belongs to the submitted dimension_id."
            )
        if MappingRejectionReason.FORBIDDEN_EVIDENCE_CLASS.value in repair_error:
            atomic_guidance.append(
                "Remove the binding or choose evidence whose Profile object type is allowed by that Dimension policy."
            )
        if MappingRejectionReason.UNSUPPORTED_EVIDENCE_CLASS.value in repair_error:
            atomic_guidance.append(
                "Current Fit evidence must be a skill name, paired skill proficiency, education field, experience summary, or project summary."
            )
        if MappingRejectionReason.SKILL_PROFICIENCY_WITHOUT_SKILL_NAME.value in repair_error:
            atomic_guidance.append(
                "A skill proficiency span requires a separate binding for the same skill record's skill_name and the same Role, Dimension, and criterion."
            )
        if MappingRejectionReason.INVALID_BINDING_TYPE.value in repair_error:
            atomic_guidance.append(
                "proposed_binding_type must be direct, bounded_semantic, or adjacent_transfer."
            )
        if MappingRejectionReason.INVALID_BINDING_PROVENANCE.value in repair_error:
            atomic_guidance.append(
                "Do not submit local binding IDs, derived fields, or review provenance; submit only the six allowed binding fields."
            )
        atomic_repair_guidance = (
            "\nAtomic evidence corrections: " + " ".join(atomic_guidance)
            if atomic_guidance
            else ""
        )
        transport_guidance = (
            "For every Current Fit binding, submit only role_id, dimension_id, criterion_id, "
            "span_id, proposed_binding_type, and provider_confidence. "
            if self.mapping_schema_version == 3
            else "For every Profile fact reference, select only a span_id from allowed_evidence_spans. "
        )
        return (
            base
            + "\nThe previous attempt failed strict JSON parsing or typed validation: "
            + repair_error
            + accepted_guidance
            + interest_guidance
            + unknown_role_guidance
            + atomic_repair_guidance
            + "\nRegenerate the complete mapping from the original task and input. "
            + transport_guidance
            + "Do not output a path, value_snapshot, excerpt, offset, fingerprint, or custom span. "
            "Do not merely patch a fragment. Do not output score, rank, Decision, Gap, stable ID, "
            "fingerprint, explanation outside JSON, or Markdown."
        )

    def _write_diagnostics(
        self,
        *,
        attempt: int,
        response: _ProviderResponse | None,
        error: str | None,
        validation_report: MappingValidationReport | None,
        retry_selection_reason: str | None = None,
        selected_attempt: int | None = None,
    ) -> None:
        raw_response=None if response is None else response.content
        safe_error=(None if error is None else error.replace(self.settings.api_key,"[REDACTED]"))
        metadata={
            "attempt":attempt,
            "provider_path":response.provider_path if response else self.capabilities.protocol,
            "model":self.settings.model,
            "response_format_mode":response.response_format_mode if response else self._active_response_format_mode,
            "parser_error":safe_error,
            "response_sha256":None if raw_response is None else hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
            "response_length":0 if raw_response is None else len(raw_response),
            "fallback_reason":response.fallback_reason if response else self._active_fallback_reason,
            "raw_response_saved":False,
        }
        if validation_report is not None and validation_report.rejections:
            metadata["candidate_rejection_count"] = validation_report.rejected_count
            metadata["candidate_rejection_reason_codes"] = list(validation_report.reason_codes)
        if retry_selection_reason is not None:
            metadata["retry_selection_reason"] = retry_selection_reason
            metadata["selected_attempt"] = selected_attempt
        self.attempt_diagnostics.append(metadata)
        if self.diagnostics is None:
            return
        self.diagnostics.write_attempt(
            attempt=attempt,
            provider_path=metadata["provider_path"],
            model=self.settings.model,
            response_format_mode=metadata["response_format_mode"],
            parser_error=safe_error,
            raw_response=raw_response,
            fallback_reason=metadata["fallback_reason"],
            candidate_rejection_count=metadata.get("candidate_rejection_count", 0),
            candidate_rejection_reason_codes=metadata.get("candidate_rejection_reason_codes", []),
            retry_selection_reason=retry_selection_reason,
            selected_attempt=selected_attempt,
        )

    def _annotate_retry_selection(self, *, reason: str, selected_attempt: int) -> None:
        for metadata in self.attempt_diagnostics:
            metadata["retry_selection_reason"] = reason
            metadata["selected_attempt"] = selected_attempt

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
