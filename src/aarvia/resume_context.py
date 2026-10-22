"""Validated, non-persistent Phase 2 context and fact index for Phase 3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

from .base_resume import BaseResumeArtifact, BaseResumeItemType
from .capability_rubric import CapabilityRubric
from .career_direction import UserRoleDecision
from .career_gap_analysis import CareerGapAnalysis
from .evidence_bank import EvidenceBank, EvidenceItem, select_resume_evidence_items
from .evidence_binding_review import EvidenceBindingReviewArtifact
from .evidence_enrichment import (
    EnrichmentScope,
    EvidenceEnrichmentArtifact,
    select_resume_enrichment_materials,
)
from .evidence_group_allocation_review import EvidenceGroupAllocationReviewArtifact
from .profile import CareerProfile
from .profile_dimension_mapping import ProfileDimensionMappingCandidateSet
from .resume_material import ResumeMaterialArtifact
from .resume_profile_supplement import (
    ProfileStructuralSelection,
    ResumeProfileSupplementArtifact,
    select_confirmed_resume_profile_supplement,
)
from .resume_wording import ResumeWordingReviewArtifact, select_confirmed_resume_wording
from .role_catalog import RoleCatalog
from .role_recommendation import RoleRecommendationArtifact


class ResumeFactKind(str, Enum):
    EVIDENCE_ITEM = "evidence_item"
    ENRICHMENT_CLAIM = "enrichment_claim"
    STRUCTURAL_SELECTION = "structural_selection"


class ResumeFactVisibility(str, Enum):
    SHOWN_IN_BASE_RESUME = "shown_in_base_resume"
    ELIGIBLE_NOT_SHOWN = "eligible_not_shown"
    STRUCTURAL_CONTEXT = "structural_context"


@dataclass(frozen=True, eq=True)
class ResumeFactSourceContext:
    """Minimal, non-authoritative context for interpreting a verified fact."""

    source_reference: str
    record_type: str
    organization_or_project_name: str | None = None
    title_or_role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    contribution_scope: str | None = None

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "source_reference": self.source_reference,
            "record_type": self.record_type,
            "organization_or_project_name": self.organization_or_project_name,
            "title_or_role": self.title_or_role,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "contribution_scope": self.contribution_scope,
        }


@dataclass(frozen=True, eq=True)
class ResumeFact:
    fact_id: str
    fact_kind: ResumeFactKind
    source_record_id: str | None
    text: str
    evidence_item_ids: tuple[str, ...]
    enrichment_claim_ids: tuple[str, ...]
    visibility: ResumeFactVisibility
    source_context: ResumeFactSourceContext | None = None

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_kind": self.fact_kind.value,
            "text": self.text,
            "visibility": self.visibility.value,
            "source_context": (
                None if self.source_context is None
                else self.source_context.to_provider_dict()
            ),
        }


@dataclass(frozen=True, eq=True)
class ResumeFactIndex:
    facts: tuple[ResumeFact, ...]
    reviewed_wording_by_id: dict[str, str]
    material_by_id: dict[str, Any]
    evidence_item_by_id: dict[str, EvidenceItem]
    unknown_need_ids: tuple[str, ...]

    @property
    def fact_by_id(self) -> dict[str, ResumeFact]:
        return {item.fact_id: item for item in self.facts}

    def provider_inventory(self) -> list[dict[str, Any]]:
        """Return confirmed, non-contact facts only."""
        return [item.to_provider_dict() for item in self.facts]


@dataclass(frozen=True)
class ValidatedResumeContext:
    profile: CareerProfile
    rubric: CapabilityRubric
    catalog: RoleCatalog
    mapping: ProfileDimensionMappingCandidateSet
    recommendation: RoleRecommendationArtifact
    decision: UserRoleDecision
    gap_analysis: CareerGapAnalysis
    evidence_bank: EvidenceBank
    evidence_enrichment: EvidenceEnrichmentArtifact
    resume_material: ResumeMaterialArtifact
    resume_wording_review: ResumeWordingReviewArtifact
    resume_profile_supplement: ResumeProfileSupplementArtifact
    base_resume: BaseResumeArtifact
    evidence_reviews: EvidenceBindingReviewArtifact | None = None
    allocation_reviews: EvidenceGroupAllocationReviewArtifact | None = None
    superseded_decision: UserRoleDecision | None = None
    superseded_enrichment: EvidenceEnrichmentArtifact | None = None
    superseded_resume_material: ResumeMaterialArtifact | None = None
    superseded_wording_review: ResumeWordingReviewArtifact | None = None
    superseded_supplement: ResumeProfileSupplementArtifact | None = None
    superseded_base_resume: BaseResumeArtifact | None = None

    def validation_context(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "rubric": self.rubric,
            "catalog": self.catalog,
            "mapping": self.mapping,
            "recommendation": self.recommendation,
            "decision": self.decision,
            "gap_analysis": self.gap_analysis,
            "evidence_bank": self.evidence_bank,
            "evidence_enrichment": self.evidence_enrichment,
            "resume_material": self.resume_material,
            "resume_wording_review": self.resume_wording_review,
            "resume_profile_supplement": self.resume_profile_supplement,
            "evidence_reviews": self.evidence_reviews,
            "allocation_reviews": self.allocation_reviews,
            "superseded_decision": self.superseded_decision,
            "superseded_enrichment": self.superseded_enrichment,
            "superseded_resume_material": self.superseded_resume_material,
            "superseded_wording_review": self.superseded_wording_review,
            "superseded_supplement": self.superseded_supplement,
            "superseded_base_resume": self.superseded_base_resume,
        }

    def validate(self) -> None:
        self.base_resume.validate(**self.validation_context())

    def fact_index(self) -> ResumeFactIndex:
        self.validate()
        context = self.validation_context()
        context.pop("superseded_base_resume", None)
        context.pop("resume_profile_supplement", None)
        shown_evidence = {
            value for item in self.base_resume.entries for value in item.evidence_item_ids
        } | {value for item in self.base_resume.bullets for value in item.evidence_item_ids}
        shown_claims = {
            value for item in self.base_resume.entries for value in item.enrichment_claim_ids
        } | {value for item in self.base_resume.bullets for value in item.enrichment_claim_ids}
        shown_subjects = {
            item.supplement_subject_id for item in self.base_resume.entries
            if item.supplement_subject_id is not None
        }
        sources = {item.source_record_id: item for item in self.evidence_bank.sources}

        def source_context(
            source_record_id: str, *, contribution_scope: str | None = None,
        ) -> ResumeFactSourceContext:
            source = sources[source_record_id]
            identity = dict(source.identity)
            return ResumeFactSourceContext(
                source_reference=source.source_record_id,
                record_type=source.record_type.value,
                organization_or_project_name=identity.get("organization_or_project_name"),
                title_or_role=identity.get("title_or_role"),
                start_date=identity.get("start_date"),
                end_date=identity.get("end_date"),
                contribution_scope=contribution_scope,
            )

        facts: list[ResumeFact] = []
        for item in select_resume_evidence_items(self.evidence_bank):
            facts.append(ResumeFact(
                item.evidence_item_id, ResumeFactKind.EVIDENCE_ITEM,
                item.source_record_id, item.exact_excerpt, (item.evidence_item_id,), (),
                ResumeFactVisibility.SHOWN_IN_BASE_RESUME
                if item.evidence_item_id in shown_evidence
                else ResumeFactVisibility.ELIGIBLE_NOT_SHOWN,
                source_context(item.source_record_id),
            ))
        claims = {item.claim_id: item for item in self.evidence_enrichment.claims}
        enrichment_context = {
            key: context[key] for key in (
                "profile", "rubric", "catalog", "mapping", "recommendation",
                "decision", "gap_analysis", "evidence_bank", "evidence_reviews",
                "allocation_reviews", "superseded_enrichment", "superseded_decision",
            )
        }
        for selected in select_resume_enrichment_materials(
            self.evidence_enrichment, **enrichment_context
        ):
            claim = claims[selected.claim_id]
            facts.append(ResumeFact(
                claim.claim_id, ResumeFactKind.ENRICHMENT_CLAIM,
                claim.source_record_id, claim.user_supplied_statement,
                claim.evidence_item_ids, (claim.claim_id,),
                ResumeFactVisibility.SHOWN_IN_BASE_RESUME
                if claim.claim_id in shown_claims
                else ResumeFactVisibility.ELIGIBLE_NOT_SHOWN,
                source_context(
                    claim.source_record_id,
                    contribution_scope=(
                        claim.scope.value
                        if claim.scope in {
                            EnrichmentScope.PERSONAL_CONTRIBUTION,
                            EnrichmentScope.TEAM_CONTEXT,
                        }
                        else None
                    ),
                ),
            ))
        _, selections = select_confirmed_resume_profile_supplement(
            self.resume_profile_supplement,
            **context,
        )
        for selection in selections:
            snapshot = selection.profile_reference.value_snapshot
            text = json.dumps(
                {key: snapshot[key] for key in selection.selected_fields},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            selected = {
                key: snapshot[key] for key in selection.selected_fields
            }
            organization = selected.get("organization_or_project_name")
            if organization is None and selection.record_type.value == "education":
                organization = selected.get("institution")
            title = selected.get("title_or_role")
            if title is None and selection.record_type.value == "education":
                title = selected.get("degree")
            facts.append(ResumeFact(
                selection.selection_id, ResumeFactKind.STRUCTURAL_SELECTION,
                None, text, (), (),
                ResumeFactVisibility.SHOWN_IN_BASE_RESUME
                if selection.selection_id in shown_subjects
                else ResumeFactVisibility.STRUCTURAL_CONTEXT,
                ResumeFactSourceContext(
                    source_reference=selection.profile_reference.path,
                    record_type=selection.record_type.value,
                    organization_or_project_name=organization,
                    title_or_role=title,
                    start_date=selected.get("start_date"),
                    end_date=selected.get("end_date") or selected.get("expected_graduation_date"),
                ),
            ))
        wording_context = dict(context)
        wording_context.pop("superseded_supplement", None)
        wording_context.pop("resume_wording_review", None)
        wording = select_confirmed_resume_wording(
            self.resume_wording_review, **wording_context
        )
        return ResumeFactIndex(
            facts=tuple(sorted(facts, key=lambda item: item.fact_id)),
            reviewed_wording_by_id={
                item.wording_candidate_id: item.wording_text for item in wording
            },
            material_by_id={item.material_id: item for item in self.resume_material.materials},
            evidence_item_by_id={item.evidence_item_id: item for item in self.evidence_bank.items},
            unknown_need_ids=tuple(sorted(item.need_id for item in self.evidence_bank.needs)),
        )
