"""Deterministic, explainable Role Recommendation scoring."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .capability_rubric import (
    CapabilityDimension,
    CapabilityRubric,
    DimensionReadiness,
    EvidenceClass,
)
from .career_direction import ProfileReference, RecommendationSet, profile_fingerprint
from .phase2_storage import load_phase2_json, save_phase2_json
from .profile import CareerProfile
from .profile_dimension_mapping import (
    AtomicEvidenceLocator,
    ConstraintCompatibilityCandidate,
    CurrentMatchStatus,
    DirectionalSignalCandidate,
    DirectionalSignalType,
    DirectionalStatus,
    EvidenceStrength,
    InferenceType,
    MappingConstraintStatus,
    MAPPING_REJECTION_SUMMARY_PREFIX,
    MAPPING_REJECTION_WARNING_PREFIX,
    ProfileDimensionMappingCandidate,
    ProfileDimensionMappingCandidateSet,
    ProfileCriterionEvidenceBinding,
    EvidenceTrustLevel,
    MappingCandidateKind,
    allowed_profile_reference_paths,
    mapping_validation_report_from_warnings,
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


RECOMMENDATION_SCHEMA = "aarvia.role_recommendations"
RECOMMENDATION_SCHEMA_VERSION = 4
RECOMMENDATION_ID_VERSION = "role-recommendation-v2"
MVP_ROLE_IDS = ("applied_ai_engineer", "machine_learning_engineer", "research_engineer")


class FitBand(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    EMERGING = "emerging"
    INSUFFICIENT = "insufficient_evidence"


class RecommendationConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class MarketEvidenceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class RecommendationBlockerCode(str, Enum):
    INSUFFICIENT_PROFILE_COVERAGE = "insufficient_profile_coverage"
    UNRESOLVED_PROFILE_CONFLICT = "unresolved_profile_conflict"
    NO_COMPARABLE_ROLE_EVIDENCE = "no_comparable_role_evidence"
    CONDITIONAL_MARKET_BASIS = "conditional_market_basis"
    CONSTRAINT_INCOMPATIBLE = "constraint_incompatible"
    PROVIDER_MAPPING_INSUFFICIENT = "provider_mapping_insufficient"
    CONFIRMED_EVIDENCE_REQUIRED = "confirmed_evidence_required_for_strong"


PROVIDER_REJECTION_LOW_CONFIDENCE_RATIO = 0.50
PROVIDER_REJECTION_ANY_CAP = RecommendationConfidence.MEDIUM
PROVIDER_REJECTION_CRITICAL_CAP = RecommendationConfidence.LOW
PROVIDER_REJECTION_ALL_CAP = RecommendationConfidence.INSUFFICIENT


CURRENT_VALUES = {
    CurrentMatchStatus.DEMONSTRATED: 1.0,
    CurrentMatchStatus.PARTIAL: 0.6,
    CurrentMatchStatus.ADJACENT: 0.3,
    CurrentMatchStatus.NOT_DEMONSTRATED: 0.0,
}
DIRECTIONAL_VALUES = {
    DirectionalStatus.ALIGNED: 1.0,
    DirectionalStatus.PARTIAL: 0.6,
    DirectionalStatus.MISALIGNED: 0.0,
}
_BAND_ORDER = {FitBand.STRONG: 3, FitBand.MODERATE: 2, FitBand.EMERGING: 1, FitBand.INSUFFICIENT: 0}
_CONFIDENCE_ORDER = {RecommendationConfidence.HIGH: 3, RecommendationConfidence.MEDIUM: 2, RecommendationConfidence.LOW: 1, RecommendationConfidence.INSUFFICIENT: 0}
_CONSTRAINT_ORDER = {MappingConstraintStatus.COMPATIBLE: 3, MappingConstraintStatus.NOT_APPLICABLE: 2, MappingConstraintStatus.UNKNOWN: 1, MappingConstraintStatus.INCOMPATIBLE: 0}


def _round_score(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _band(score: float | None, coverage: float) -> FitBand:
    if score is None or coverage < 0.40:
        return FitBand.INSUFFICIENT
    base = FitBand.STRONG if score >= 75 else FitBand.MODERATE if score >= 50 else FitBand.EMERGING
    if coverage < 0.60:
        return FitBand.EMERGING
    if coverage < 0.70 and base == FitBand.STRONG:
        return FitBand.MODERATE
    return base


def _profile_references(values: Iterable[ProfileReference]) -> tuple[ProfileReference, ...]:
    by_key: dict[str, ProfileReference] = {}
    for item in values:
        key = json.dumps(item.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        by_key[key] = item
    return tuple(by_key[key] for key in sorted(by_key))


@dataclass(frozen=True, eq=True)
class DimensionAssessment:
    dimension_id: str
    display_code: str
    name: str
    status: CurrentMatchStatus
    conditional_market_basis: bool
    supporting_profile_references: tuple[ProfileReference, ...]
    reasoning: tuple[str, ...]
    evidence_strength: EvidenceStrength | None
    inference_type: InferenceType | None
    review_required: bool
    supporting_atomic_evidence: tuple[AtomicEvidenceLocator, ...] = ()
    supporting_bindings: tuple[ProfileCriterionEvidenceBinding, ...] = ()

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str, *, schema_version: int = 2
    ) -> DimensionAssessment:
        data = _mapping(value, path)
        allowed = {"dimension_id","display_code","name","status","conditional_market_basis","supporting_profile_references","reasoning","evidence_strength","inference_type","review_required"}
        if schema_version in {3, 4}:
            allowed.add("supporting_atomic_evidence")
        if schema_version == 4:
            allowed.add("supporting_bindings")
        _reject_unknown(data, allowed, path)
        conditional = data.get("conditional_market_basis"); review = data.get("review_required")
        if not isinstance(conditional, bool) or not isinstance(review, bool):
            raise Phase2ValidationError(f"{path} boolean fields are invalid")
        references = _typed_profile_references(data.get("supporting_profile_references"), f"{path}.supporting_profile_references")
        raw_strength = data.get("evidence_strength"); raw_inference = data.get("inference_type")
        atomic = () if schema_version == 2 else tuple(
            AtomicEvidenceLocator.from_dict(
                item, f"{path}.supporting_atomic_evidence[{index}]"
            )
            for index, item in enumerate(
                _list(
                    data.get("supporting_atomic_evidence"),
                    f"{path}.supporting_atomic_evidence",
                )
            )
        )
        bindings = () if schema_version != 4 else tuple(
            ProfileCriterionEvidenceBinding.from_dict(
                item, f"{path}.supporting_bindings[{index}]"
            )
            for index, item in enumerate(
                _list(
                    data.get("supporting_bindings"),
                    f"{path}.supporting_bindings",
                )
            )
        )
        return cls(
            dimension_id=_stable_id(data.get("dimension_id"),f"{path}.dimension_id"),
            display_code=_text(data.get("display_code"),f"{path}.display_code"),
            name=_text(data.get("name"),f"{path}.name"),
            status=_enum(data.get("status"),CurrentMatchStatus,f"{path}.status"),
            conditional_market_basis=conditional,
            supporting_profile_references=references,
            reasoning=_string_tuple(data.get("reasoning"),f"{path}.reasoning"),
            evidence_strength=None if raw_strength is None else _enum(raw_strength,EvidenceStrength,f"{path}.evidence_strength"),
            inference_type=None if raw_inference is None else _enum(raw_inference,InferenceType,f"{path}.inference_type"),
            review_required=review,
            supporting_atomic_evidence=atomic,
            supporting_bindings=bindings,
        )

    def to_dict(self, *, schema_version: int = 2) -> dict[str, Any]:
        result = {"dimension_id":self.dimension_id,"display_code":self.display_code,"name":self.name,"status":self.status.value,"conditional_market_basis":self.conditional_market_basis,"supporting_profile_references":[x.to_dict() for x in self.supporting_profile_references],"reasoning":list(self.reasoning),"evidence_strength":None if self.evidence_strength is None else self.evidence_strength.value,"inference_type":None if self.inference_type is None else self.inference_type.value,"review_required":self.review_required}
        if schema_version in {3, 4}:
            result["supporting_atomic_evidence"] = [
                item.to_dict() for item in self.supporting_atomic_evidence
            ]
        if schema_version == 4:
            result["supporting_bindings"] = [
                item.to_dict() for item in self.supporting_bindings
            ]
        return result


@dataclass(frozen=True, eq=True)
class FitAxisResult:
    score: float | None
    coverage: float
    band: FitBand
    assessed_count: int
    applicable_count: int
    band_cap_reason: str | None = None
    dimension_assessments: tuple[DimensionAssessment, ...] = ()
    directional_signals: tuple[DirectionalSignalCandidate, ...] = ()

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str, *, schema_version: int = 2
    ) -> FitAxisResult:
        data=_mapping(value,path); _reject_unknown(data,{"score","coverage","band","assessed_count","applicable_count","band_cap_reason","dimension_assessments","directional_signals"},path)
        score=data.get("score"); coverage=data.get("coverage"); assessed=data.get("assessed_count"); applicable=data.get("applicable_count")
        if score is not None and (not isinstance(score,(int,float)) or isinstance(score,bool) or not 0<=score<=100): raise Phase2ValidationError(f"{path}.score is invalid")
        if not isinstance(coverage,(int,float)) or isinstance(coverage,bool) or not 0<=coverage<=1: raise Phase2ValidationError(f"{path}.coverage is invalid")
        if not isinstance(assessed,int) or isinstance(assessed,bool) or assessed<0 or not isinstance(applicable,int) or isinstance(applicable,bool) or applicable<0 or assessed>applicable: raise Phase2ValidationError(f"{path} counts are invalid")
        dimensions=tuple(DimensionAssessment.from_dict(x,f"{path}.dimension_assessments[{i}]",schema_version=schema_version) for i,x in enumerate(_list(data.get("dimension_assessments"),f"{path}.dimension_assessments")))
        signals=tuple(_directional_from_typed(x,f"{path}.directional_signals[{i}]") for i,x in enumerate(_list(data.get("directional_signals"),f"{path}.directional_signals")))
        cap_reason=_text(data.get("band_cap_reason"),f"{path}.band_cap_reason",required=False)
        result=cls(None if score is None else float(score),float(coverage),_enum(data.get("band"),FitBand,f"{path}.band"),assessed,applicable,cap_reason,dimensions,signals)
        expected_band=_band(result.score,result.coverage)
        valid_strong_cap = (
            cap_reason
            in {
                "conditional_only_strong_prevention",
                "confirmed_evidence_required_for_strong",
            }
            and expected_band == FitBand.STRONG
            and result.band == FitBand.MODERATE
        )
        if result.band != expected_band and not valid_strong_cap:
            raise Phase2ValidationError(f"{path}.band does not match score and coverage")
        return result

    def to_dict(self, *, schema_version: int = 2) -> dict[str, Any]:
        return {"score":self.score,"coverage":self.coverage,"band":self.band.value,"assessed_count":self.assessed_count,"applicable_count":self.applicable_count,"band_cap_reason":self.band_cap_reason,"dimension_assessments":[x.to_dict(schema_version=schema_version) for x in self.dimension_assessments],"directional_signals":[x.to_dict() for x in self.directional_signals]}

    def validate_calculation(self, path: str, *, kind: str) -> None:
        if kind not in {"current", "directional"}:
            raise ValueError("kind must be current or directional")
        if kind == "current" and self.directional_signals:
            raise Phase2ValidationError(f"{path} cannot contain directional signals")
        if kind == "directional" and (self.dimension_assessments or not self.directional_signals):
            raise Phase2ValidationError(f"{path} must contain directional signals only")
        if kind == "current":
            applicable = [x for x in self.dimension_assessments if x.status != CurrentMatchStatus.NOT_APPLICABLE]
            values = [CURRENT_VALUES[x.status] for x in applicable if x.status in CURRENT_VALUES]
        else:
            applicable = [x for x in self.directional_signals if x.status != DirectionalStatus.NOT_APPLICABLE]
            values = [DIRECTIONAL_VALUES[x.status] for x in applicable if x.status in DIRECTIONAL_VALUES]
        expected_score = None if not values else _round_score(sum(values) / len(values) * 100)
        expected_coverage = 0.0 if not applicable else _round_score(len(values) / len(applicable))
        if self.assessed_count != len(values) or self.applicable_count != len(applicable):
            raise Phase2ValidationError(f"{path} counts do not match assessments")
        if self.score != expected_score or self.coverage != expected_coverage:
            raise Phase2ValidationError(f"{path} score or coverage does not match assessments")
        expected_band = _band(self.score, self.coverage)
        valid_cap = (
            self.band_cap_reason
            in {
                "conditional_only_strong_prevention",
                "confirmed_evidence_required_for_strong",
            }
            and expected_band == FitBand.STRONG
            and self.band == FitBand.MODERATE
        )
        if self.band != expected_band and not valid_cap:
            raise Phase2ValidationError(f"{path} band does not match score and coverage")
        if self.band_cap_reason is not None and not valid_cap:
            raise Phase2ValidationError(f"{path} band cap reason is invalid")


@dataclass(frozen=True, eq=True)
class ConfidenceAssessment:
    result: RecommendationConfidence
    component_results: tuple[tuple[str, RecommendationConfidence], ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_dict(cls,value:Mapping[str,Any],path:str)->ConfidenceAssessment:
        data=_mapping(value,path);_reject_unknown(data,{"result","component_results","reason_codes"},path)
        raw=_list(data.get("component_results"),f"{path}.component_results"); components=[]
        for i,item in enumerate(raw):
            x=_mapping(item,f"{path}.component_results[{i}]");_reject_unknown(x,{"component","confidence"},f"{path}.component_results[{i}]")
            components.append((_stable_id(x.get("component"),f"{path}.component_results[{i}].component"),_enum(x.get("confidence"),RecommendationConfidence,f"{path}.component_results[{i}].confidence")))
        result=cls(_enum(data.get("result"),RecommendationConfidence,f"{path}.result"),tuple(components),_string_tuple(data.get("reason_codes"),f"{path}.reason_codes",ids=True))
        if components and result.result != min((x[1] for x in components),key=lambda c:_CONFIDENCE_ORDER[c]): raise Phase2ValidationError(f"{path}.result is not the weakest component")
        return result

    def to_dict(self)->dict[str,Any]:
        return {"result":self.result.value,"component_results":[{"component":k,"confidence":v.value} for k,v in self.component_results],"reason_codes":list(self.reason_codes)}


@dataclass(frozen=True, eq=True)
class FollowUpQuestion:
    question_id: str
    role_id: str
    dimension_id: str | None
    constraint_id: str | None
    question: str
    current_unknown: str
    possible_effect: str
    missing_fact_type: str

    @classmethod
    def from_dict(cls,value:Mapping[str,Any],path:str)->FollowUpQuestion:
        data=_mapping(value,path);_reject_unknown(data,{"question_id","role_id","dimension_id","constraint_id","question","current_unknown","possible_effect","missing_fact_type"},path)
        dimension=data.get("dimension_id"); constraint=data.get("constraint_id")
        if (dimension is None)==(constraint is None): raise Phase2ValidationError(f"{path} must bind exactly one dimension or constraint")
        return cls(_stable_id(data.get("question_id"),f"{path}.question_id"),_stable_id(data.get("role_id"),f"{path}.role_id"),None if dimension is None else _stable_id(dimension,f"{path}.dimension_id"),None if constraint is None else _stable_id(constraint,f"{path}.constraint_id"),_text(data.get("question"),f"{path}.question"),_text(data.get("current_unknown"),f"{path}.current_unknown"),_text(data.get("possible_effect"),f"{path}.possible_effect"),_stable_id(data.get("missing_fact_type"),f"{path}.missing_fact_type"))

    def to_dict(self)->dict[str,Any]: return self.__dict__.copy()


@dataclass(frozen=True, eq=True)
class RoleRecommendationResult:
    role_id: str
    rank: int | None
    tied_role_ids: tuple[str, ...]
    core_current_fit: FitAxisResult
    extended_current_fit: FitAxisResult
    directional_fit: FitAxisResult
    constraint: ConstraintCompatibilityCandidate
    market_evidence_confidence: MarketEvidenceConfidence
    recommendation_confidence: ConfidenceAssessment
    provisional: bool
    ranking_unstable: bool
    blockers: tuple[str, ...]
    rationale: str
    anti_rationale: str

    @classmethod
    def from_dict(
        cls,value:Mapping[str,Any],path:str,*,schema_version:int=2
    )->RoleRecommendationResult:
        data=_mapping(value,path);_reject_unknown(data,{"role_id","rank","tied_role_ids","core_current_fit","extended_current_fit","directional_fit","constraint","market_evidence_confidence","recommendation_confidence","provisional","ranking_unstable","blockers","rationale","anti_rationale"},path)
        rank=data.get("rank"); provisional=data.get("provisional"); unstable=data.get("ranking_unstable")
        if rank is not None and (not isinstance(rank,int) or isinstance(rank,bool) or rank<1): raise Phase2ValidationError(f"{path}.rank is invalid")
        if not isinstance(provisional,bool) or not isinstance(unstable,bool): raise Phase2ValidationError(f"{path} flags are invalid")
        return cls(role_id=_stable_id(data.get("role_id"),f"{path}.role_id"),rank=rank,tied_role_ids=_string_tuple(data.get("tied_role_ids"),f"{path}.tied_role_ids",ids=True),core_current_fit=FitAxisResult.from_dict(data.get("core_current_fit"),f"{path}.core_current_fit",schema_version=schema_version),extended_current_fit=FitAxisResult.from_dict(data.get("extended_current_fit"),f"{path}.extended_current_fit",schema_version=schema_version),directional_fit=FitAxisResult.from_dict(data.get("directional_fit"),f"{path}.directional_fit",schema_version=schema_version),constraint=_constraint_from_typed(data.get("constraint"),f"{path}.constraint"),market_evidence_confidence=_enum(data.get("market_evidence_confidence"),MarketEvidenceConfidence,f"{path}.market_evidence_confidence"),recommendation_confidence=ConfidenceAssessment.from_dict(data.get("recommendation_confidence"),f"{path}.recommendation_confidence"),provisional=provisional,ranking_unstable=unstable,blockers=_string_tuple(data.get("blockers"),f"{path}.blockers",ids=True),rationale=_text(data.get("rationale"),f"{path}.rationale"),anti_rationale=_text(data.get("anti_rationale"),f"{path}.anti_rationale"))

    def to_dict(self, *, schema_version:int=2)->dict[str,Any]:
        return {"role_id":self.role_id,"rank":self.rank,"tied_role_ids":list(self.tied_role_ids),"core_current_fit":self.core_current_fit.to_dict(schema_version=schema_version),"extended_current_fit":self.extended_current_fit.to_dict(schema_version=schema_version),"directional_fit":self.directional_fit.to_dict(schema_version=schema_version),"constraint":self.constraint.to_dict(),"market_evidence_confidence":self.market_evidence_confidence.value,"recommendation_confidence":self.recommendation_confidence.to_dict(),"provisional":self.provisional,"ranking_unstable":self.ranking_unstable,"blockers":list(self.blockers),"rationale":self.rationale,"anti_rationale":self.anti_rationale}


@dataclass(frozen=True, eq=True)
class RoleRecommendationArtifact:
    recommendation_set_id: str
    created_at: str
    profile_fingerprint: str
    rubric_version: str
    catalog_version: str
    provider_name: str
    provider_model: str
    mapping_schema_version: int
    profile_conflict_warnings: tuple[str, ...]
    role_results: tuple[RoleRecommendationResult, ...]
    follow_up_questions: tuple[FollowUpQuestion, ...]
    tie_groups: tuple[tuple[str, ...], ...]
    blockers: tuple[str, ...]
    schema: str = RECOMMENDATION_SCHEMA
    schema_version: int = RECOMMENDATION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls,value:Mapping[str,Any])->RoleRecommendationArtifact:
        data=_mapping(value,"recommendations");_reject_unknown(data,{"schema","schema_version","recommendation_set_id","created_at","profile_fingerprint","rubric_version","catalog_version","provider_name","provider_model","mapping_schema_version","profile_conflict_warnings","role_results","follow_up_questions","tie_groups","blockers"},"recommendations")
        schema_version=data.get("schema_version")
        if data.get("schema")!=RECOMMENDATION_SCHEMA or schema_version not in {2,3,4}: raise Phase2ValidationError("unsupported Recommendation schema version")
        mapping_version=data.get("mapping_schema_version")
        expected_mapping_version={2:1,3:2,4:3}[schema_version]
        if mapping_version!=expected_mapping_version: raise Phase2ValidationError("unsupported mapping schema version in Recommendation")
        groups=[]
        for i,item in enumerate(_list(data.get("tie_groups"),"recommendations.tie_groups")):
            groups.append(_string_tuple(item,f"recommendations.tie_groups[{i}]",ids=True))
        return cls(recommendation_set_id=_stable_id(data.get("recommendation_set_id"),"recommendations.recommendation_set_id"),created_at=_iso_datetime(data.get("created_at"),"recommendations.created_at"),profile_fingerprint=_text(data.get("profile_fingerprint"),"recommendations.profile_fingerprint"),rubric_version=_version(data.get("rubric_version"),"recommendations.rubric_version"),catalog_version=_version(data.get("catalog_version"),"recommendations.catalog_version"),provider_name=_text(data.get("provider_name"),"recommendations.provider_name"),provider_model=_text(data.get("provider_model"),"recommendations.provider_model"),mapping_schema_version=mapping_version,profile_conflict_warnings=_string_tuple(data.get("profile_conflict_warnings"),"recommendations.profile_conflict_warnings"),role_results=tuple(RoleRecommendationResult.from_dict(x,f"recommendations.role_results[{i}]",schema_version=schema_version) for i,x in enumerate(_list(data.get("role_results"),"recommendations.role_results"))),follow_up_questions=tuple(FollowUpQuestion.from_dict(x,f"recommendations.follow_up_questions[{i}]") for i,x in enumerate(_list(data.get("follow_up_questions"),"recommendations.follow_up_questions"))),tie_groups=tuple(groups),blockers=_string_tuple(data.get("blockers"),"recommendations.blockers",ids=True),schema_version=schema_version)

    def validate(self, *, profile: CareerProfile, rubric: CapabilityRubric, catalog: RoleCatalog) -> None:
        rubric.validate(catalog)
        if (self.schema, self.schema_version, self.mapping_schema_version) not in {
            (RECOMMENDATION_SCHEMA, 2, 1),
            (RECOMMENDATION_SCHEMA, 3, 2),
            (RECOMMENDATION_SCHEMA, 4, 3),
        }:
            raise Phase2ValidationError("Recommendation and mapping schema versions are incompatible")
        if self.schema_version == 4 and rubric.schema_version != 2:
            raise Phase2ValidationError(
                "Recommendation schema 4 requires Capability Rubric schema 2"
            )
        if self.profile_fingerprint != profile_fingerprint(profile): raise Phase2ValidationError("Recommendation Profile fingerprint mismatch")
        if self.rubric_version != rubric.rubric_version: raise Phase2ValidationError("Recommendation Rubric version mismatch")
        if self.catalog_version != catalog.catalog_version: raise Phase2ValidationError("Recommendation Catalog version mismatch")
        if tuple(sorted(x.role_id for x in self.role_results)) != tuple(sorted(MVP_ROLE_IDS)): raise Phase2ValidationError("Recommendation must contain all three MVP roles exactly once")
        ranks=[x.rank for x in self.role_results if x.rank is not None]
        if any(x<1 for x in ranks): raise Phase2ValidationError("Recommendation rank is invalid")
        result_map={x.role_id:x for x in self.role_results}
        baseline_results = tuple(
            RoleRecommendationResult(
                **{
                    **result.__dict__,
                    "rank": None,
                    "tied_role_ids": (),
                    "recommendation_confidence": ConfidenceAssessment(
                        RecommendationConfidence.HIGH, (), ()
                    ),
                    "ranking_unstable": False,
                }
            )
            for result in self.role_results
        )
        core_ranks, _ = _rank(list(baseline_results))
        extended_ranks, _ = _rank(list(baseline_results), extended=True)
        baseline_by_role = {item.role_id: item for item in baseline_results}
        provider_rejection_cap, profile_conflicts, all_provider_candidates_rejected = (
            _provider_rejection_context(self.profile_conflict_warnings, rubric)
        )
        rejection_report = mapping_validation_report_from_warnings(
            self.profile_conflict_warnings
        )
        if rejection_report is not None:
            allowed_paths = set(allowed_profile_reference_paths(profile))
            if any(
                item.canonical_profile_path is not None
                and item.canonical_profile_path not in allowed_paths
                for item in rejection_report.rejections
            ):
                raise Phase2ValidationError(
                    "Recommendation rejection metadata contains an invalid Profile path"
                )
        if all_provider_candidates_rejected and any(
            any(
                assessment.status in CURRENT_VALUES
                for assessment in result.extended_current_fit.dimension_assessments
            )
            or any(
                signal.status in DIRECTIONAL_VALUES
                for signal in result.directional_fit.directional_signals
            )
            or result.constraint.status
            in {MappingConstraintStatus.COMPATIBLE, MappingConstraintStatus.INCOMPATIBLE}
            for result in self.role_results
        ):
            raise Phase2ValidationError(
                "all-rejected Provider metadata conflicts with assessed recommendation evidence"
            )
        for result in self.role_results:
            catalog.role(result.role_id)
            result.constraint.validate(profile=profile,rubric=rubric)
            for axis in (result.core_current_fit,result.extended_current_fit):
                axis.validate_calculation(
                    f"recommendations.{result.role_id}.current_fit", kind="current"
                )
                for assessment in axis.dimension_assessments:
                    dimension=rubric.dimension(assessment.dimension_id)
                    if dimension.role_id!=result.role_id or assessment.display_code!=dimension.display_code or assessment.name!=dimension.name: raise Phase2ValidationError("Recommendation dimension identity mismatch")
                    if assessment.conditional_market_basis != (dimension.readiness==DimensionReadiness.CONDITIONAL): raise Phase2ValidationError("Recommendation conditional market basis mismatch")
                    for reference in assessment.supporting_profile_references: reference.validate(profile)
                    if self.schema_version in {3, 4}:
                        if _profile_references(
                            item.profile_reference
                            for item in assessment.supporting_atomic_evidence
                        ) != assessment.supporting_profile_references:
                            raise Phase2ValidationError(
                                "Recommendation Profile references do not match atomic evidence"
                            )
                        for locator in assessment.supporting_atomic_evidence:
                            locator.validate(profile)
                        if self.schema_version == 4:
                            _validate_policy_assessment(
                                assessment,
                                dimension=dimension,
                                role_id=result.role_id,
                                profile=profile,
                                rubric=rubric,
                            )
                        elif assessment.supporting_bindings:
                            raise Phase2ValidationError(
                                "Recommendation schema 3 cannot contain policy bindings"
                            )
                        expected_reasoning = _deterministic_dimension_reasoning(
                            dimension,
                            assessment.status,
                            assessment.inference_type,
                            assessment.supporting_atomic_evidence,
                        )
                        if assessment.reasoning != expected_reasoning:
                            raise Phase2ValidationError(
                                "Recommendation dimension reasoning is not evidence-derived"
                            )
                    elif assessment.supporting_atomic_evidence:
                        raise Phase2ValidationError(
                            "Recommendation schema 2 cannot contain atomic evidence"
                        )
                    elif assessment.supporting_bindings:
                        raise Phase2ValidationError(
                            "Recommendation schema 2 cannot contain policy bindings"
                        )
            for signal in result.directional_fit.directional_signals: signal.validate(profile=profile,rubric=rubric)
            if self.schema_version in {3, 4}:
                spans = [
                    locator
                    for assessment in result.extended_current_fit.dimension_assessments
                    for locator in assessment.supporting_atomic_evidence
                ]
                for index, left in enumerate(spans):
                    for right in spans[index + 1:]:
                        if left.profile_reference.path != right.profile_reference.path:
                            continue
                        if max(left.start_offset, right.start_offset) < min(
                            left.end_offset, right.end_offset
                        ):
                            raise Phase2ValidationError(
                                "Recommendation reuses overlapping atomic evidence"
                            )
            result.directional_fit.validate_calculation(
                f"recommendations.{result.role_id}.directional_fit", kind="directional"
            )
            core_ids={x.dimension_id for x in result.core_current_fit.dimension_assessments}
            extended_ids={x.dimension_id for x in result.extended_current_fit.dimension_assessments}
            expected_core={x.dimension_id for x in rubric.role_dimensions(result.role_id) if x.readiness==DimensionReadiness.READY}
            expected_extended={x.dimension_id for x in rubric.role_dimensions(result.role_id)}
            if core_ids!=expected_core or extended_ids!=expected_extended:
                raise Phase2ValidationError("Recommendation does not cover the exact Rubric dimensions")
            signal_types={x.signal_type for x in result.directional_fit.directional_signals}
            if signal_types!=set(DirectionalSignalType):
                raise Phase2ValidationError("Recommendation does not cover all directional signals")
            if result.market_evidence_confidence != _market_confidence(
                tuple(x for x in rubric.role_dimensions(result.role_id) if x.readiness==DimensionReadiness.READY)
                or rubric.role_dimensions(result.role_id)
            ):
                raise Phase2ValidationError("Recommendation market evidence confidence is invalid")
            if result.constraint.status==MappingConstraintStatus.INCOMPATIBLE and result.rank is not None: raise Phase2ValidationError("incompatible Role cannot receive a normal rank")
            expected_unstable = (
                core_ranks[result.role_id] != extended_ranks[result.role_id]
                and core_ranks[result.role_id] is not None
                and extended_ranks[result.role_id] is not None
            )
            if result.ranking_unstable != expected_unstable:
                raise Phase2ValidationError("Recommendation ranking stability is invalid")
            expected_provisional = (
                result.core_current_fit.band == FitBand.INSUFFICIENT
                and result.extended_current_fit.band != FitBand.INSUFFICIENT
            ) or expected_unstable or (
                self.schema_version == 4 and _result_has_provisional_bindings(result)
            )
            if result.provisional != expected_provisional:
                raise Phase2ValidationError("Recommendation provisional flag is invalid")
            expected_blockers=[]
            if max(result.core_current_fit.coverage,result.extended_current_fit.coverage)<.4: expected_blockers.append(RecommendationBlockerCode.INSUFFICIENT_PROFILE_COVERAGE.value)
            if profile_conflicts: expected_blockers.append(RecommendationBlockerCode.UNRESOLVED_PROFILE_CONFLICT.value)
            if not expected_core: expected_blockers.append(RecommendationBlockerCode.NO_COMPARABLE_ROLE_EVIDENCE.value)
            if result.core_current_fit.band==FitBand.INSUFFICIENT and result.extended_current_fit.band!=FitBand.INSUFFICIENT: expected_blockers.append(RecommendationBlockerCode.CONDITIONAL_MARKET_BASIS.value)
            if result.constraint.status==MappingConstraintStatus.INCOMPATIBLE: expected_blockers.append(RecommendationBlockerCode.CONSTRAINT_INCOMPATIBLE.value)
            if all_provider_candidates_rejected: expected_blockers.append(RecommendationBlockerCode.PROVIDER_MAPPING_INSUFFICIENT.value)
            if self.schema_version == 4:
                expected_blockers.append(
                    RecommendationBlockerCode.CONFIRMED_EVIDENCE_REQUIRED.value
                )
                if any(
                    axis.band == FitBand.STRONG
                    for axis in (result.core_current_fit, result.extended_current_fit)
                ):
                    raise Phase2ValidationError(
                        "Recommendation schema 4 cannot claim Strong without confirmed evidence"
                    )
            if result.blockers != tuple(expected_blockers):
                raise Phase2ValidationError("Recommendation blockers are invalid")
            expected_confidence=_confidence(core=result.core_current_fit,extended=result.extended_current_fit,directional=result.directional_fit,constraint=result.constraint,market=result.market_evidence_confidence,conflicts=profile_conflicts,ranking_unstable=expected_unstable,separation=_separation_confidence(baseline_by_role[result.role_id],baseline_results),provider_rejection_cap=provider_rejection_cap)
            if result.recommendation_confidence != expected_confidence:
                raise Phase2ValidationError("Recommendation confidence does not match deterministic inputs")
            expected_rationale=f"Current evidence supports {result.extended_current_fit.band.value.replace('_',' ')} fit in the extended rubric."
            expected_anti=f"The role is limited by {', '.join(result.blockers) if result.blockers else 'remaining unknown or weaker dimensions'}."
            if result.rationale!=expected_rationale or result.anti_rationale!=expected_anti:
                raise Phase2ValidationError("Recommendation explanation payload is invalid")
        for group in self.tie_groups:
            if len(group)<2 or any(role not in result_map for role in group): raise Phase2ValidationError("Recommendation tie group is invalid")
            ranks_in_group={result_map[role].rank for role in group}
            if len(ranks_in_group)!=1: raise Phase2ValidationError("tied Roles must have the same rank")
        if len(self.follow_up_questions)>3: raise Phase2ValidationError("Recommendation allows at most three follow-up questions")
        question_ids=[x.question_id for x in self.follow_up_questions]
        if len(question_ids)!=len(set(question_ids)): raise Phase2ValidationError("duplicate follow-up question IDs")
        if self.follow_up_questions != _follow_ups(self.role_results):
            raise Phase2ValidationError("Recommendation follow-up questions are invalid")
        expected_ranks, expected_groups = _final_ranking(tuple(self.role_results))
        if any(item.rank != expected_ranks[item.role_id] for item in self.role_results):
            raise Phase2ValidationError("Recommendation ranks do not match deterministic ranking")
        normalized_groups = tuple(sorted(set(tuple(sorted(group)) for group in self.tie_groups)))
        if normalized_groups != expected_groups:
            raise Phase2ValidationError("Recommendation tie groups do not match deterministic ranking")
        for result in self.role_results:
            expected_tied = tuple(
                sorted({role for group in expected_groups if result.role_id in group for role in group if role != result.role_id})
            )
            if result.tied_role_ids != expected_tied:
                raise Phase2ValidationError("Recommendation tied Role references are invalid")
        expected_blockers=tuple(sorted(set(code for result in self.role_results for code in result.blockers)))
        if self.blockers!=expected_blockers:
            raise Phase2ValidationError("Recommendation aggregate blockers are invalid")
        expected=generate_recommendation_id(profile_fingerprint=self.profile_fingerprint,rubric_version=self.rubric_version,catalog_version=self.catalog_version,provider_name=self.provider_name,provider_model=self.provider_model,role_results=self.role_results,schema_version=self.schema_version)
        if self.recommendation_set_id!=expected: raise Phase2ValidationError("Recommendation ID is not deterministic")

    def to_dict(self)->dict[str,Any]:
        return {"schema":self.schema,"schema_version":self.schema_version,"recommendation_set_id":self.recommendation_set_id,"created_at":self.created_at,"profile_fingerprint":self.profile_fingerprint,"rubric_version":self.rubric_version,"catalog_version":self.catalog_version,"provider_name":self.provider_name,"provider_model":self.provider_model,"mapping_schema_version":self.mapping_schema_version,"profile_conflict_warnings":list(self.profile_conflict_warnings),"role_results":[x.to_dict(schema_version=self.schema_version) for x in self.role_results],"follow_up_questions":[x.to_dict() for x in self.follow_up_questions],"tie_groups":[list(x) for x in self.tie_groups],"blockers":list(self.blockers)}


def _list(value:Any,path:str)->list[Any]:
    if not isinstance(value,list): raise Phase2ValidationError(f"{path} must be a list")
    return value


def _typed_profile_references(value:Any,path:str)->tuple[ProfileReference,...]:
    return tuple(ProfileReference.from_dict(x,f"{path}[{i}]") for i,x in enumerate(_list(value,path)))


def _directional_from_typed(value:Any,path:str)->DirectionalSignalCandidate:
    data=_mapping(value,path);_reject_unknown(data,{"role_id","signal_type","status","profile_fact_references","reasoning","provider_confidence","review_required","suggested_follow_up"},path)
    review=data.get("review_required")
    if not isinstance(review,bool): raise Phase2ValidationError(f"{path}.review_required must be a boolean")
    from .profile_dimension_mapping import ProviderConfidence
    return DirectionalSignalCandidate(_stable_id(data.get("role_id"),f"{path}.role_id"),_enum(data.get("signal_type"),DirectionalSignalType,f"{path}.signal_type"),_enum(data.get("status"),DirectionalStatus,f"{path}.status"),_typed_profile_references(data.get("profile_fact_references"),f"{path}.profile_fact_references"),_text(data.get("reasoning"),f"{path}.reasoning"),_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review,_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


def _constraint_from_typed(value:Any,path:str)->ConstraintCompatibilityCandidate:
    data=_mapping(value,path);_reject_unknown(data,{"role_id","status","profile_fact_references","reasoning","provider_confidence","review_required","unknown_constraint_ids","suggested_follow_up"},path)
    review=data.get("review_required")
    if not isinstance(review,bool): raise Phase2ValidationError(f"{path}.review_required must be a boolean")
    from .profile_dimension_mapping import ProviderConfidence
    return ConstraintCompatibilityCandidate(_stable_id(data.get("role_id"),f"{path}.role_id"),_enum(data.get("status"),MappingConstraintStatus,f"{path}.status"),_typed_profile_references(data.get("profile_fact_references"),f"{path}.profile_fact_references"),_text(data.get("reasoning"),f"{path}.reasoning"),_enum(data.get("provider_confidence"),ProviderConfidence,f"{path}.provider_confidence"),review,_string_tuple(data.get("unknown_constraint_ids"),f"{path}.unknown_constraint_ids",ids=True),_text(data.get("suggested_follow_up"),f"{path}.suggested_follow_up",required=False))


def _strongest_mapping(items:tuple[ProfileDimensionMappingCandidate,...])->ProfileDimensionMappingCandidate|None:
    values={CurrentMatchStatus.DEMONSTRATED:5,CurrentMatchStatus.PARTIAL:4,CurrentMatchStatus.ADJACENT:3,CurrentMatchStatus.NOT_DEMONSTRATED:2,CurrentMatchStatus.UNKNOWN:1,CurrentMatchStatus.NOT_APPLICABLE:0}
    return max(items,key=lambda x:(values[x.match_status],x.mapping_id)) if items else None


def _deterministic_dimension_reasoning(
    dimension: CapabilityDimension,
    status: CurrentMatchStatus,
    inference_type: InferenceType | None,
    evidence: tuple[AtomicEvidenceLocator, ...],
) -> tuple[str, ...]:
    if not evidence:
        return ("No validated atomic Profile evidence was provided.",)
    inference = "direct" if inference_type is None else inference_type.value.replace("_", " ")
    return tuple(
        f'{dimension.name}: {status.value.replace("_", " ")} from {inference} evidence "{item.exact_excerpt}".'
        for item in sorted(evidence, key=lambda value: value.evidence_fingerprint)
    )


_BINDING_STATUS_ORDER = {
    CurrentMatchStatus.DEMONSTRATED: 4,
    CurrentMatchStatus.PARTIAL: 3,
    CurrentMatchStatus.ADJACENT: 2,
    CurrentMatchStatus.UNKNOWN: 1,
}


def _policy_dimension_assessment(
    dimension: CapabilityDimension,
    bindings: tuple[ProfileCriterionEvidenceBinding, ...],
) -> DimensionAssessment:
    ordered = tuple(sorted(bindings, key=lambda item: item.binding_id))
    if not ordered:
        status = CurrentMatchStatus.UNKNOWN
        strength = None
        inference = None
        review_required = False
        evidence: tuple[AtomicEvidenceLocator, ...] = ()
    else:
        strongest = max(
            ordered,
            key=lambda item: (
                _BINDING_STATUS_ORDER[item.derived_match_status],
                item.binding_id,
            ),
        )
        status = strongest.derived_match_status
        strength = strongest.derived_evidence_strength
        inference = strongest.derived_inference_type
        review_required = any(item.derived_review_required for item in ordered)
        evidence = tuple(item.atomic_evidence for item in ordered)
    if status == CurrentMatchStatus.DEMONSTRATED:
        raise Phase2ValidationError(
            "Recommendation schema 4 cannot derive demonstrated without confirmed evidence"
        )
    references = _profile_references(item.profile_reference for item in evidence)
    return DimensionAssessment(
        dimension_id=dimension.dimension_id,
        display_code=dimension.display_code,
        name=dimension.name,
        status=status,
        conditional_market_basis=(
            dimension.readiness == DimensionReadiness.CONDITIONAL
        ),
        supporting_profile_references=references,
        reasoning=_deterministic_dimension_reasoning(
            dimension, status, inference, evidence
        ),
        evidence_strength=strength,
        inference_type=inference,
        review_required=review_required,
        supporting_atomic_evidence=evidence,
        supporting_bindings=ordered,
    )


def _validate_policy_assessment(
    assessment: DimensionAssessment,
    *,
    dimension: CapabilityDimension,
    role_id: str,
    profile: CareerProfile,
    rubric: CapabilityRubric,
) -> None:
    binding_ids = [item.binding_id for item in assessment.supporting_bindings]
    if binding_ids != sorted(binding_ids) or len(binding_ids) != len(set(binding_ids)):
        raise Phase2ValidationError(
            "Recommendation policy bindings must be unique and deterministically ordered"
        )
    for binding in assessment.supporting_bindings:
        binding.validate(profile=profile, rubric=rubric)
        if binding.role_id != role_id or binding.dimension_id != dimension.dimension_id:
            raise Phase2ValidationError(
                "Recommendation policy binding belongs to another Role or Dimension"
            )
    skill_names = {
        (binding.criterion_id, match.group(1))
        for binding in assessment.supporting_bindings
        if binding.evidence_class == EvidenceClass.SKILL_NAME
        and (
            match := re.fullmatch(
                r"skills\[(\d+)\]\.skill_name",
                binding.atomic_evidence.profile_reference.path,
            )
        )
        is not None
    }
    for binding in assessment.supporting_bindings:
        if binding.evidence_class != EvidenceClass.SKILL_PROFICIENCY:
            continue
        match = re.fullmatch(
            r"skills\[(\d+)\]\.self_reported_proficiency",
            binding.atomic_evidence.profile_reference.path,
        )
        if match is None or (binding.criterion_id, match.group(1)) not in skill_names:
            raise Phase2ValidationError(
                "Recommendation skill proficiency requires its skill-name binding"
            )
    expected = _policy_dimension_assessment(
        dimension, assessment.supporting_bindings
    )
    if assessment != expected:
        raise Phase2ValidationError(
            "Recommendation Dimension assessment does not match policy-derived bindings"
        )


def _result_has_provisional_bindings(result: RoleRecommendationResult) -> bool:
    return any(
        binding.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
        for assessment in result.extended_current_fit.dimension_assessments
        for binding in assessment.supporting_bindings
    )


def _current_axis(role_id:str,dimensions:tuple[CapabilityDimension,...],mappings:tuple[ProfileDimensionMappingCandidate,...],*,conditional:bool,schema_version:int)->FitAxisResult:
    assessments=[]
    for dimension in dimensions:
        candidates=tuple(x for x in mappings if x.role_id==role_id and x.dimension_id==dimension.dimension_id)
        strongest=_strongest_mapping(candidates)
        if strongest is None:
            status=CurrentMatchStatus.UNKNOWN; refs=(); evidence=(); strength=None; inference=None; review=False
            reasons=("No validated Profile mapping was provided.",) if schema_version == 2 else _deterministic_dimension_reasoning(dimension,status,inference,evidence)
        else:
            status=strongest.match_status;refs=_profile_references(r for x in candidates for r in x.profile_fact_references);evidence=tuple(sorted((locator for x in candidates for locator in x.atomic_evidence),key=lambda value:value.evidence_fingerprint));strength=strongest.evidence_strength;inference=strongest.inference_type;review=any(x.review_required for x in candidates)
            reasons=tuple(sorted({x.reasoning for x in candidates})) if schema_version == 2 else _deterministic_dimension_reasoning(dimension,status,inference,evidence)
        assessments.append(DimensionAssessment(dimension.dimension_id,dimension.display_code,dimension.name,status,dimension.readiness==DimensionReadiness.CONDITIONAL,refs,reasons,strength,inference,review,evidence))
    applicable=[x for x in assessments if x.status!=CurrentMatchStatus.NOT_APPLICABLE]
    assessed=[x for x in applicable if x.status in CURRENT_VALUES]
    score=None if not assessed else _round_score(sum(CURRENT_VALUES[x.status] for x in assessed)/len(assessed)*100)
    coverage=0.0 if not applicable else _round_score(len(assessed)/len(applicable))
    band=_band(score,coverage);cap_reason=None
    if conditional and band==FitBand.STRONG:
        ready_positive=any(not x.conditional_market_basis and x.status in {CurrentMatchStatus.DEMONSTRATED,CurrentMatchStatus.PARTIAL} for x in assessments)
        if not ready_positive: band=FitBand.MODERATE;cap_reason="conditional_only_strong_prevention"
    return FitAxisResult(score,coverage,band,len(assessed),len(applicable),cap_reason,tuple(assessments),())


def _policy_current_axis(
    role_id: str,
    dimensions: tuple[CapabilityDimension, ...],
    bindings: tuple[ProfileCriterionEvidenceBinding, ...],
    *,
    conditional: bool,
) -> FitAxisResult:
    assessments = tuple(
        _policy_dimension_assessment(
            dimension,
            tuple(
                item
                for item in bindings
                if item.role_id == role_id
                and item.dimension_id == dimension.dimension_id
            ),
        )
        for dimension in dimensions
    )
    applicable = tuple(
        item
        for item in assessments
        if item.status != CurrentMatchStatus.NOT_APPLICABLE
    )
    assessed = tuple(item for item in applicable if item.status in CURRENT_VALUES)
    score = (
        None
        if not assessed
        else _round_score(
            sum(CURRENT_VALUES[item.status] for item in assessed)
            / len(assessed)
            * 100
        )
    )
    coverage = (
        0.0
        if not applicable
        else _round_score(len(assessed) / len(applicable))
    )
    band = _band(score, coverage)
    cap_reason = None
    if band == FitBand.STRONG:
        band = FitBand.MODERATE
        cap_reason = "confirmed_evidence_required_for_strong"
    if conditional and band == FitBand.STRONG:
        raise Phase2ValidationError("policy band cap was not applied")
    return FitAxisResult(
        score,
        coverage,
        band,
        len(assessed),
        len(applicable),
        cap_reason,
        assessments,
        (),
    )


def _directional_axis(role_id:str,signals:tuple[DirectionalSignalCandidate,...])->FitAxisResult:
    by_type={x.signal_type:x for x in signals if x.role_id==role_id}
    complete=tuple(by_type.get(kind) or DirectionalSignalCandidate(role_id,kind,DirectionalStatus.UNKNOWN,(),"No directional evidence was provided.",__import__("aarvia.profile_dimension_mapping",fromlist=["ProviderConfidence"]).ProviderConfidence.LOW,False,None) for kind in DirectionalSignalType)
    applicable=[x for x in complete if x.status!=DirectionalStatus.NOT_APPLICABLE]
    assessed=[x for x in applicable if x.status in DIRECTIONAL_VALUES]
    score=None if not assessed else _round_score(sum(DIRECTIONAL_VALUES[x.status] for x in assessed)/len(assessed)*100)
    coverage=0.0 if not applicable else _round_score(len(assessed)/len(applicable))
    return FitAxisResult(score,coverage,_band(score,coverage),len(assessed),len(applicable),None,(),complete)


def _market_confidence(dimensions:tuple[CapabilityDimension,...])->MarketEvidenceConfidence:
    if not dimensions:return MarketEvidenceConfidence.INSUFFICIENT
    confirmed=[x.confirmed_company_count for x in dimensions]
    if all(x.readiness==DimensionReadiness.READY and x.confirmed_company_count>=4 for x in dimensions):return MarketEvidenceConfidence.HIGH
    if sum(x.readiness==DimensionReadiness.READY and x.confirmed_company_count>=2 for x in dimensions)>=max(1,len(dimensions)//2):return MarketEvidenceConfidence.MEDIUM
    if any(confirmed):return MarketEvidenceConfidence.LOW
    return MarketEvidenceConfidence.INSUFFICIENT


def _separation_confidence(
    result: RoleRecommendationResult,
    peers: tuple[RoleRecommendationResult, ...],
) -> RecommendationConfidence:
    if result.core_current_fit.band == FitBand.INSUFFICIENT:
        return RecommendationConfidence.INSUFFICIENT
    comparable = tuple(
        peer for peer in peers
        if peer.role_id != result.role_id
        and peer.constraint.status != MappingConstraintStatus.INCOMPATIBLE
        and peer.core_current_fit.band != FitBand.INSUFFICIENT
    )
    if not comparable:
        return RecommendationConfidence.INSUFFICIENT
    nearest = min(
        abs((result.core_current_fit.score or 0) - (peer.core_current_fit.score or 0))
        for peer in comparable
    )
    if nearest > 10:
        return RecommendationConfidence.HIGH
    if nearest > 5:
        return RecommendationConfidence.MEDIUM
    return RecommendationConfidence.LOW


def _confidence(
    *,
    core: FitAxisResult,
    extended: FitAxisResult,
    directional: FitAxisResult,
    constraint: ConstraintCompatibilityCandidate,
    market: MarketEvidenceConfidence,
    conflicts: tuple[str, ...],
    ranking_unstable: bool,
    separation: RecommendationConfidence,
    provider_rejection_cap: RecommendationConfidence | None = None,
) -> ConfidenceAssessment:
    components=[];reasons=[]
    coverage=RecommendationConfidence.INSUFFICIENT if max(core.coverage,extended.coverage)<.4 else RecommendationConfidence.MEDIUM if max(core.coverage,extended.coverage)<.8 else RecommendationConfidence.HIGH
    components.append(("profile_coverage",coverage))
    assessments=tuple(x for x in extended.dimension_assessments if x.status in CURRENT_VALUES)
    strengths=tuple(x.evidence_strength for x in assessments if x.evidence_strength is not None)
    if not strengths:
        fact_strength=RecommendationConfidence.INSUFFICIENT
    elif EvidenceStrength.WEAK in strengths:
        fact_strength=RecommendationConfidence.LOW
    elif EvidenceStrength.SUPPORTING in strengths:
        fact_strength=RecommendationConfidence.MEDIUM
    else:
        fact_strength=RecommendationConfidence.HIGH
    components.append(("profile_fact_strength",fact_strength))
    applicable=tuple(x for x in extended.dimension_assessments if x.status!=CurrentMatchStatus.NOT_APPLICABLE)
    unknown_ratio=(sum(x.status==CurrentMatchStatus.UNKNOWN for x in applicable)/len(applicable)) if applicable else 1.0
    unknown=RecommendationConfidence.INSUFFICIENT if not applicable else RecommendationConfidence.LOW if unknown_ratio>.5 else RecommendationConfidence.MEDIUM if unknown_ratio>0 else RecommendationConfidence.HIGH
    components.append(("unknown_ratio",unknown))
    inferred=tuple(x for x in assessments if x.inference_type is not None)
    inference_ratio=(sum(x.inference_type!=InferenceType.DIRECT for x in inferred)/len(inferred)) if inferred else 1.0
    inference=RecommendationConfidence.LOW if inference_ratio>.5 else RecommendationConfidence.MEDIUM if inference_ratio>0 else RecommendationConfidence.HIGH
    components.append(("semantic_inference",inference))
    market_component={MarketEvidenceConfidence.HIGH:RecommendationConfidence.HIGH,MarketEvidenceConfidence.MEDIUM:RecommendationConfidence.MEDIUM,MarketEvidenceConfidence.LOW:RecommendationConfidence.LOW,MarketEvidenceConfidence.INSUFFICIENT:RecommendationConfidence.INSUFFICIENT}[market]
    components.append(("market_evidence",market_component))
    constraint_component=RecommendationConfidence.LOW if constraint.status==MappingConstraintStatus.UNKNOWN else RecommendationConfidence.INSUFFICIENT if constraint.status==MappingConstraintStatus.INCOMPATIBLE else RecommendationConfidence.HIGH
    components.append(("constraint_certainty",constraint_component))
    components.append(("profile_conflict",RecommendationConfidence.LOW if conflicts else RecommendationConfidence.HIGH))
    components.append(("ranking_stability",RecommendationConfidence.LOW if ranking_unstable else RecommendationConfidence.HIGH))
    components.append(("adjacent_role_separation",separation))
    conditional_decisive=core.band!=extended.band
    components.append(("rubric_readiness",RecommendationConfidence.LOW if conditional_decisive else RecommendationConfidence.MEDIUM if any(x.conditional_market_basis for x in extended.dimension_assessments) else RecommendationConfidence.HIGH))
    if provider_rejection_cap is not None:
        components.append(("provider_candidate_rejection", provider_rejection_cap))
    for name,value in components:
        if value!=RecommendationConfidence.HIGH: reasons.append(f"{name}_{value.value}")
    result=min((x[1] for x in components),key=lambda c:_CONFIDENCE_ORDER[c])
    return ConfidenceAssessment(result,tuple(components),tuple(reasons))


def _provider_rejection_context(
    warnings: tuple[str, ...],
    rubric: CapabilityRubric,
) -> tuple[RecommendationConfidence | None, tuple[str, ...], bool]:
    report = mapping_validation_report_from_warnings(warnings)
    profile_conflicts = tuple(
        warning
        for warning in warnings
        if not warning.startswith((MAPPING_REJECTION_WARNING_PREFIX, MAPPING_REJECTION_SUMMARY_PREFIX))
    )
    if report is None:
        return None, profile_conflicts, False
    if report.all_candidates_rejected:
        return PROVIDER_REJECTION_ALL_CAP, profile_conflicts, True
    rejection_ratio = report.rejected_count / report.total_candidate_count
    affects_ready_dimension = False
    for rejection in report.rejections:
        if (
            rejection.candidate_kind == MappingCandidateKind.CURRENT_FIT
            and rejection.dimension_id is not None
        ):
            try:
                dimension = rubric.dimension(rejection.dimension_id)
            except (KeyError, Phase2ValidationError):
                continue
            if dimension.readiness == DimensionReadiness.READY:
                affects_ready_dimension = True
                break
    cap = (
        PROVIDER_REJECTION_CRITICAL_CAP
        if rejection_ratio >= PROVIDER_REJECTION_LOW_CONFIDENCE_RATIO or affects_ready_dimension
        else PROVIDER_REJECTION_ANY_CAP
    )
    return cap, profile_conflicts, False


def _sort_key(result:RoleRecommendationResult,*,extended:bool=False)->tuple[Any,...]:
    current=result.extended_current_fit if extended else result.core_current_fit
    score=-1 if current.score is None else current.score; directional=-1 if result.directional_fit.score is None else result.directional_fit.score
    return (_CONSTRAINT_ORDER[result.constraint.status],_BAND_ORDER[current.band],_BAND_ORDER[result.directional_fit.band],_CONFIDENCE_ORDER[result.recommendation_confidence.result],score,directional)


def _rank(results:list[RoleRecommendationResult],*,extended:bool=False)->tuple[dict[str,int|None],tuple[tuple[str,...],...]]:
    eligible=[x for x in results if x.constraint.status!=MappingConstraintStatus.INCOMPATIBLE and (x.extended_current_fit if extended else x.core_current_fit).band!=FitBand.INSUFFICIENT]
    ordered=sorted(eligible,key=lambda x:(tuple(-v if isinstance(v,(int,float)) else v for v in _sort_key(x,extended=extended)),x.role_id))
    ranks={x.role_id:None for x in results};groups=[];rank=0;previous=None;previous_result=None
    for index,item in enumerate(ordered,1):
        axis=item.extended_current_fit if extended else item.core_current_fit
        tied=False
        if previous_result is not None:
            prev_axis=previous_result.extended_current_fit if extended else previous_result.core_current_fit
            tied=(item.constraint.status==previous_result.constraint.status and axis.band==prev_axis.band and item.directional_fit.band==previous_result.directional_fit.band and abs((axis.score or 0)-(prev_axis.score or 0))<=5 and abs((item.directional_fit.score or 0)-(previous_result.directional_fit.score or 0))<=5)
        if not tied: rank=index
        ranks[item.role_id]=rank
        if tied:
            if groups and previous in groups[-1]:groups[-1]=(*groups[-1],item.role_id)
            else:groups.append((previous,item.role_id))
        previous=item.role_id;previous_result=item
    return ranks,tuple(groups)


def _final_ranking(
    results: tuple[RoleRecommendationResult, ...],
) -> tuple[dict[str, int | None], tuple[tuple[str, ...], ...]]:
    items = list(results)
    core_ranks, core_groups = _rank(items)
    extended_ranks, extended_groups = _rank(items, extended=True)
    final = {
        item.role_id: core_ranks[item.role_id]
        if core_ranks[item.role_id] is not None
        else extended_ranks[item.role_id]
        for item in items
    }
    groups: tuple[tuple[str, ...], ...] = (*core_groups, *extended_groups)
    unstable_roles = {item.role_id for item in items if item.ranking_unstable}
    if unstable_roles:
        adjacent = {
            role
            for role in MVP_ROLE_IDS
            if role in unstable_roles
            or any(abs((core_ranks.get(role) or 99) - (core_ranks.get(other) or 99)) <= 1 for other in unstable_roles)
        }
        tie_rank = min((rank for role, rank in final.items() if role in adjacent and rank is not None), default=None)
        if tie_rank is not None:
            for role in adjacent:
                final[role] = tie_rank
            groups = (*groups, tuple(sorted(adjacent)))
    normalized = tuple(
        sorted(
            set(
                tuple(sorted(group))
                for group in groups
                if len(group) > 1 and len({final[role] for role in group}) == 1
            )
        )
    )
    return final, normalized


def _question_id(role_id:str,target:str)->str:return f"question_{hashlib.sha256(f'{role_id}|{target}'.encode()).hexdigest()[:24]}"


def _follow_ups(results:tuple[RoleRecommendationResult,...])->tuple[FollowUpQuestion,...]:
    candidates=[]
    for result in sorted(results,key=lambda x:x.role_id):
        if result.constraint.status==MappingConstraintStatus.UNKNOWN:
            target=result.constraint.unknown_constraint_ids[0] if result.constraint.unknown_constraint_ids else "constraint_compatibility"
            candidates.append((0,FollowUpQuestion(_question_id(result.role_id,target),result.role_id,None,target,result.constraint.suggested_follow_up or "What practical constraint should Aarvia consider for this role?","Constraint compatibility is unknown.","Could change eligibility and recommendation confidence.","constraints")))
        axis=result.core_current_fit if result.core_current_fit.applicable_count else result.extended_current_fit
        for assessment in axis.dimension_assessments:
            if assessment.status==CurrentMatchStatus.UNKNOWN:
                candidates.append((1,FollowUpQuestion(_question_id(result.role_id,assessment.dimension_id),result.role_id,assessment.dimension_id,None,f"What have you done that demonstrates {assessment.name}?",f"{assessment.name} is unknown.","Could change Current Fit coverage, band, or ranking.","capability_evidence")))
        for signal in result.directional_fit.directional_signals:
            if signal.status==DirectionalStatus.UNKNOWN:
                candidates.append((2,FollowUpQuestion(_question_id(result.role_id,signal.signal_type.value),result.role_id,None,signal.signal_type.value,signal.suggested_follow_up or f"How does {result.role_id.replace('_',' ')} fit the work you want to do?",f"{signal.signal_type.value} is unknown.","Could change Directional Fit or confidence.","career_preference")))
    seen=set();selected=[]
    for _,question in sorted(candidates,key=lambda x:(x[0],x[1].role_id,x[1].dimension_id or x[1].constraint_id or "")):
        key=(question.role_id,question.dimension_id,question.constraint_id)
        if key not in seen:selected.append(question);seen.add(key)
        if len(selected)==3:break
    return tuple(selected)


def generate_recommendation_id(*,profile_fingerprint:str,rubric_version:str,catalog_version:str,provider_name:str,provider_model:str,role_results:tuple[RoleRecommendationResult,...],schema_version:int=RECOMMENDATION_SCHEMA_VERSION)->str:
    payload=json.dumps([x.to_dict(schema_version=schema_version) for x in sorted(role_results,key=lambda x:x.role_id)],sort_keys=True,ensure_ascii=False,separators=(",",":"))
    values=(RECOMMENDATION_ID_VERSION,profile_fingerprint,rubric_version,catalog_version,provider_name,provider_model,hashlib.sha256(payload.encode()).hexdigest())
    return f"recommendations_{hashlib.sha256('|'.join(values).encode()).hexdigest()[:24]}"


def build_role_recommendation(*,profile:CareerProfile,mapping_candidates:ProfileDimensionMappingCandidateSet,rubric:CapabilityRubric,catalog:RoleCatalog,created_at:str)->RoleRecommendationArtifact:
    mapping_candidates.validate(profile=profile,rubric=rubric,catalog=catalog);_iso_datetime(created_at,"created_at")
    recommendation_schema_version = {1: 2, 2: 3, 3: 4}.get(
        mapping_candidates.schema_version
    )
    if recommendation_schema_version is None:
        raise Phase2ValidationError("unsupported Mapping schema for Recommendation")
    if recommendation_schema_version == 4 and rubric.schema_version != 2:
        raise Phase2ValidationError(
            "Recommendation schema 4 requires Capability Rubric schema 2"
        )
    provider_rejection_cap, profile_conflicts, all_provider_candidates_rejected = (
        _provider_rejection_context(mapping_candidates.conflict_warnings, rubric)
    )
    constraints={x.role_id:x for x in mapping_candidates.constraints}; preliminary=[]
    for role_id in MVP_ROLE_IDS:
        all_dims=rubric.role_dimensions(role_id);ready=tuple(x for x in all_dims if x.readiness==DimensionReadiness.READY)
        role_maps=tuple(x for x in mapping_candidates.mappings if x.role_id==role_id)
        if recommendation_schema_version == 4:
            if any(
                not isinstance(item, ProfileCriterionEvidenceBinding)
                for item in role_maps
            ):
                raise Phase2ValidationError(
                    "Recommendation schema 4 requires policy-derived bindings"
                )
            policy_bindings = tuple(
                item
                for item in role_maps
                if isinstance(item, ProfileCriterionEvidenceBinding)
            )
            core = _policy_current_axis(
                role_id, ready, policy_bindings, conditional=False
            )
            extended = _policy_current_axis(
                role_id, all_dims, policy_bindings, conditional=True
            )
        else:
            if any(
                not isinstance(item, ProfileDimensionMappingCandidate)
                for item in role_maps
            ):
                raise Phase2ValidationError(
                    "legacy Recommendation schemas require legacy mappings"
                )
            legacy_maps = tuple(
                item
                for item in role_maps
                if isinstance(item, ProfileDimensionMappingCandidate)
            )
            core = _current_axis(
                role_id,
                ready,
                legacy_maps,
                conditional=False,
                schema_version=recommendation_schema_version,
            )
            extended = _current_axis(
                role_id,
                all_dims,
                legacy_maps,
                conditional=True,
                schema_version=recommendation_schema_version,
            )
        directional=_directional_axis(role_id,mapping_candidates.directional_signals)
        constraint=constraints.get(role_id) or ConstraintCompatibilityCandidate(role_id,MappingConstraintStatus.UNKNOWN,(),"No constraint assessment was provided.",__import__("aarvia.profile_dimension_mapping",fromlist=["ProviderConfidence"]).ProviderConfidence.LOW,False,("constraint_compatibility",),None)
        market=_market_confidence(ready if ready else all_dims);conditional_provisional=core.band==FitBand.INSUFFICIENT and extended.band!=FitBand.INSUFFICIENT
        provisional = conditional_provisional
        if recommendation_schema_version == 4:
            provisional = provisional or any(
                binding.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
                for assessment in extended.dimension_assessments
                for binding in assessment.supporting_bindings
            )
        blockers=[]
        if max(core.coverage,extended.coverage)<.4:blockers.append(RecommendationBlockerCode.INSUFFICIENT_PROFILE_COVERAGE.value)
        if profile_conflicts:blockers.append(RecommendationBlockerCode.UNRESOLVED_PROFILE_CONFLICT.value)
        if not ready:blockers.append(RecommendationBlockerCode.NO_COMPARABLE_ROLE_EVIDENCE.value)
        if conditional_provisional:blockers.append(RecommendationBlockerCode.CONDITIONAL_MARKET_BASIS.value)
        if constraint.status==MappingConstraintStatus.INCOMPATIBLE:blockers.append(RecommendationBlockerCode.CONSTRAINT_INCOMPATIBLE.value)
        if all_provider_candidates_rejected:blockers.append(RecommendationBlockerCode.PROVIDER_MAPPING_INSUFFICIENT.value)
        if recommendation_schema_version == 4:
            blockers.append(
                RecommendationBlockerCode.CONFIRMED_EVIDENCE_REQUIRED.value
            )
        preliminary.append(RoleRecommendationResult(role_id,None,(),core,extended,directional,constraint,market,ConfidenceAssessment(RecommendationConfidence.HIGH,(),()),provisional,False,tuple(blockers),f"Current evidence supports {extended.band.value.replace('_',' ')} fit in the extended rubric.",f"The role is limited by {', '.join(blockers) if blockers else 'remaining unknown or weaker dimensions'}."))
    # Establish preliminary core/extended orders before confidence, then recompute with stability caps.
    core_ranks,_=_rank(preliminary);ext_ranks,_=_rank(preliminary,extended=True)
    stabilized=[]
    for item in preliminary:
        unstable=core_ranks[item.role_id]!=ext_ranks[item.role_id] and core_ranks[item.role_id] is not None and ext_ranks[item.role_id] is not None
        conf=_confidence(core=item.core_current_fit,extended=item.extended_current_fit,directional=item.directional_fit,constraint=item.constraint,market=item.market_evidence_confidence,conflicts=profile_conflicts,ranking_unstable=unstable,separation=_separation_confidence(item,tuple(preliminary)),provider_rejection_cap=provider_rejection_cap)
        stabilized.append(RoleRecommendationResult(**{**item.__dict__,"recommendation_confidence":conf,"ranking_unstable":unstable,"provisional":item.provisional or unstable}))
    effective=[]
    final_ranks, tie_groups = _final_ranking(tuple(stabilized))
    for item in stabilized:
        rank=final_ranks[item.role_id]
        related=tuple(sorted({r for g in tie_groups if item.role_id in g for r in g if r!=item.role_id}))
        effective.append(RoleRecommendationResult(**{**item.__dict__,"rank":rank,"tied_role_ids":related}))
    role_results=tuple(sorted(effective,key=lambda x:((x.rank or 999),x.role_id)))
    followups=_follow_ups(role_results)
    recommendation_id=generate_recommendation_id(profile_fingerprint=profile_fingerprint(profile),rubric_version=rubric.rubric_version,catalog_version=catalog.catalog_version,provider_name=mapping_candidates.provider_name,provider_model=mapping_candidates.provider_model,role_results=role_results,schema_version=recommendation_schema_version)
    artifact=RoleRecommendationArtifact(recommendation_id,created_at,profile_fingerprint(profile),rubric.rubric_version,catalog.catalog_version,mapping_candidates.provider_name,mapping_candidates.provider_model,mapping_candidates.schema_version,mapping_candidates.conflict_warnings,role_results,followups,tie_groups,tuple(sorted(set(code for x in role_results for code in x.blockers))),schema_version=recommendation_schema_version)
    artifact.validate(profile=profile,rubric=rubric,catalog=catalog);return artifact


def save_role_recommendation(value:RoleRecommendationArtifact,path:str|Path,*,profile:CareerProfile,rubric:CapabilityRubric,catalog:RoleCatalog)->Path:
    if not isinstance(value,RoleRecommendationArtifact):raise TypeError("value must be a RoleRecommendationArtifact")
    value.validate(profile=profile,rubric=rubric,catalog=catalog);return save_phase2_json(RoleRecommendationArtifact.from_dict(value.to_dict()).to_dict(),path)


def load_role_recommendation(path:str|Path,*,profile:CareerProfile,rubric:CapabilityRubric,catalog:RoleCatalog)->RoleRecommendationArtifact:
    raw=load_phase2_json(path)
    if not isinstance(raw,Mapping) or raw.get("schema")!=RECOMMENDATION_SCHEMA:raise Phase2ValidationError("unsupported Recommendation artifact")
    if raw.get("schema_version")==1:raise Phase2ValidationError("Recommendation schema 1 requires load_recommendation_set")
    value=RoleRecommendationArtifact.from_dict(raw);value.validate(profile=profile,rubric=rubric,catalog=catalog);return value


def load_any_recommendation(path:str|Path,*,profile:CareerProfile,rubric:CapabilityRubric,catalog:RoleCatalog)->RecommendationSet|RoleRecommendationArtifact:
    raw=load_phase2_json(path)
    if not isinstance(raw,Mapping) or raw.get("schema")!=RECOMMENDATION_SCHEMA:raise Phase2ValidationError("unsupported Recommendation artifact")
    if raw.get("schema_version")==1:
        value=RecommendationSet.from_dict(raw);value.validate(catalog,profile);return value
    if raw.get("schema_version") in {2,3,4}:
        value=RoleRecommendationArtifact.from_dict(raw);value.validate(profile=profile,rubric=rubric,catalog=catalog);return value
    raise Phase2ValidationError("unsupported Recommendation schema version")


def migrate_recommendation_v1_to_v2(value:RecommendationSet)->RoleRecommendationArtifact:
    raise Phase2ValidationError("Recommendation schema 1 cannot be auto-upgraded without rubric mappings and scoring provenance")
