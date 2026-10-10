"""Command-line entry point for Aarvia."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from . import __version__
from .confirmation import run_narrative_discovery
from .follow_up import run_follow_up_discovery
from .interview import InputFunction, OutputFunction, run_discovery
from .llm_client import LLMConfigurationError, LLMRequestError
from .narrative_extraction import ExtractionDebugError, NarrativeExtractor, OpenAINarrativeExtractor
from .profile import ProfileValidationError
from .role_catalog import Phase2ValidationError, production_role_catalog
from .capability_rubric import CapabilityRubric, production_capability_rubric
from .career_direction import (
    DecisionStatus,
    SelectedRole,
    create_user_role_decision,
    load_user_role_decision,
    load_user_role_decision_revision_source,
    save_user_role_decision,
)
from .career_gap_analysis import (
    CareerGapAnalysis,
    GapClassification,
    build_career_gap_analysis,
    load_career_gap_analysis,
    save_career_gap_analysis,
)
from .evidence_bank import (
    EvidenceBank,
    build_evidence_bank,
    load_evidence_bank,
    load_evidence_bank_revision_source,
    save_evidence_bank,
)
from .evidence_enrichment import (
    ClaimReviewDecision,
    EnrichmentClaimType,
    EnrichmentRelationship,
    EnrichmentScope,
    EnrichmentTemporality,
    EvidenceEnrichmentArtifact,
    build_evidence_enrichment,
    create_claim_review,
    create_enrichment_claim,
    load_evidence_enrichment,
    load_evidence_enrichment_revision_source,
    save_evidence_enrichment,
)
from .resume_material import (
    ResumeMaterialArtifact,
    build_resume_material,
    load_resume_material,
    load_resume_material_revision_source,
    save_resume_material,
)
from .resume_wording import (
    ResumeWordingReviewArtifact,
    WordingCandidate,
    WordingCandidateType,
    WordingOrigin,
    WordingReviewDecision,
    WordingReviewRecord,
    _validate_candidate_materials,
    build_resume_wording_review,
    create_wording_candidate,
    create_wording_review,
    load_resume_wording_review_revision_source,
    save_resume_wording_review,
)
from .evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
    load_evidence_binding_reviews,
    save_evidence_binding_reviews,
)
from .evidence_group_allocation_review import (
    AllocationReviewDecision,
    AllocationReviewerType,
    create_evidence_group_allocation_review_artifact,
    load_evidence_group_allocation_reviews,
    save_evidence_group_allocation_reviews,
)
from .phase2_storage import load_phase2_json, save_phase2_json_transaction
from .profile_dimension_mapping import (
    AllocatedProfileCriterionEvidenceBinding,
    EvidenceTrustLevel,
    OpenAIProfileDimensionMapper,
    ProfileDimensionMapper,
    ProfileCriterionEvidenceBinding,
    load_mapping_candidates,
    mapping_validation_report_from_warnings,
)
from .role_recommendation import (
    RoleRecommendationArtifact,
    build_role_recommendation,
    load_role_recommendation,
    save_role_recommendation,
)
from .storage import load_profile

DEFAULT_PROFILE_PATH = Path("data/profiles/default.json")
MAX_NARRATIVE_FILE_BYTES = 2 * 1024 * 1024


class NarrativeFileError(ValueError):
    """Raised when a narrative text file cannot be used safely."""


class DecisionSessionCancelled(Exception):
    """Raised when the user cancels before a Decision artifact is written."""


class GapAnalysisSessionCancelled(Exception):
    """Raised when the user cancels before a Gap Analysis artifact is written."""


class EvidenceBankSessionCancelled(Exception):
    """Raised when the user cancels before an Evidence Bank is written."""


class EvidenceEnrichmentSessionCancelled(Exception):
    """Raised when the user cancels before an Enrichment artifact is written."""


class ResumeMaterialSessionCancelled(Exception):
    """Raised when the user cancels before Resume Material is written."""


class ResumeWordingSessionCancelled(Exception):
    """Raised when the user cancels before Resume Wording is written."""


def read_narrative_file(path: Path) -> str:
    try:
        size = path.stat().st_size
    except FileNotFoundError as error:
        raise NarrativeFileError(f"Narrative file does not exist: {path}") from error
    except OSError as error:
        raise NarrativeFileError(f"Could not inspect narrative file: {path}") from error
    if size > MAX_NARRATIVE_FILE_BYTES:
        raise NarrativeFileError(
            f"Narrative file is too large ({size} bytes). Maximum size is {MAX_NARRATIVE_FILE_BYTES} bytes."
        )
    try:
        narrative = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise NarrativeFileError("Narrative file must be UTF-8 text.") from error
    except OSError as error:
        raise NarrativeFileError(f"Could not read narrative file: {path}") from error
    if not narrative.strip():
        raise NarrativeFileError("Narrative file is empty.")
    return narrative


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aarvia", description="Aarvia career navigation tools")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    discover = subparsers.add_parser("discover", help="create or continue a Career Profile")
    discover.add_argument(
        "--profile",
        type=Path,
        default=DEFAULT_PROFILE_PATH,
        help=f"profile JSON path (default: {DEFAULT_PROFILE_PATH})",
    )
    mode = discover.add_mutually_exclusive_group()
    mode.add_argument("--manual", action="store_true", help="use the fixed-field manual questionnaire")
    mode.add_argument("--narrative", action="store_true", help="extract candidates from a natural-language description")
    mode.add_argument(
        "--follow-up",
        action="store_true",
        help="adaptively collect missing topics from an existing Profile",
    )
    mode.add_argument(
        "--narrative-file",
        type=Path,
        help="read a UTF-8 natural-language description from a text file",
    )
    discover.add_argument(
        "--debug-extraction",
        action="store_true",
        help="show in-memory provider output diagnostics when narrative extraction fails",
    )
    discover.add_argument(
        "--debug-full-profile",
        action="store_true",
        help="include the complete merged Profile in follow-up debug output",
    )
    recommend = subparsers.add_parser(
        "recommend", help="create an explainable Role Recommendation from a confirmed Profile"
    )
    recommend.add_argument("--profile", type=Path, required=True, help="existing Career Profile JSON path")
    mapping_input = recommend.add_mutually_exclusive_group()
    mapping_input.add_argument(
        "--mapping-candidates",
        type=Path,
        help="legacy schema 1/2 offline mapping JSON (keeps the legacy Recommendation path)",
    )
    mapping_input.add_argument(
        "--mapping-artifact",
        type=Path,
        help="existing Mapping schema 3/4/5 JSON; reuses it without calling the Provider",
    )
    recommend.add_argument(
        "--mapping-output",
        type=Path,
        help="new Mapping schema 5 output path (default: PROFILE with .mapping.json suffix)",
    )
    recommend.add_argument(
        "--review-artifact",
        type=Path,
        help="Evidence Binding Review JSON for the supplied --mapping-artifact",
    )
    recommend.add_argument(
        "--allocation-review-artifact", type=Path,
        help="Evidence Group Allocation Review JSON for a Mapping schema 5 artifact",
    )
    recommend.add_argument(
        "--output",
        type=Path,
        help="Recommendation JSON path (default: PROFILE with .recommendation.json suffix)",
    )
    recommend.add_argument(
        "--overwrite", action="store_true", help="replace an existing output file atomically"
    )
    recommend.add_argument(
        "--provider-diagnostics-dir",
        type=Path,
        help="opt in to redacted Provider attempt metadata; raw responses are never saved",
    )
    review = subparsers.add_parser(
        "review-evidence",
        help="resolve Mapping 5 evidence groups, then review provisional semantic bindings",
    )
    review.add_argument("--profile", type=Path, required=True, help="existing Career Profile JSON path")
    review.add_argument("--mapping", type=Path, required=True, help="Mapping schema 3/4/5 JSON path")
    review.add_argument("--output", type=Path, required=True, help="Evidence Binding Review JSON path")
    review.add_argument("--allocation-output", type=Path, help="Evidence Group Allocation Review JSON path")
    review.add_argument(
        "--overwrite", action="store_true", help="replace an existing Review output atomically"
    )
    decide = subparsers.add_parser(
        "decide", help="choose and save a career direction from Recommendation schema 7"
    )
    decide.add_argument("--profile", type=Path, required=True, help="existing Career Profile JSON path")
    decide.add_argument(
        "--recommendation", type=Path, required=True,
        help="validated Recommendation schema 7 JSON path",
    )
    decide.add_argument("--output", type=Path, required=True, help="new User Decision JSON path")
    decide.add_argument(
        "--supersede", type=Path,
        help="confirmed User Decision schema 2 to replace with an explicit new revision",
    )
    gaps = subparsers.add_parser(
        "analyze-gaps",
        help="derive a deterministic Dimension-level Gap Analysis from confirmed artifacts",
    )
    gaps.add_argument("--profile", type=Path, required=True, help="Career Profile JSON path")
    gaps.add_argument("--mapping", type=Path, required=True, help="Mapping schema 5 JSON path")
    gaps.add_argument(
        "--recommendation", type=Path, required=True,
        help="Recommendation schema 7 JSON path",
    )
    gaps.add_argument("--decision", type=Path, required=True, help="confirmed Decision schema 2 JSON path")
    gaps.add_argument(
        "--review-artifact", type=Path,
        help="Evidence Binding Review referenced by the Recommendation",
    )
    gaps.add_argument(
        "--allocation-review-artifact", type=Path,
        help="Evidence Group Allocation Review referenced by the Recommendation",
    )
    gaps.add_argument(
        "--superseded-decision", type=Path,
        help="prior confirmed Decision required when the current Decision is a revision",
    )
    gaps.add_argument("--output", type=Path, required=True, help="new Career Gap Analysis JSON path")
    bank = subparsers.add_parser(
        "build-evidence-bank",
        help="build a deterministic fact and capability evidence bank from confirmed artifacts",
    )
    bank.add_argument("--profile", type=Path, required=True, help="Career Profile JSON path")
    bank.add_argument("--mapping", type=Path, required=True, help="Mapping schema 5 JSON path")
    bank.add_argument(
        "--recommendation", type=Path, required=True,
        help="Recommendation schema 7 JSON path",
    )
    bank.add_argument("--decision", type=Path, required=True, help="confirmed Decision schema 2 JSON path")
    bank.add_argument("--gap-analysis", type=Path, required=True, help="Career Gap Analysis schema 2 JSON path")
    bank.add_argument(
        "--review-artifact", type=Path,
        help="Evidence Binding Review referenced by the Recommendation",
    )
    bank.add_argument(
        "--allocation-review-artifact", type=Path,
        help="Evidence Group Allocation Review referenced by the Recommendation",
    )
    bank.add_argument(
        "--superseded-decision", type=Path,
        help="prior confirmed Decision required when the current Decision is a revision",
    )
    bank.add_argument(
        "--supersede", type=Path,
        help="immutable Evidence Bank schema 1 to replace with an explicit new revision",
    )
    bank.add_argument("--output", type=Path, required=True, help="new Evidence Bank JSON path")
    enrichment = subparsers.add_parser(
        "enrich-evidence",
        help="add user-confirmed details to an immutable Evidence Bank",
    )
    enrichment.add_argument("--profile", type=Path, required=True, help="Career Profile JSON path")
    enrichment.add_argument("--mapping", type=Path, required=True, help="Mapping schema 5 JSON path")
    enrichment.add_argument("--recommendation", type=Path, required=True, help="Recommendation schema 7 JSON path")
    enrichment.add_argument("--decision", type=Path, required=True, help="confirmed Decision schema 2 JSON path")
    enrichment.add_argument("--gap-analysis", type=Path, required=True, help="Career Gap Analysis schema 2 JSON path")
    enrichment.add_argument("--evidence-bank", type=Path, required=True, help="Evidence Bank schema 1 JSON path")
    enrichment.add_argument("--review-artifact", type=Path, help="Binding Review referenced by the Recommendation")
    enrichment.add_argument("--allocation-review-artifact", type=Path, help="Allocation Review referenced by the Recommendation")
    enrichment.add_argument("--superseded-decision", type=Path, help="prior confirmed Decision required by a Decision revision")
    enrichment.add_argument("--supersede", type=Path, help="prior Enrichment artifact for an explicit immutable revision")
    enrichment.add_argument("--output", type=Path, required=True, help="new Evidence Enrichment JSON path")
    materials = subparsers.add_parser(
        "prepare-resume-materials",
        help="prepare deterministic role-neutral Resume material from confirmed evidence",
    )
    materials.add_argument("--profile", type=Path, required=True, help="Career Profile JSON path")
    materials.add_argument("--mapping", type=Path, required=True, help="Mapping schema 5 JSON path")
    materials.add_argument("--recommendation", type=Path, required=True, help="Recommendation schema 7 JSON path")
    materials.add_argument("--decision", type=Path, required=True, help="confirmed Decision schema 2 JSON path")
    materials.add_argument("--gap-analysis", type=Path, required=True, help="Career Gap Analysis schema 2 JSON path")
    materials.add_argument("--evidence-bank", type=Path, required=True, help="Evidence Bank schema 1 JSON path")
    materials.add_argument("--evidence-enrichment", type=Path, required=True, help="Evidence Enrichment schema 1 JSON path")
    materials.add_argument("--review-artifact", type=Path, help="Binding Review referenced by the Recommendation")
    materials.add_argument("--allocation-review-artifact", type=Path, help="Allocation Review referenced by the Recommendation")
    materials.add_argument("--superseded-decision", type=Path, help="prior confirmed Decision required by a Decision revision")
    materials.add_argument("--superseded-enrichment", type=Path, help="prior Enrichment required by an Enrichment revision")
    materials.add_argument("--supersede", type=Path, help="prior Resume Material artifact for an explicit immutable revision")
    materials.add_argument("--output", type=Path, required=True, help="new Resume Material JSON path")
    wording = subparsers.add_parser(
        "review-resume-wording",
        help="compose and review user-authored wording from verified Resume materials",
    )
    wording.add_argument("--profile", type=Path, required=True, help="Career Profile JSON path")
    wording.add_argument("--mapping", type=Path, required=True, help="Mapping schema 5 JSON path")
    wording.add_argument("--recommendation", type=Path, required=True, help="Recommendation schema 7 JSON path")
    wording.add_argument("--decision", type=Path, required=True, help="confirmed Decision schema 2 JSON path")
    wording.add_argument("--gap-analysis", type=Path, required=True, help="Career Gap Analysis schema 2 JSON path")
    wording.add_argument("--evidence-bank", type=Path, required=True, help="Evidence Bank schema 1 JSON path")
    wording.add_argument("--evidence-enrichment", type=Path, required=True, help="Evidence Enrichment schema 1 JSON path")
    wording.add_argument("--resume-material", type=Path, required=True, help="Resume Material schema 1 JSON path")
    wording.add_argument("--review-artifact", type=Path, help="Binding Review referenced by the Recommendation")
    wording.add_argument("--allocation-review-artifact", type=Path, help="Allocation Review referenced by the Recommendation")
    wording.add_argument("--superseded-decision", type=Path, help="prior Decision required by a Decision revision")
    wording.add_argument("--superseded-enrichment", type=Path, help="prior Enrichment required by an Enrichment revision")
    wording.add_argument("--superseded-resume-material", type=Path, help="prior Resume Material required by a material revision")
    wording.add_argument("--supersede", type=Path, help="prior Wording Review for an immutable revision")
    wording.add_argument("--output", type=Path, required=True, help="new Resume Wording Review JSON path")
    return parser


def _ensure_output_available(path: Path, *, overwrite: bool, label: str) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{label} output already exists: {path}. Use --overwrite to replace it."
        )


def _print_recommendation(
    artifact,
    candidates,
    *,
    output_path: Path,
    output_fn: OutputFunction,
) -> None:
    output_fn("Role recommendations")
    rejection_report = mapping_validation_report_from_warnings(
        candidates.conflict_warnings
    )
    if rejection_report is not None:
        output_fn(
            "Warning: "
            f"{rejection_report.rejected_count} Provider mapping candidate(s) "
            "were rejected; affected evidence remains unknown."
        )
    for result in artifact.role_results:
        rank = "Unranked" if result.rank is None else f"Rank {result.rank}"
        marker = " (provisional)" if result.provisional else ""
        tier = ""
        if result.ranking_tier is not None:
            tier = {
                "core_supported": " - Core evidence",
                "extended_only": " - Extended evidence only",
                "unranked": " - Unranked",
            }[result.ranking_tier.value]
        output_fn(
            f"{rank}: {result.role_id.replace('_', ' ').title()}{marker}{tier}"
        )
        if result.tied_role_ids:
            output_fn(
                "  Tied with: "
                + ", ".join(
                    role_id.replace("_", " ").title()
                    for role_id in result.tied_role_ids
                )
            )
        output_fn(
            f"  Current Fit: {result.core_current_fit.band.value.replace('_', ' ').title()}"
        )
        output_fn(
            f"  Extended Fit: {result.extended_current_fit.band.value.replace('_', ' ').title()}"
        )
        output_fn(
            f"  Directional Fit: {result.directional_fit.band.value.replace('_', ' ').title()}"
        )
        output_fn(f"  Constraints: {result.constraint.status.value.replace('_', ' ').title()}")
        output_fn(f"  Confidence: {result.recommendation_confidence.result.value.title()}")
        output_fn(f"  Why: {result.rationale}")
    if artifact.follow_up_questions:
        output_fn("Follow-up questions")
        for question in artifact.follow_up_questions:
            output_fn(f"- {question.question}")
    output_fn(f"Saved to: {output_path}")
    output_fn("No career direction was selected. User Decision remains separate.")


def _provisional_semantic_bindings(candidates, *, extra_binding_ids: frozenset[str] = frozenset()) -> tuple[ProfileCriterionEvidenceBinding, ...]:
    values = list(candidates.mappings)
    values.extend(
        member for group in candidates.unresolved_evidence_groups for member in group.members
        if member.binding_id in extra_binding_ids
    )
    return tuple(
        sorted(
            (
                item for item in values
                if isinstance(item, ProfileCriterionEvidenceBinding)
                and item.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
            ),
            key=lambda item: (
                item.role_id, item.dimension_id, item.criterion_id, item.binding_id
            ),
        )
    )


def _run_recommend(
    arguments,
    *,
    mapper: ProfileDimensionMapper | None,
    output_fn: OutputFunction,
) -> int:
    profile = load_profile(arguments.profile)
    catalog = production_role_catalog()
    rubric = production_capability_rubric()
    output_path = arguments.output or arguments.profile.with_suffix(".recommendation.json")
    protected_inputs = {
        path for path in (
            arguments.profile, arguments.mapping_candidates, arguments.mapping_artifact,
            arguments.review_artifact, arguments.allocation_review_artifact,
        ) if path is not None
    }
    if output_path in protected_inputs:
        raise Phase2ValidationError(
            "Recommendation output must not replace an input artifact"
        )
    _ensure_output_available(
        output_path, overwrite=arguments.overwrite, label="Recommendation"
    )

    if arguments.review_artifact is not None and arguments.mapping_artifact is None:
        raise Phase2ValidationError("--review-artifact requires --mapping-artifact")
    if arguments.allocation_review_artifact is not None and arguments.mapping_artifact is None:
        raise Phase2ValidationError("--allocation-review-artifact requires --mapping-artifact")
    if arguments.mapping_output is not None and (
        arguments.mapping_candidates is not None or arguments.mapping_artifact is not None
    ):
        raise Phase2ValidationError(
            "--mapping-output is only valid when creating a new Provider mapping"
        )

    review_artifact = None
    allocation_review_artifact = None
    mapping_output_path: Path | None = None
    if arguments.mapping_candidates is not None:
        candidates = load_mapping_candidates(
            arguments.mapping_candidates, profile=profile, rubric=rubric, catalog=catalog
        )
        if candidates.schema_version not in {1, 2}:
            raise Phase2ValidationError(
                "--mapping-candidates accepts only legacy schema 1/2; use --mapping-artifact for schema 3"
            )
    elif arguments.mapping_artifact is not None:
        candidates = load_mapping_candidates(
            arguments.mapping_artifact, profile=profile, rubric=rubric, catalog=catalog
        )
        if candidates.schema_version not in {3, 4, 5}:
            raise Phase2ValidationError("--mapping-artifact requires Mapping schema 3, 4, or 5")
        if arguments.allocation_review_artifact is not None:
            allocation_review_artifact = load_evidence_group_allocation_reviews(
                arguments.allocation_review_artifact, profile=profile, rubric=rubric,
                mapping=candidates, catalog=catalog,
            )
        if arguments.review_artifact is not None:
            review_artifact = load_evidence_binding_reviews(
                arguments.review_artifact,
                profile=profile,
                rubric=rubric,
                mapping=candidates,
                catalog=catalog,
            )
    else:
        mapping_output_path = arguments.mapping_output or arguments.profile.with_suffix(
            ".mapping.json"
        )
        if mapping_output_path in {arguments.profile, output_path}:
            raise Phase2ValidationError(
                "Profile, Mapping, and Recommendation paths must be different"
            )
        _ensure_output_available(
            mapping_output_path, overwrite=arguments.overwrite, label="Mapping"
        )
        active_mapper = mapper or OpenAIProfileDimensionMapper(
            diagnostics_dir=arguments.provider_diagnostics_dir,
            mapping_schema_version=5,
        )
        candidates = active_mapper.map(profile, rubric, catalog)
        if candidates.schema_version != 5:
            raise Phase2ValidationError(
                "the default recommend flow requires Mapping schema 5"
            )

    baseline = None
    if review_artifact is not None:
        baseline = build_role_recommendation(
            profile=profile,
            mapping_candidates=candidates,
            rubric=rubric,
            catalog=catalog,
            created_at=datetime.now(timezone.utc).isoformat(),
            allocation_reviews=allocation_review_artifact,
        )
    created_at = datetime.now(timezone.utc).isoformat()
    artifact = build_role_recommendation(
        profile=profile,
        mapping_candidates=candidates,
        rubric=rubric,
        catalog=catalog,
        created_at=created_at,
        evidence_reviews=review_artifact,
        allocation_reviews=allocation_review_artifact,
    )

    if mapping_output_path is not None:
        candidates.validate(profile=profile, rubric=rubric, catalog=catalog)
        artifact.validate(
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=candidates,
        )
        save_phase2_json_transaction(
            (
                (mapping_output_path, candidates.to_dict()),
                (output_path, artifact.to_dict()),
            )
        )
    else:
        save_role_recommendation(
            artifact,
            output_path,
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping_candidates=candidates if candidates.schema_version in {3, 4, 5} else None,
            evidence_reviews=review_artifact,
            allocation_reviews=allocation_review_artifact,
        )

    _print_recommendation(
        artifact, candidates, output_path=output_path, output_fn=output_fn
    )
    provisional_count = len(_provisional_semantic_bindings(candidates))
    unresolved_count = len(candidates.unresolved_evidence_groups)
    if mapping_output_path is not None:
        output_fn(f"Mapping saved to: {mapping_output_path}")
        output_fn(f"Provisional semantic bindings requiring review: {provisional_count}")
        if unresolved_count:
            unresolved_candidates = sum(len(group.members) for group in candidates.unresolved_evidence_groups)
            output_fn(f"Unresolved evidence groups: {unresolved_count} ({unresolved_candidates} candidates); they currently contribute nothing to scoring.")
        if provisional_count or unresolved_count:
            suggested_review = mapping_output_path.with_suffix(".review.json")
            output_fn(
                "Next: aarvia review-evidence "
                f"--profile {arguments.profile} --mapping {mapping_output_path} "
                f"--output {suggested_review}"
            )
    if review_artifact is not None:
        counts = {decision: 0 for decision in BindingReviewDecision}
        for review in review_artifact.reviews:
            counts[review.decision] += 1
        output_fn(
            "Evidence review applied: "
            f"{counts[BindingReviewDecision.CONFIRMED]} confirmed, "
            f"{counts[BindingReviewDecision.REJECTED]} rejected, "
            f"{counts[BindingReviewDecision.DEFERRED]} deferred."
        )
        output_fn("Recommendation changes")
        assert baseline is not None
        baseline_roles = {item.role_id: item for item in baseline.role_results}
        for result in artifact.role_results:
            before = baseline_roles[result.role_id]
            output_fn(
                f"- {result.role_id.replace('_', ' ').title()}: "
                f"{before.extended_current_fit.band.value.replace('_', ' ')} -> "
                f"{result.extended_current_fit.band.value.replace('_', ' ')}"
            )
    return 0


def _run_review_evidence(
    arguments,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> int:
    _ensure_output_available(arguments.output, overwrite=arguments.overwrite, label="Evidence Review")
    allocation_output = arguments.allocation_output or arguments.output.with_name(arguments.output.stem + ".allocation.json")
    if len({arguments.output, allocation_output, arguments.profile, arguments.mapping}) != 4:
        raise Phase2ValidationError("Profile, Mapping, Binding Review, and Allocation Review paths must be different")
    profile = load_profile(arguments.profile)
    catalog = production_role_catalog()
    rubric = production_capability_rubric()
    candidates = load_mapping_candidates(
        arguments.mapping, profile=profile, rubric=rubric, catalog=catalog
    )
    if candidates.schema_version not in {3, 4, 5}:
        raise Phase2ValidationError("review-evidence requires Mapping schema 3, 4, or 5")
    if candidates.unresolved_evidence_groups:
        _ensure_output_available(
            allocation_output,
            overwrite=arguments.overwrite,
            label="Allocation Review",
        )
    reviewed_at = datetime.now(timezone.utc).isoformat()
    allocation_decisions = {}
    selected_binding_ids: set[str] = set()
    for index, group in enumerate(candidates.unresolved_evidence_groups, 1):
        output_fn(f"Unresolved evidence group {index} of {len(candidates.unresolved_evidence_groups)}")
        output_fn(f"Role: {group.role_id.replace('_', ' ').title()}")
        output_fn(f"Profile excerpt: {group.members[0].atomic_evidence.exact_excerpt}")
        dimensions = list(group.allowed_primary_dimension_ids)
        for choice, dimension_id in enumerate(dimensions, 1):
            dimension = rubric.dimension(dimension_id)
            members = tuple(
                member for member in group.members
                if member.dimension_id == dimension_id
            )
            criterion_text = {
                criterion.criterion_id: criterion.text
                for criterion in dimension.criteria
            }
            criteria = sorted({
                criterion_text[member.criterion_id] for member in members
            })
            impacts = sorted({
                f"{member.derived_match_status.value.replace('_', ' ')} / "
                f"{member.derived_evidence_strength.value}"
                for member in members
            })
            evidence_classes = sorted({member.evidence_class.value for member in members})
            output_fn(f"{choice}. {dimension.name}")
            output_fn(f"   Criteria: {'; '.join(criteria)}")
            output_fn(f"   Evidence class: {', '.join(evidence_classes)}")
            output_fn(f"   Provisional impact: {', '.join(impacts)}")
        output_fn("Primary contribution: 1.0. Optional secondary: at most 0.3 and adjacent/weak.")
        while True:
            answer = input_fn("Primary number, [r]eject, [d]efer, or [q]uit: ").strip().lower()
            if answer == "q":
                output_fn("Evidence review cancelled. No files were changed."); return 0
            if answer in {"r", "d"}:
                decision = AllocationReviewDecision.REJECTED if answer == "r" else AllocationReviewDecision.DEFERRED
                allocation_decisions[group.evidence_group_id] = (decision, None, None, AllocationReviewerType.PROFILE_OWNER, reviewed_at)
                break
            if answer.isdigit() and 1 <= int(answer) <= len(dimensions):
                primary = dimensions[int(answer)-1]
                secondary_choices = [item for item in group.allowed_secondary_dimension_ids if item != primary]
                secondary_answer = input_fn("Optional secondary number from the same list, or Enter for none: ").strip()
                secondary = None
                if secondary_answer:
                    if not secondary_answer.isdigit() or not 1 <= int(secondary_answer) <= len(dimensions):
                        output_fn("Invalid secondary choice; choose the group again."); continue
                    secondary = dimensions[int(secondary_answer)-1]
                    if secondary == primary or secondary not in secondary_choices:
                        output_fn("Secondary must differ from primary; choose the group again."); continue
                allocation_decisions[group.evidence_group_id] = (AllocationReviewDecision.RESOLVED, primary, secondary, AllocationReviewerType.PROFILE_OWNER, reviewed_at)
                selected_binding_ids.update(member.binding_id for member in group.members if member.dimension_id in {primary, secondary})
                break
            output_fn("Enter a listed primary number, r, d, or q.")
    allocation_artifact = (
        create_evidence_group_allocation_review_artifact(profile=profile, rubric=rubric, mapping=candidates, catalog=catalog, decisions=allocation_decisions)
        if candidates.unresolved_evidence_groups else None
    )
    bindings = _provisional_semantic_bindings(candidates, extra_binding_ids=frozenset(selected_binding_ids))
    if not bindings and allocation_artifact is None:
        output_fn("No evidence requires review. No file was written.")
        return 0

    output_fn(f"Provisional semantic bindings to review: {len(bindings)}")
    output_fn("Choose confirmed, rejected, deferred, or q to cancel the whole review.")
    decisions = {}
    aliases = {
        "c": BindingReviewDecision.CONFIRMED,
        "confirmed": BindingReviewDecision.CONFIRMED,
        "r": BindingReviewDecision.REJECTED,
        "rejected": BindingReviewDecision.REJECTED,
        "d": BindingReviewDecision.DEFERRED,
        "deferred": BindingReviewDecision.DEFERRED,
    }
    for index, binding in enumerate(bindings, 1):
        dimension = rubric.dimension(binding.dimension_id)
        criterion = next(
            item.text for item in dimension.criteria
            if item.criterion_id == binding.criterion_id
        )
        output_fn(f"Binding {index} of {len(bindings)}")
        output_fn(f"Role: {binding.role_id.replace('_', ' ').title()}")
        output_fn(f"Dimension: {dimension.name}")
        output_fn(f"Criterion: {criterion}")
        output_fn(f"Profile excerpt: {binding.atomic_evidence.exact_excerpt}")
        output_fn(f"Evidence class: {binding.evidence_class.value.replace('_', ' ')}")
        output_fn(
            "Current provisional impact: "
            f"{(
                binding.allocated_match_status
                if isinstance(binding, AllocatedProfileCriterionEvidenceBinding)
                else binding.derived_match_status
            ).value.replace('_', ' ')}"
        )
        while True:
            answer = input_fn("Decision [c]onfirmed/[r]ejected/[d]eferred/[q]uit: ").strip().lower()
            if answer == "q":
                output_fn("Evidence review cancelled. No files were changed.")
                return 0
            if answer in aliases:
                decisions[binding.binding_id] = (
                    aliases[answer], BindingReviewerType.PROFILE_OWNER,
                    reviewed_at,
                )
                break
            output_fn("Please enter c, r, d, or q.")

    save_answer = input_fn("Save all evidence review decisions? [y/N]: ").strip().lower()
    if save_answer != "y":
        output_fn("Evidence review cancelled. No files were changed.")
        return 0
    artifact = create_evidence_binding_review_artifact(
        profile=profile,
        rubric=rubric,
        mapping=candidates,
        catalog=catalog,
        decisions=decisions,
    )
    writes = [(arguments.output, artifact.to_dict())]
    if allocation_artifact is not None:
        writes.append((allocation_output, allocation_artifact.to_dict()))
    save_phase2_json_transaction(tuple(writes))
    counts = {decision: 0 for decision in BindingReviewDecision}
    for review in artifact.reviews:
        counts[review.decision] += 1
    output_fn(
        "Evidence review saved: "
        f"{counts[BindingReviewDecision.CONFIRMED]} confirmed, "
        f"{counts[BindingReviewDecision.REJECTED]} rejected, "
        f"{counts[BindingReviewDecision.DEFERRED]} deferred."
    )
    output_fn(f"Saved to: {arguments.output}")
    if allocation_artifact is not None:
        output_fn(f"Allocation review saved to: {allocation_output}")
    return 0


def _decision_answer(prompt: str, *, input_fn: InputFunction) -> str:
    answer = input_fn(prompt).strip()
    if answer.lower() == "q":
        raise DecisionSessionCancelled
    return answer


def _ask_role_numbers(
    prompt: str,
    *,
    role_ids: tuple[str, ...],
    input_fn: InputFunction,
    output_fn: OutputFunction,
    required: bool = False,
    maximum: int | None = None,
    excluded: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    while True:
        answer = _decision_answer(prompt, input_fn=input_fn)
        if not answer:
            if required:
                output_fn("Please choose one role, or enter q to cancel.")
                continue
            return ()
        pieces = tuple(item.strip() for item in answer.split(",") if item.strip())
        try:
            indexes = tuple(int(item) for item in pieces)
        except ValueError:
            output_fn("Enter role numbers separated by commas.")
            continue
        if (
            not indexes
            or len(indexes) != len(set(indexes))
            or any(index < 1 or index > len(role_ids) for index in indexes)
        ):
            output_fn("Choose each listed role at most once using its number.")
            continue
        selected = tuple(role_ids[index - 1] for index in indexes)
        if maximum is not None and len(selected) > maximum:
            output_fn(f"Choose at most {maximum} role(s).")
            continue
        conflicts = set(selected) & set(excluded)
        if conflicts:
            output_fn(
                "A role cannot appear in more than one decision category: "
                + ", ".join(sorted(conflicts))
            )
            continue
        return selected


def _run_decide(
    arguments,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    profile = load_profile(arguments.profile)
    catalog = production_role_catalog()
    rubric = production_capability_rubric()
    protected_inputs = {
        arguments.profile,
        arguments.recommendation,
        *(() if arguments.supersede is None else (arguments.supersede,)),
    }
    if arguments.output in protected_inputs:
        raise Phase2ValidationError("Decision output must not replace an input artifact")
    _ensure_output_available(arguments.output, overwrite=False, label="Decision")

    recommendation = RoleRecommendationArtifact.from_dict(
        load_phase2_json(arguments.recommendation)
    )
    recommendation.validate_decision_source(
        profile=profile, rubric=rubric, catalog=catalog
    )
    superseded = (
        None
        if arguments.supersede is None
        else load_user_role_decision_revision_source(
            arguments.supersede, catalog=catalog
        )
    )

    output_fn("Career direction decision")
    role_ids = tuple(item.role_id for item in recommendation.role_results)
    for index, item in enumerate(recommendation.role_results, 1):
        role = catalog.role(item.role_id)
        rank = "Unranked" if item.rank is None else f"Rank {item.rank}"
        tier = item.ranking_tier.value.replace("_", " ").title()
        provisional = "Yes" if item.provisional else "No"
        constraint = item.constraint.status.value.replace("_", " ").title()
        confidence = item.recommendation_confidence.result.value.replace("_", " ").title()
        output_fn(
            f"{index}. {role.display_name} | {rank} | {tier} | "
            f"Provisional: {provisional} | Constraints: {constraint} | "
            f"Confidence: {confidence}"
        )

    while True:
        mode = _decision_answer(
            "Choose [c] confirm a direction, [d] defer, or [q] cancel: ",
            input_fn=input_fn,
        ).lower()
        if mode in {"c", "d"}:
            break
        output_fn("Enter c, d, or q.")

    status = DecisionStatus.CONFIRMED if mode == "c" else DecisionStatus.DEFERRED
    primary_id: str | None = None
    secondary_ids: tuple[str, ...] = ()
    if status == DecisionStatus.CONFIRMED:
        primary_id = _ask_role_numbers(
            "Primary role number: ",
            role_ids=role_ids,
            input_fn=input_fn,
            output_fn=output_fn,
            required=True,
            maximum=1,
        )[0]
        secondary_ids = _ask_role_numbers(
            "Secondary role numbers (up to 2, comma-separated; blank for none): ",
            role_ids=role_ids,
            input_fn=input_fn,
            output_fn=output_fn,
            maximum=2,
            excluded=frozenset({primary_id}),
        )
    selected_ids = frozenset(
        (*(() if primary_id is None else (primary_id,)), *secondary_ids)
    )
    rejected_ids = _ask_role_numbers(
        "Role numbers to reject (comma-separated; blank for none): ",
        role_ids=role_ids,
        input_fn=input_fn,
        output_fn=output_fn,
        excluded=selected_ids,
    )
    explore_ids = _ask_role_numbers(
        "Role numbers to explore later (comma-separated; blank for none): ",
        role_ids=role_ids,
        input_fn=input_fn,
        output_fn=output_fn,
        excluded=frozenset((*selected_ids, *rejected_ids)),
    )
    reason = _decision_answer(
        "Reason or note (optional): ", input_fn=input_fn
    )
    timestamp = now_fn()
    decision = create_user_role_decision(
        status=status,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=(None if primary_id is None else SelectedRole(primary_id)),
        secondary_roles=tuple(SelectedRole(role_id) for role_id in secondary_ids),
        rejected_role_ids=rejected_ids,
        explore_later_role_ids=explore_ids,
        user_reason=reason or None,
        created_at=timestamp,
        updated_at=timestamp,
        superseded_decision=superseded,
    )

    def names(values: tuple[str, ...]) -> str:
        return ", ".join(catalog.role(role_id).display_name for role_id in values) or "None"

    output_fn("Decision summary")
    output_fn(f"Status: {decision.status.value.title()}")
    output_fn(
        "Primary: "
        + ("None" if decision.primary_role is None else catalog.role(decision.primary_role.role_id).display_name)
    )
    output_fn(f"Secondary: {names(tuple(item.role_id for item in decision.secondary_roles))}")
    output_fn(f"Rejected: {names(decision.rejected_role_ids)}")
    output_fn(f"Explore later: {names(decision.explore_later_role_ids)}")
    output_fn(f"Reason: {decision.user_reason or 'Not provided'}")
    if decision.supersedes_decision_id is not None:
        output_fn(f"Supersedes: {decision.supersedes_decision_id}")
    confirm = _decision_answer(
        "Save this career direction decision? [y/N]: ", input_fn=input_fn
    ).lower()
    if confirm != "y":
        output_fn("Decision not saved. No files were changed.")
        return 0
    save_user_role_decision(
        decision,
        arguments.output,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
        superseded_decision=superseded,
    )
    output_fn(f"Decision saved to: {arguments.output}")
    return 0


def _gap_answer(prompt: str, *, input_fn: InputFunction) -> str:
    answer = input_fn(prompt).strip()
    if answer.lower() == "q":
        raise GapAnalysisSessionCancelled
    return answer


def _print_gap_analysis(
    artifact: CareerGapAnalysis,
    *,
    rubric: CapabilityRubric,
    output_fn: OutputFunction,
) -> None:
    output_fn("Career gap analysis")
    analyses = (artifact.primary_role_analysis, *artifact.secondary_role_analyses)
    labels = {
        GapClassification.CONFIRMED_STRENGTH: "Confirmed strengths",
        GapClassification.DEVELOPING_CAPABILITY: "Development areas",
        GapClassification.TRANSFERABLE_FOUNDATION: "Transferable foundations",
        GapClassification.UNKNOWN_EVIDENCE: "Unknown evidence",
        GapClassification.CAPABILITY_GAP: "Capability gaps",
    }
    dimension_names = {
        item.dimension_id: item.name for item in rubric.dimensions
    }
    for role in analyses:
        output_fn(
            f"{role.selection_type.value.title()}: {role.role_id.replace('_', ' ').title()}"
        )
        for classification, label in labels.items():
            names = tuple(
                dimension_names[item.dimension_id]
                for item in role.dimensions
                if item.gap_classification == classification
            )
            output_fn(
                f"  {label}: "
                + (", ".join(names) or "None")
            )
    summary = artifact.summary
    output_fn(
        "Summary: "
        f"{summary.confirmed_strength_count} strengths, "
        f"{summary.development_area_count} development areas, "
        f"{summary.transferable_foundation_count} transferable foundations, "
        f"{summary.unknown_evidence_count} unknowns, "
        f"{summary.capability_gap_count} explicit gaps."
    )


def _run_analyze_gaps(
    arguments,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    protected_inputs = {
        arguments.profile,
        arguments.mapping,
        arguments.recommendation,
        arguments.decision,
        *(() if arguments.review_artifact is None else (arguments.review_artifact,)),
        *(
            ()
            if arguments.allocation_review_artifact is None
            else (arguments.allocation_review_artifact,)
        ),
        *(
            ()
            if arguments.superseded_decision is None
            else (arguments.superseded_decision,)
        ),
    }
    if arguments.output in protected_inputs:
        raise Phase2ValidationError("Gap Analysis output must not replace an input artifact")
    _ensure_output_available(arguments.output, overwrite=False, label="Gap Analysis")

    profile = load_profile(arguments.profile)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    mapping = load_mapping_candidates(
        arguments.mapping, profile=profile, rubric=rubric, catalog=catalog
    )
    evidence_reviews = (
        None
        if arguments.review_artifact is None
        else load_evidence_binding_reviews(
            arguments.review_artifact,
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
        )
    )
    allocation_reviews = (
        None
        if arguments.allocation_review_artifact is None
        else load_evidence_group_allocation_reviews(
            arguments.allocation_review_artifact,
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
        )
    )
    recommendation = load_role_recommendation(
        arguments.recommendation,
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping_candidates=mapping,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    superseded = (
        None
        if arguments.superseded_decision is None
        else load_user_role_decision_revision_source(
            arguments.superseded_decision, catalog=catalog
        )
    )
    decision = load_user_role_decision(
        arguments.decision,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
        superseded_decision=superseded,
    )
    artifact = build_career_gap_analysis(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded,
        created_at=now_fn(),
    )
    _print_gap_analysis(artifact, rubric=rubric, output_fn=output_fn)
    confirmation = _gap_answer(
        "Save this Career Gap Analysis? [y/N]: ", input_fn=input_fn
    ).lower()
    if confirmation != "y":
        output_fn("Gap Analysis not saved. No files were changed.")
        return 0
    save_career_gap_analysis(
        artifact,
        arguments.output,
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_decision=superseded,
    )
    output_fn(f"Gap Analysis saved to: {arguments.output}")
    return 0


def _evidence_bank_answer(prompt: str, *, input_fn: InputFunction) -> str:
    answer = input_fn(prompt).strip()
    if answer.lower() == "q":
        raise EvidenceBankSessionCancelled
    return answer


def _print_evidence_bank(
    artifact: EvidenceBank, *, output_fn: OutputFunction
) -> None:
    summary = artifact.summary
    output_fn("Evidence Bank")
    output_fn(f"Confirmed profile facts: {summary.confirmed_profile_fact_count}")
    output_fn(
        "Confirmed capability support: "
        f"{summary.confirmed_capability_support_count}"
    )
    output_fn(f"Developing evidence: {summary.developing_support_count}")
    output_fn(f"Transferable foundations: {summary.transferable_support_count}")
    output_fn(f"Unknown evidence needs: {summary.unknown_evidence_need_count}")
    output_fn(
        "Explicit development needs: "
        f"{summary.explicit_development_need_count}"
    )


def _run_build_evidence_bank(
    arguments,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    protected_inputs = {
        arguments.profile,
        arguments.mapping,
        arguments.recommendation,
        arguments.decision,
        arguments.gap_analysis,
        *(() if arguments.review_artifact is None else (arguments.review_artifact,)),
        *(
            ()
            if arguments.allocation_review_artifact is None
            else (arguments.allocation_review_artifact,)
        ),
        *(
            ()
            if arguments.superseded_decision is None
            else (arguments.superseded_decision,)
        ),
        *(() if arguments.supersede is None else (arguments.supersede,)),
    }
    if arguments.output in protected_inputs:
        raise Phase2ValidationError("Evidence Bank output must not replace an input artifact")
    if arguments.output.exists():
        raise FileExistsError(
            f"Evidence Bank output already exists: {arguments.output}. "
            "Choose a new output path."
        )

    profile = load_profile(arguments.profile)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    mapping = load_mapping_candidates(
        arguments.mapping, profile=profile, rubric=rubric, catalog=catalog
    )
    evidence_reviews = (
        None
        if arguments.review_artifact is None
        else load_evidence_binding_reviews(
            arguments.review_artifact,
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
        )
    )
    allocation_reviews = (
        None
        if arguments.allocation_review_artifact is None
        else load_evidence_group_allocation_reviews(
            arguments.allocation_review_artifact,
            profile=profile,
            rubric=rubric,
            mapping=mapping,
            catalog=catalog,
        )
    )
    recommendation = load_role_recommendation(
        arguments.recommendation,
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping_candidates=mapping,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    superseded_decision = (
        None
        if arguments.superseded_decision is None
        else load_user_role_decision_revision_source(
            arguments.superseded_decision, catalog=catalog
        )
    )
    decision = load_user_role_decision(
        arguments.decision,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
        superseded_decision=superseded_decision,
    )
    gap_analysis = load_career_gap_analysis(
        arguments.gap_analysis,
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
    superseded_bank = (
        None
        if arguments.supersede is None
        else load_evidence_bank_revision_source(
            arguments.supersede,
            profile=profile,
            rubric=rubric,
            catalog=catalog,
            mapping=mapping,
            recommendation=recommendation,
            decision=decision,
            gap_analysis=gap_analysis,
            evidence_reviews=evidence_reviews,
            allocation_reviews=allocation_reviews,
            superseded_decision=superseded_decision,
        )
    )
    artifact = build_evidence_bank(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap_analysis,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_bank=superseded_bank,
        superseded_decision=superseded_decision,
        created_at=now_fn(),
    )
    _print_evidence_bank(artifact, output_fn=output_fn)
    confirmation = _evidence_bank_answer(
        "Save this Evidence Bank? [y/N]: ", input_fn=input_fn
    ).lower()
    if confirmation != "y":
        output_fn("Evidence Bank not saved. No files were changed.")
        return 0
    save_evidence_bank(
        artifact,
        arguments.output,
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap_analysis,
        evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
        superseded_bank=superseded_bank,
        superseded_decision=superseded_decision,
    )
    output_fn(f"Evidence Bank saved to: {arguments.output}")
    return 0


def _enrichment_answer(prompt: str, *, input_fn: InputFunction) -> str:
    answer = input_fn(prompt)
    if answer.strip().lower() == "q":
        raise EvidenceEnrichmentSessionCancelled
    return answer


def _source_label(source) -> str:
    values = [value for _, value in source.identity if value]
    return " - ".join(values) if values else source.record_type.value.replace("_", " ").title()


def _enrichment_choice(
    prompt: str, allowed: set[str], *, input_fn: InputFunction,
    output_fn: OutputFunction,
) -> str:
    while True:
        answer = _enrichment_answer(prompt, input_fn=input_fn).strip().lower()
        if answer in allowed:
            return answer
        output_fn("Please enter one of: " + ", ".join(sorted(allowed)) + ", or q")


def _enrichment_yes_no(
    prompt: str, *, input_fn: InputFunction, output_fn: OutputFunction
) -> bool:
    return _enrichment_choice(
        prompt, {"y", "n"}, input_fn=input_fn, output_fn=output_fn
    ) == "y"


def _parse_evidence_numbers(value: str, *, item_count: int) -> tuple[int, ...]:
    """Parse one-based Evidence choices without normalizing invalid input away."""
    if not value.strip():
        return ()
    tokens = value.split(",")
    stripped = [token.strip() for token in tokens]
    if any(not token for token in stripped):
        raise Phase2ValidationError("empty evidence number entries are not allowed")
    try:
        indices = tuple(int(token) for token in stripped)
    except ValueError as error:
        raise Phase2ValidationError("each evidence number must be an integer") from error
    if len(indices) != len(set(indices)):
        raise Phase2ValidationError("duplicate evidence numbers are not allowed")
    if any(index < 1 or index > item_count for index in indices):
        raise Phase2ValidationError(
            f"evidence numbers must be between 1 and {item_count}"
        )
    return tuple(sorted(indices))


_ENRICHMENT_STRUCTURED_TYPES = {
    EnrichmentClaimType.METRIC,
    EnrichmentClaimType.SCALE,
    EnrichmentClaimType.TECHNOLOGY,
    EnrichmentClaimType.COMPLETION_STATUS,
    EnrichmentClaimType.TIMELINE,
}
_CLAIM_FIELDS = (
    "claim_type", "statement", "evidence_numbers", "relationship",
    "temporality", "scope", "structured_value",
)


def _claim_draft(claim=None, *, source_items=()):
    if claim is None:
        return {
            "claim_type": None, "statement": None, "evidence_numbers": None,
            "relationship": None, "temporality": None, "scope": None,
            "structured_value": None, "_completed_fields": set(),
        }
    item_numbers = {
        item.evidence_item_id: index for index, item in enumerate(source_items, 1)
    }
    return {
        "claim_type": claim.claim_type,
        "statement": claim.user_supplied_statement,
        "evidence_numbers": tuple(item_numbers[item_id] for item_id in claim.evidence_item_ids),
        "relationship": claim.relationship,
        "temporality": claim.temporality,
        "scope": claim.scope,
        "structured_value": claim.structured_value,
        "_completed_fields": set(_CLAIM_FIELDS),
    }


def _claim_field_order(draft) -> tuple[str, ...]:
    fields = _CLAIM_FIELDS[:-1]
    if draft["claim_type"] in _ENRICHMENT_STRUCTURED_TYPES:
        fields += ("structured_value",)
    return fields


def _edit_claim_field(
    field: str, draft, *, source_items, input_fn: InputFunction,
    output_fn: OutputFunction,
) -> bool:
    """Edit one draft field. Return False when the user requests back."""
    if field == "claim_type":
        values = "/".join(item.value for item in EnrichmentClaimType)
        while True:
            answer = _enrichment_answer(
                f"Detail type [{values}/b/q]: ", input_fn=input_fn
            ).strip().lower()
            if answer == "b":
                return False
            try:
                draft[field] = EnrichmentClaimType(answer)
            except ValueError:
                output_fn("Invalid detail type. Please try again.")
                continue
            if draft[field] not in _ENRICHMENT_STRUCTURED_TYPES:
                draft["structured_value"] = None
                draft["_completed_fields"].discard("structured_value")
            return True
    if field == "statement":
        while True:
            answer = _enrichment_answer(
                "Completed or ongoing fact in your own words [b/q]: ", input_fn=input_fn
            )
            if answer.strip().lower() == "b":
                return False
            if not answer.strip():
                output_fn("Statement cannot be blank.")
                continue
            draft[field] = answer
            return True
    if field == "evidence_numbers":
        while True:
            answer = _enrichment_answer(
                "Evidence numbers (comma-separated; blank for source-level context; b/q): ",
                input_fn=input_fn,
            )
            if answer.strip().lower() == "b":
                return False
            try:
                draft[field] = _parse_evidence_numbers(
                    answer, item_count=len(source_items)
                )
            except Phase2ValidationError as error:
                output_fn(f"Invalid evidence numbers: {error}")
                continue
            return True
    enum_fields = {
        "relationship": EnrichmentRelationship,
        "temporality": EnrichmentTemporality,
        "scope": EnrichmentScope,
    }
    if field in enum_fields:
        enum_type = enum_fields[field]
        values = "/".join(item.value for item in enum_type)
        while True:
            answer = _enrichment_answer(
                f"{field.replace('_', ' ').title()} [{values}/b/q]: ",
                input_fn=input_fn,
            ).strip().lower()
            if answer == "b":
                return False
            try:
                draft[field] = enum_type(answer)
            except ValueError:
                output_fn(f"Invalid {field.replace('_', ' ')}. Please try again.")
                continue
            return True
    if field == "structured_value":
        answer = _enrichment_answer(
            "Optional structured value (blank to omit; b/q): ", input_fn=input_fn
        )
        if answer.strip().lower() == "b":
            return False
        draft[field] = answer if answer.strip() else None
        return True
    raise AssertionError(f"unsupported Claim field: {field}")


def _fill_claim_draft(
    draft, *, source_items, input_fn: InputFunction, output_fn: OutputFunction,
    start_index: int = 0,
) -> bool:
    index = start_index
    force_prompt = True
    while True:
        fields = _claim_field_order(draft)
        if index >= len(fields):
            return True
        if fields[index] in draft["_completed_fields"] and not force_prompt:
            index += 1
            continue
        if _edit_claim_field(
            fields[index], draft, source_items=source_items,
            input_fn=input_fn, output_fn=output_fn,
        ):
            draft["_completed_fields"].add(fields[index])
            index += 1
            force_prompt = False
        elif index == 0:
            return False
        else:
            index -= 1
            force_prompt = True


def _print_claim(draft, *, source_items, output_fn: OutputFunction) -> None:
    evidence = (
        "Source-level context"
        if not draft["evidence_numbers"]
        else "; ".join(
            source_items[index - 1].exact_excerpt
            for index in draft["evidence_numbers"]
        )
    )
    output_fn("Claim preview")
    output_fn(f"Type: {draft['claim_type'].value}")
    output_fn(f"Statement: {draft['statement']}")
    output_fn(f"Evidence: {evidence}")
    output_fn(f"Relationship: {draft['relationship'].value}")
    output_fn(f"Temporality: {draft['temporality'].value}")
    output_fn(f"Scope: {draft['scope'].value}")
    if draft["claim_type"] in _ENRICHMENT_STRUCTURED_TYPES:
        output_fn(f"Structured value: {draft['structured_value'] or 'Not provided'}")


def _edit_claim_menu(
    draft, *, source_items, input_fn: InputFunction, output_fn: OutputFunction
) -> None:
    while True:
        fields = _claim_field_order(draft)
        output_fn("Edit fields:")
        for index, field in enumerate(fields, 1):
            output_fn(f"  {index}. {field.replace('_', ' ')}")
        answer = _enrichment_answer(
            "Choose a field number, or [b]ack/[q]uit: ", input_fn=input_fn
        ).strip().lower()
        if answer == "b":
            return
        try:
            index = int(answer)
        except ValueError:
            output_fn("Please enter a listed field number, b, or q.")
            continue
        if index < 1 or index > len(fields):
            output_fn("Field number is out of range.")
            continue
        if _edit_claim_field(
            fields[index - 1], draft, source_items=source_items,
            input_fn=input_fn, output_fn=output_fn,
        ):
            draft["_completed_fields"].add(fields[index - 1])


def _review_claim_draft(
    draft, *, source_items, input_fn: InputFunction, output_fn: OutputFunction,
    existing_decision: ClaimReviewDecision | None = None,
):
    while True:
        _print_claim(draft, source_items=source_items, output_fn=output_fn)
        answer = _enrichment_choice(
            "[c]onfirmed/[r]ejected/[d]eferred/[e]dit/[b]ack/[q]uit: ",
            {"c", "r", "d", "e", "b"}, input_fn=input_fn, output_fn=output_fn,
        )
        if answer == "e":
            _edit_claim_menu(
                draft, source_items=source_items, input_fn=input_fn,
                output_fn=output_fn,
            )
            continue
        if answer == "b":
            return None
        return {
            "c": ClaimReviewDecision.CONFIRMED,
            "r": ClaimReviewDecision.REJECTED,
            "d": ClaimReviewDecision.DEFERRED,
        }[answer]


def _materialize_claim(bank, source, source_items, draft):
    return create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=source.source_record_id,
        evidence_item_ids=[
            source_items[index - 1].evidence_item_id
            for index in draft["evidence_numbers"]
        ],
        claim_type=draft["claim_type"], relationship=draft["relationship"],
        user_supplied_statement=draft["statement"],
        temporality=draft["temporality"], scope=draft["scope"],
        structured_value=draft["structured_value"],
    )


def _create_claim_interactively(
    bank, source, source_items, *, input_fn, output_fn, reviewed_at,
):
    draft = _claim_draft(source_items=source_items)
    if not _fill_claim_draft(
        draft, source_items=source_items, input_fn=input_fn, output_fn=output_fn
    ):
        return None
    while True:
        decision = _review_claim_draft(
            draft, source_items=source_items, input_fn=input_fn, output_fn=output_fn
        )
        if decision is not None:
            claim = _materialize_claim(bank, source, source_items, draft)
            return claim, create_claim_review(
                claim, decision=decision, reviewed_at=reviewed_at
            )
        fields = _claim_field_order(draft)
        if not _fill_claim_draft(
            draft, source_items=source_items, input_fn=input_fn,
            output_fn=output_fn, start_index=len(fields) - 1,
        ):
            return None


def _select_claim(
    source_claims, *, prompt: str, input_fn: InputFunction,
    output_fn: OutputFunction,
):
    while True:
        answer = _enrichment_answer(prompt, input_fn=input_fn).strip().lower()
        if answer == "b":
            return None
        try:
            index = int(answer)
        except ValueError:
            output_fn("Please enter a Claim number, b, or q.")
            continue
        if index < 1 or index > len(source_claims):
            output_fn("Claim number is out of range.")
            continue
        return source_claims[index - 1]


def _source_claims(source_id, claims_by_id):
    return sorted(
        (item for item in claims_by_id.values() if item.source_record_id == source_id),
        key=lambda item: item.claim_id,
    )


def _print_source_claims(
    source, claims_by_id, reviews_by_claim, *, locked_claim_ids, output_fn
):
    source_claims = _source_claims(source.source_record_id, claims_by_id)
    output_fn(f"Claims for {_source_label(source)}:")
    if not source_claims:
        output_fn("  None")
    for index, claim in enumerate(source_claims, 1):
        locked = " (saved revision)" if claim.claim_id in locked_claim_ids else ""
        output_fn(
            f"  {index}. [{reviews_by_claim[claim.claim_id].decision.value}] "
            f"{claim.user_supplied_statement}{locked}"
        )
    return source_claims


def _edit_existing_claim(
    bank, source, source_items, claim, review, *, locked: bool,
    input_fn: InputFunction, output_fn: OutputFunction, reviewed_at: str,
):
    if locked:
        output_fn(f"Statement: {claim.user_supplied_statement}")
        answer = _enrichment_choice(
            "Saved Claim: [c]onfirmed/[r]ejected/[d]eferred/[b]ack/[q]uit: ",
            {"c", "r", "d", "b"}, input_fn=input_fn, output_fn=output_fn,
        )
        if answer == "b":
            return claim, review
        decision = {
            "c": ClaimReviewDecision.CONFIRMED,
            "r": ClaimReviewDecision.REJECTED,
            "d": ClaimReviewDecision.DEFERRED,
        }[answer]
        return claim, create_claim_review(
            claim, decision=decision, reviewed_at=reviewed_at
        )
    draft = _claim_draft(claim, source_items=source_items)
    decision = _review_claim_draft(
        draft, source_items=source_items, input_fn=input_fn,
        output_fn=output_fn, existing_decision=review.decision,
    )
    if decision is None:
        return claim, review
    updated = _materialize_claim(bank, source, source_items, draft)
    return updated, create_claim_review(
        updated, decision=decision, reviewed_at=reviewed_at
    )


def _collect_enrichment_claims(
    bank: EvidenceBank,
    *, input_fn: InputFunction, output_fn: OutputFunction, reviewed_at: str,
    previous: EvidenceEnrichmentArtifact | None,
):
    claims_by_id = {
        item.claim_id: item for item in (() if previous is None else previous.claims)
    }
    reviews_by_claim = {
        item.claim_id: item for item in (() if previous is None else previous.claim_reviews)
    }
    locked_claim_ids = set(claims_by_id)

    items_by_source = {
        source.source_record_id: tuple(
            item for item in bank.items if item.source_record_id == source.source_record_id
        )
        for source in bank.sources
    }
    for source in bank.sources:
        output_fn("")
        output_fn(_source_label(source))
        source_items = items_by_source[source.source_record_id]
        for index, item in enumerate(source_items, 1):
            output_fn(f"  {index}. {item.exact_excerpt}")
        output_fn(
            "You may clarify contribution, challenge, action, responsibility, result, "
            "metric/scale, technology, ownership, completion, timeline, or context."
        )
        while True:
            source_claims = _print_source_claims(
                source, claims_by_id, reviews_by_claim,
                locked_claim_ids=locked_claim_ids, output_fn=output_fn,
            )
            action = _enrichment_choice(
                "[a]dd/[e]dit/[d]elete/[n]ext/[q]uit: ",
                {"a", "e", "d", "n"}, input_fn=input_fn, output_fn=output_fn,
            )
            if action == "n":
                break
            if action == "a":
                result = _create_claim_interactively(
                    bank, source, source_items, input_fn=input_fn,
                    output_fn=output_fn, reviewed_at=reviewed_at,
                )
                if result is None:
                    continue
                claim, review = result
                if claim.claim_id in claims_by_id:
                    output_fn("That Claim already exists; edit the existing Claim instead.")
                    continue
                claims_by_id[claim.claim_id] = claim
                reviews_by_claim[claim.claim_id] = review
                continue
            if not source_claims:
                output_fn("There are no Claims to edit or delete for this Source.")
                continue
            selected = _select_claim(
                source_claims, prompt="Claim number [b/q]: ",
                input_fn=input_fn, output_fn=output_fn,
            )
            if selected is None:
                continue
            if action == "d":
                if selected.claim_id in locked_claim_ids:
                    output_fn("Saved revision Claims are immutable and cannot be deleted.")
                    continue
                if _enrichment_yes_no(
                    "Delete this Claim? [y/n/q]: ", input_fn=input_fn,
                    output_fn=output_fn,
                ):
                    claims_by_id.pop(selected.claim_id)
                    reviews_by_claim.pop(selected.claim_id)
                continue
            updated, review = _edit_existing_claim(
                bank, source, source_items, selected,
                reviews_by_claim[selected.claim_id],
                locked=selected.claim_id in locked_claim_ids,
                input_fn=input_fn, output_fn=output_fn, reviewed_at=reviewed_at,
            )
            if updated.claim_id != selected.claim_id:
                if updated.claim_id in claims_by_id:
                    output_fn("The edited Claim duplicates an existing Claim; no change was made.")
                    continue
                claims_by_id.pop(selected.claim_id)
                reviews_by_claim.pop(selected.claim_id)
            claims_by_id[updated.claim_id] = updated
            reviews_by_claim[updated.claim_id] = review
    return claims_by_id, reviews_by_claim, locked_claim_ids, items_by_source


def _print_all_enrichment_claims(
    bank, claims_by_id, reviews_by_claim, items_by_source, *,
    locked_claim_ids, output_fn
) -> None:
    output_fn("Evidence Enrichment final summary")
    for source in bank.sources:
        source_claims = _source_claims(source.source_record_id, claims_by_id)
        output_fn(_source_label(source))
        if not source_claims:
            output_fn("  None")
            continue
        for index, claim in enumerate(source_claims, 1):
            locked = " (saved revision)" if claim.claim_id in locked_claim_ids else ""
            output_fn(
                f"  {index}. Review: {reviews_by_claim[claim.claim_id].decision.value}{locked}"
            )
            _print_claim(
                _claim_draft(
                    claim, source_items=items_by_source[source.source_record_id]
                ),
                source_items=items_by_source[source.source_record_id],
                output_fn=output_fn,
            )


def _final_edit_enrichment_claim(
    bank, claims_by_id, reviews_by_claim, locked_claim_ids, items_by_source,
    *, input_fn: InputFunction, output_fn: OutputFunction, reviewed_at: str,
) -> None:
    sources = [
        source for source in bank.sources
        if _source_claims(source.source_record_id, claims_by_id)
    ]
    if not sources:
        output_fn("There are no Claims to edit.")
        return
    output_fn("Sources with Claims:")
    for index, source in enumerate(sources, 1):
        output_fn(f"  {index}. {_source_label(source)}")
    while True:
        answer = _enrichment_answer(
            "Source number [b/q]: ", input_fn=input_fn
        ).strip().lower()
        if answer == "b":
            return
        try:
            source_index = int(answer)
        except ValueError:
            output_fn("Please enter a Source number, b, or q.")
            continue
        if source_index < 1 or source_index > len(sources):
            output_fn("Source number is out of range.")
            continue
        break
    source = sources[source_index - 1]
    source_claims = _print_source_claims(
        source, claims_by_id, reviews_by_claim,
        locked_claim_ids=locked_claim_ids, output_fn=output_fn,
    )
    selected = _select_claim(
        source_claims, prompt="Claim number [b/q]: ",
        input_fn=input_fn, output_fn=output_fn,
    )
    if selected is None:
        return
    updated, review = _edit_existing_claim(
        bank, source, items_by_source[source.source_record_id], selected,
        reviews_by_claim[selected.claim_id],
        locked=selected.claim_id in locked_claim_ids,
        input_fn=input_fn, output_fn=output_fn, reviewed_at=reviewed_at,
    )
    if updated.claim_id != selected.claim_id:
        if updated.claim_id in claims_by_id:
            output_fn("The edited Claim duplicates an existing Claim; no change was made.")
            return
        claims_by_id.pop(selected.claim_id)
        reviews_by_claim.pop(selected.claim_id)
    claims_by_id[updated.claim_id] = updated
    reviews_by_claim[updated.claim_id] = review


def _run_enrich_evidence(
    arguments, *, input_fn: InputFunction, output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    protected_inputs = {
        arguments.profile, arguments.mapping, arguments.recommendation,
        arguments.decision, arguments.gap_analysis, arguments.evidence_bank,
        *(() if arguments.review_artifact is None else (arguments.review_artifact,)),
        *(() if arguments.allocation_review_artifact is None else (arguments.allocation_review_artifact,)),
        *(() if arguments.superseded_decision is None else (arguments.superseded_decision,)),
        *(() if arguments.supersede is None else (arguments.supersede,)),
    }
    if arguments.output in protected_inputs:
        raise Phase2ValidationError("Evidence Enrichment output must not replace an input artifact")
    if arguments.output.exists():
        raise FileExistsError(
            f"Evidence Enrichment output already exists: {arguments.output}. Choose a new path."
        )
    profile = load_profile(arguments.profile)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    mapping = load_mapping_candidates(arguments.mapping, profile=profile, rubric=rubric, catalog=catalog)
    evidence_reviews = None if arguments.review_artifact is None else load_evidence_binding_reviews(
        arguments.review_artifact, profile=profile, rubric=rubric, mapping=mapping, catalog=catalog
    )
    allocation_reviews = None if arguments.allocation_review_artifact is None else load_evidence_group_allocation_reviews(
        arguments.allocation_review_artifact, profile=profile, rubric=rubric, mapping=mapping, catalog=catalog
    )
    recommendation = load_role_recommendation(
        arguments.recommendation, profile=profile, rubric=rubric, catalog=catalog,
        mapping_candidates=mapping, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    superseded_decision = None if arguments.superseded_decision is None else load_user_role_decision_revision_source(
        arguments.superseded_decision, catalog=catalog
    )
    decision = load_user_role_decision(
        arguments.decision, catalog=catalog, recommendation_set=recommendation,
        profile=profile, rubric=rubric, superseded_decision=superseded_decision,
    )
    gap = load_career_gap_analysis(
        arguments.gap_analysis, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    bank = load_evidence_bank(
        arguments.evidence_bank, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        gap_analysis=gap, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    context = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    previous = None if arguments.supersede is None else load_evidence_enrichment_revision_source(
        arguments.supersede, **context
    )
    now = now_fn()
    claims_by_id, reviews_by_claim, locked_claim_ids, items_by_source = _collect_enrichment_claims(
        bank, input_fn=input_fn, output_fn=output_fn, reviewed_at=now, previous=previous
    )
    while True:
        _print_all_enrichment_claims(
            bank, claims_by_id, reviews_by_claim, items_by_source,
            locked_claim_ids=locked_claim_ids, output_fn=output_fn,
        )
        final_action = _enrichment_choice(
            "[s]ave/[e]dit/[q]uit: ", {"s", "e"},
            input_fn=input_fn, output_fn=output_fn,
        )
        if final_action == "s":
            break
        _final_edit_enrichment_claim(
            bank, claims_by_id, reviews_by_claim, locked_claim_ids,
            items_by_source, input_fn=input_fn, output_fn=output_fn,
            reviewed_at=now,
        )
    artifact = build_evidence_enrichment(
        **context, claims=list(claims_by_id.values()),
        claim_reviews=list(reviews_by_claim.values()), created_at=now,
        superseded_enrichment=previous,
    )
    summary = artifact.summary
    output_fn("Evidence Enrichment summary")
    output_fn(f"Confirmed: {summary.confirmed_count}")
    output_fn(f"Rejected: {summary.rejected_count}")
    output_fn(f"Deferred: {summary.deferred_count}")
    output_fn(f"Conservative Resume inputs: {summary.resume_selectable_count}")
    save_evidence_enrichment(
        artifact, arguments.output, **context, superseded_enrichment=previous
    )
    output_fn(f"Evidence Enrichment saved to: {arguments.output}")
    return 0


def _print_resume_material(
    artifact: ResumeMaterialArtifact, *, output_fn: OutputFunction,
) -> None:
    output_fn("Resume Material")
    for material in sorted(
        artifact.materials, key=lambda item: (item.section.value, item.material_id)
    ):
        output_fn(
            f"[{material.section.value}] {material.eligibility.value}: "
            f"{material.exact_text}"
        )
    coverage = artifact.coverage
    output_fn("Coverage")
    output_fn(
        f"Education: {coverage.covered_education_count}/"
        f"{coverage.profile_education_count}"
    )
    output_fn(
        f"Experience / projects: {coverage.covered_experience_count}/"
        f"{coverage.profile_experience_count}"
    )
    output_fn(f"Skills: {coverage.covered_skill_count}/{coverage.profile_skill_count}")
    output_fn(
        "Missing contact fields: "
        + (", ".join(coverage.missing_contact_fields) or "None")
    )
    for issue in coverage.issues:
        output_fn(f"- {issue.reason.value}: {issue.profile_path}")
    output_fn(f"Partial coverage: {'yes' if coverage.partial_coverage else 'no'}")


def _run_prepare_resume_materials(
    arguments, *, input_fn: InputFunction, output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    protected_inputs = {
        arguments.profile, arguments.mapping, arguments.recommendation,
        arguments.decision, arguments.gap_analysis, arguments.evidence_bank,
        arguments.evidence_enrichment,
        *(() if arguments.review_artifact is None else (arguments.review_artifact,)),
        *(() if arguments.allocation_review_artifact is None else (arguments.allocation_review_artifact,)),
        *(() if arguments.superseded_decision is None else (arguments.superseded_decision,)),
        *(() if arguments.superseded_enrichment is None else (arguments.superseded_enrichment,)),
        *(() if arguments.supersede is None else (arguments.supersede,)),
    }
    if arguments.output in protected_inputs:
        raise Phase2ValidationError("Resume Material output must not replace an input artifact")
    if arguments.output.exists():
        raise FileExistsError(
            f"Resume Material output already exists: {arguments.output}. Choose a new path."
        )
    profile = load_profile(arguments.profile)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    mapping = load_mapping_candidates(
        arguments.mapping, profile=profile, rubric=rubric, catalog=catalog
    )
    evidence_reviews = None if arguments.review_artifact is None else load_evidence_binding_reviews(
        arguments.review_artifact, profile=profile, rubric=rubric,
        mapping=mapping, catalog=catalog,
    )
    allocation_reviews = None if arguments.allocation_review_artifact is None else load_evidence_group_allocation_reviews(
        arguments.allocation_review_artifact, profile=profile, rubric=rubric,
        mapping=mapping, catalog=catalog,
    )
    recommendation = load_role_recommendation(
        arguments.recommendation, profile=profile, rubric=rubric, catalog=catalog,
        mapping_candidates=mapping, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    superseded_decision = None if arguments.superseded_decision is None else load_user_role_decision_revision_source(
        arguments.superseded_decision, catalog=catalog
    )
    decision = load_user_role_decision(
        arguments.decision, catalog=catalog, recommendation_set=recommendation,
        profile=profile, rubric=rubric, superseded_decision=superseded_decision,
    )
    gap = load_career_gap_analysis(
        arguments.gap_analysis, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    bank = load_evidence_bank(
        arguments.evidence_bank, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        gap_analysis=gap, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    enrichment_context = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    superseded_enrichment = None if arguments.superseded_enrichment is None else load_evidence_enrichment_revision_source(
        arguments.superseded_enrichment, **enrichment_context
    )
    enrichment = load_evidence_enrichment(
        arguments.evidence_enrichment, **enrichment_context,
        superseded_enrichment=superseded_enrichment,
    )
    material_context = dict(
        **enrichment_context, evidence_enrichment=enrichment,
        superseded_enrichment=superseded_enrichment,
    )
    previous = None if arguments.supersede is None else load_resume_material_revision_source(
        arguments.supersede, **material_context
    )
    artifact = build_resume_material(
        **material_context, created_at=now_fn(), superseded_resume_material=previous
    )
    _print_resume_material(artifact, output_fn=output_fn)
    while True:
        answer = input_fn("[s]ave/[q]uit: ").strip().lower()
        if answer == "q":
            raise ResumeMaterialSessionCancelled
        if answer == "s":
            break
        output_fn("Please enter s or q.")
    save_resume_material(
        artifact, arguments.output, **material_context,
        superseded_resume_material=previous,
    )
    output_fn(f"Resume Material saved to: {arguments.output}")
    return 0


def _wording_choice(
    prompt: str, allowed: set[str], *, input_fn: InputFunction,
    output_fn: OutputFunction,
) -> str:
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer == "q":
            raise ResumeWordingSessionCancelled
        if answer in allowed:
            return answer
        output_fn("Please enter one of: " + ", ".join(sorted(allowed | {"q"})) + ".")


def _wording_numbers(value: str, maximum: int) -> tuple[int, ...]:
    tokens = value.split(",")
    if any(not item.strip() for item in tokens):
        raise ValueError("material numbers must not contain empty entries")
    try:
        numbers = tuple(int(item.strip()) for item in tokens)
    except ValueError as error:
        raise ValueError("material numbers must be integers") from error
    if len(numbers) != len(set(numbers)):
        raise ValueError("material numbers must not contain duplicates")
    if any(item < 1 or item > maximum for item in numbers):
        raise ValueError(f"material numbers must be between 1 and {maximum}")
    return numbers


def _print_wording_candidate(
    candidate: WordingCandidate, review: WordingReviewRecord | None,
    material_by_id: dict[str, object], *, output_fn: OutputFunction,
) -> None:
    output_fn(f"Section: {candidate.section_type.value}")
    output_fn(f"Type: {candidate.candidate_type.value}")
    output_fn("Materials:")
    for material_id in candidate.material_ids:
        material = material_by_id[material_id]
        output_fn(f"- [{material.eligibility.value}] {material.exact_text}")
    output_fn(f"Wording: {candidate.wording_text}")
    if review is not None:
        output_fn(f"Review: {review.decision.value}")


def _compose_wording_candidate(
    *, source_record_id: str, materials: list, created_at: str,
    claims_by_id: dict[str, object],
    input_fn: InputFunction, output_fn: OutputFunction,
    initial: WordingCandidate | None = None,
) -> tuple[WordingCandidate, WordingReviewRecord] | None:
    selected_type = None if initial is None else initial.candidate_type
    selected_ids = None if initial is None else initial.material_ids
    wording_text = None if initial is None else initial.wording_text
    origin = WordingOrigin.USER_AUTHORED if initial is None else initial.origin
    step = 0
    while True:
        if step == 0:
            raw = input_fn("Candidate type [u]bullet/[s]tructural_entry/[b]ack/[q]uit: ").strip().lower()
            if raw == "q":
                raise ResumeWordingSessionCancelled
            if raw == "b":
                return None
            if raw not in {"u", "s"}:
                output_fn("Please enter u, s, b, or q.")
                continue
            selected_type = (
                WordingCandidateType.BULLET if raw == "u"
                else WordingCandidateType.STRUCTURAL_ENTRY
            )
            step = 1
        elif step == 1:
            output_fn("Available materials")
            for index, material in enumerate(materials, 1):
                output_fn(f"{index}. [{material.eligibility.value}] {material.exact_text}")
            raw = input_fn("Material numbers (comma-separated) [b]ack/[q]uit: ").strip()
            if raw.lower() == "q":
                raise ResumeWordingSessionCancelled
            if raw.lower() == "b":
                step = 0
                continue
            try:
                numbers = _wording_numbers(raw, len(materials))
            except ValueError as error:
                output_fn(f"Invalid input: {error}")
                continue
            selected = [materials[index - 1] for index in numbers]
            if selected_type == WordingCandidateType.BULLET:
                if any(item.eligibility.value == "structural_only" for item in selected):
                    output_fn("Invalid input: structural material cannot support a bullet.")
                    continue
                if not any(item.eligibility.value == "achievement_component" for item in selected):
                    output_fn("Invalid input: a bullet requires an achievement component.")
                    continue
            elif any(item.eligibility.value != "structural_only" for item in selected):
                output_fn("Invalid input: structural entries may use only structural material.")
                continue
            selected_ids = tuple(sorted(item.material_id for item in selected))
            step = 2
        elif step == 2:
            raw = input_fn(
                "Wording text (.exact for one material) [b]ack/[q]uit: "
            )
            if raw.strip().lower() == "q":
                raise ResumeWordingSessionCancelled
            if raw.strip().lower() == "b":
                step = 1
                continue
            if raw == ".exact":
                if selected_ids is None or len(selected_ids) != 1:
                    output_fn("Invalid input: .exact requires exactly one material.")
                    continue
                wording_text = next(
                    item.exact_text for item in materials
                    if item.material_id == selected_ids[0]
                )
                origin = WordingOrigin.EXACT_MATERIAL
            elif not raw.strip():
                output_fn("Invalid input: wording must not be blank.")
                continue
            else:
                wording_text = raw
                origin = WordingOrigin.USER_AUTHORED
            step = 3
        else:
            assert selected_type is not None and selected_ids is not None and wording_text is not None
            candidate = create_wording_candidate(
                section_type=next(
                    item.section for item in materials if item.material_id == selected_ids[0]
                ),
                candidate_type=selected_type, source_record_id=source_record_id,
                material_ids=selected_ids, wording_text=wording_text,
                origin=origin, created_at=created_at,
            )
            try:
                selected_materials = [
                    item for item in materials if item.material_id in candidate.material_ids
                ]
                _validate_candidate_materials(
                    candidate, selected_materials, claims_by_id
                )
            except Phase2ValidationError as error:
                output_fn(f"Invalid input: {error}")
                step = 2
                continue
            _print_wording_candidate(
                candidate, None, {item.material_id: item for item in materials},
                output_fn=output_fn,
            )
            raw = input_fn(
                "[c]onfirmed/[r]ejected/[d]eferred/[e]dit/[b]ack/[q]uit: "
            ).strip().lower()
            if raw == "q":
                raise ResumeWordingSessionCancelled
            if raw == "b":
                step = 2
                continue
            if raw == "e":
                while True:
                    field = input_fn(
                        "Edit [t]ype/[m]aterials/[w]ording/[b]ack/[q]uit: "
                    ).strip().lower()
                    if field == "q":
                        raise ResumeWordingSessionCancelled
                    if field == "b":
                        break
                    if field in {"t", "m", "w"}:
                        step = {"t": 0, "m": 1, "w": 2}[field]
                        break
                    output_fn("Please enter t, m, w, b, or q.")
                continue
            decisions = {
                "c": WordingReviewDecision.CONFIRMED,
                "r": WordingReviewDecision.REJECTED,
                "d": WordingReviewDecision.DEFERRED,
            }
            if raw not in decisions:
                output_fn("Please enter c, r, d, e, b, or q.")
                continue
            return candidate, create_wording_review(
                candidate, decision=decisions[raw], reviewed_at=created_at
            )


def _source_candidates(
    source_id: str, candidates: dict[str, WordingCandidate],
) -> list[WordingCandidate]:
    return sorted(
        (item for item in candidates.values() if item.source_record_id == source_id),
        key=lambda item: item.wording_candidate_id,
    )


def _print_source_wording(
    source_id: str, candidates: dict[str, WordingCandidate],
    reviews: dict[str, WordingReviewRecord], *, output_fn: OutputFunction,
) -> None:
    items = _source_candidates(source_id, candidates)
    output_fn(f"Current wording candidates ({len(items)})")
    for index, item in enumerate(items, 1):
        output_fn(
            f"{index}. [{reviews[item.wording_candidate_id].decision.value}] "
            f"{item.wording_text}"
        )


def _manage_wording_source(
    source_id: str, materials: list, candidates: dict[str, WordingCandidate],
    reviews: dict[str, WordingReviewRecord], *, created_at: str,
    claims_by_id: dict[str, object],
    input_fn: InputFunction, output_fn: OutputFunction,
) -> None:
    while True:
        _print_source_wording(source_id, candidates, reviews, output_fn=output_fn)
        action = _wording_choice(
            "[a]dd/[e]dit/[d]elete/[n]ext/[q]uit: ", {"a", "e", "d", "n"},
            input_fn=input_fn, output_fn=output_fn,
        )
        if action == "n":
            return
        existing = _source_candidates(source_id, candidates)
        if action in {"e", "d"} and not existing:
            output_fn("No candidates are available for this action.")
            continue
        if action == "a":
            composed = _compose_wording_candidate(
                source_record_id=source_id, materials=materials,
                claims_by_id=claims_by_id,
                created_at=created_at, input_fn=input_fn, output_fn=output_fn,
            )
            if composed is None:
                continue
            candidate, review = composed
            if review.decision == WordingReviewDecision.CONFIRMED:
                confirmed_materials = {
                    material_id
                    for candidate_id, existing_review in reviews.items()
                    if existing_review.decision == WordingReviewDecision.CONFIRMED
                    for material_id in candidates[candidate_id].material_ids
                }
                if confirmed_materials.intersection(candidate.material_ids):
                    output_fn("Invalid input: confirmed wording cannot consume material twice.")
                    continue
            candidates[candidate.wording_candidate_id] = candidate
            reviews[candidate.wording_candidate_id] = review
            continue
        while True:
            raw = input_fn("Candidate number [b]ack/[q]uit: ").strip().lower()
            if raw == "q":
                raise ResumeWordingSessionCancelled
            if raw == "b":
                break
            try:
                index = int(raw)
                if index < 1 or index > len(existing):
                    raise ValueError
            except ValueError:
                output_fn(f"Please enter a number from 1 to {len(existing)}, b, or q.")
                continue
            old = existing[index - 1]
            if action == "d":
                confirm = _wording_choice(
                    "Delete this candidate? [y/n/q]: ", {"y", "n"},
                    input_fn=input_fn, output_fn=output_fn,
                )
                if confirm == "y":
                    candidates.pop(old.wording_candidate_id)
                    reviews.pop(old.wording_candidate_id)
                break
            composed = _compose_wording_candidate(
                source_record_id=source_id, materials=materials,
                claims_by_id=claims_by_id,
                created_at=created_at, input_fn=input_fn, output_fn=output_fn,
                initial=old,
            )
            if composed is None:
                break
            candidate, review = composed
            if review.decision == WordingReviewDecision.CONFIRMED:
                confirmed_materials = {
                    material_id
                    for candidate_id, existing_review in reviews.items()
                    if candidate_id != old.wording_candidate_id
                    and existing_review.decision == WordingReviewDecision.CONFIRMED
                    for material_id in candidates[candidate_id].material_ids
                }
                if confirmed_materials.intersection(candidate.material_ids):
                    output_fn("Invalid input: confirmed wording cannot consume material twice.")
                    continue
            candidates.pop(old.wording_candidate_id)
            reviews.pop(old.wording_candidate_id)
            candidates[candidate.wording_candidate_id] = candidate
            reviews[candidate.wording_candidate_id] = review
            break


def _print_wording_summary(
    candidates: dict[str, WordingCandidate], reviews: dict[str, WordingReviewRecord],
    material_by_id: dict[str, object], *, output_fn: OutputFunction,
) -> None:
    output_fn("Resume Wording final summary")
    for index, candidate in enumerate(sorted(candidates.values(), key=lambda item: item.wording_candidate_id), 1):
        output_fn(f"Candidate {index}")
        _print_wording_candidate(
            candidate, reviews[candidate.wording_candidate_id], material_by_id,
            output_fn=output_fn,
        )


def _load_wording_chain(arguments):
    profile = load_profile(arguments.profile)
    rubric = production_capability_rubric()
    catalog = production_role_catalog()
    mapping = load_mapping_candidates(arguments.mapping, profile=profile, rubric=rubric, catalog=catalog)
    evidence_reviews = None if arguments.review_artifact is None else load_evidence_binding_reviews(
        arguments.review_artifact, profile=profile, rubric=rubric, mapping=mapping, catalog=catalog
    )
    allocation_reviews = None if arguments.allocation_review_artifact is None else load_evidence_group_allocation_reviews(
        arguments.allocation_review_artifact, profile=profile, rubric=rubric, mapping=mapping, catalog=catalog
    )
    recommendation = load_role_recommendation(
        arguments.recommendation, profile=profile, rubric=rubric, catalog=catalog,
        mapping_candidates=mapping, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews,
    )
    superseded_decision = None if arguments.superseded_decision is None else load_user_role_decision_revision_source(
        arguments.superseded_decision, catalog=catalog
    )
    decision = load_user_role_decision(
        arguments.decision, catalog=catalog, recommendation_set=recommendation,
        profile=profile, rubric=rubric, superseded_decision=superseded_decision,
    )
    gap = load_career_gap_analysis(
        arguments.gap_analysis, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        evidence_reviews=evidence_reviews, allocation_reviews=allocation_reviews,
        superseded_decision=superseded_decision,
    )
    bank = load_evidence_bank(
        arguments.evidence_bank, profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        gap_analysis=gap, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    enrichment_context = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_reviews=evidence_reviews,
        allocation_reviews=allocation_reviews, superseded_decision=superseded_decision,
    )
    superseded_enrichment = None if arguments.superseded_enrichment is None else load_evidence_enrichment_revision_source(
        arguments.superseded_enrichment, **enrichment_context
    )
    enrichment = load_evidence_enrichment(
        arguments.evidence_enrichment, **enrichment_context,
        superseded_enrichment=superseded_enrichment,
    )
    material_context = dict(
        **enrichment_context, evidence_enrichment=enrichment,
        superseded_enrichment=superseded_enrichment,
    )
    superseded_material = None if arguments.superseded_resume_material is None else load_resume_material_revision_source(
        arguments.superseded_resume_material, **material_context
    )
    material = load_resume_material(
        arguments.resume_material, **material_context,
        superseded_resume_material=superseded_material,
    )
    return bank, material, dict(
        **material_context, superseded_resume_material=superseded_material
    )


def _run_review_resume_wording(
    arguments, *, input_fn: InputFunction, output_fn: OutputFunction,
    now_fn=lambda: datetime.now(timezone.utc).isoformat(),
) -> int:
    protected = {
        arguments.profile, arguments.mapping, arguments.recommendation,
        arguments.decision, arguments.gap_analysis, arguments.evidence_bank,
        arguments.evidence_enrichment, arguments.resume_material,
        *(() if arguments.review_artifact is None else (arguments.review_artifact,)),
        *(() if arguments.allocation_review_artifact is None else (arguments.allocation_review_artifact,)),
        *(() if arguments.superseded_decision is None else (arguments.superseded_decision,)),
        *(() if arguments.superseded_enrichment is None else (arguments.superseded_enrichment,)),
        *(() if arguments.superseded_resume_material is None else (arguments.superseded_resume_material,)),
        *(() if arguments.supersede is None else (arguments.supersede,)),
    }
    if arguments.output in protected:
        raise Phase2ValidationError("Resume Wording output must not replace an input artifact")
    if arguments.output.exists():
        raise FileExistsError(
            f"Resume Wording output already exists: {arguments.output}. Choose a new path."
        )
    bank, material, context = _load_wording_chain(arguments)
    if material.coverage.partial_coverage:
        output_fn(
            "Resume Material coverage is partial. This wording review is not a complete resume."
        )
    previous = None if arguments.supersede is None else load_resume_wording_review_revision_source(
        arguments.supersede, resume_material=material, **context
    )
    candidates = {
        item.wording_candidate_id: item
        for item in (() if previous is None else previous.candidates)
    }
    reviews = {
        item.wording_candidate_id: item
        for item in (() if previous is None else previous.reviews)
    }
    material_by_id = {item.material_id: item for item in material.materials}
    materials_by_source: dict[str, list] = {}
    for item in material.materials:
        materials_by_source.setdefault(item.source_record_id, []).append(item)
    source_by_id = {item.source_record_id: item for item in bank.sources}
    claims_by_id = {
        item.claim_id: item for item in context["evidence_enrichment"].claims
    }
    now = now_fn()
    source_ids = sorted(materials_by_source)
    for source_id in source_ids:
        source = source_by_id[source_id]
        output_fn(
            f"Source: {source.record_type.value} {dict(source.identity)}"
        )
        _manage_wording_source(
            source_id, sorted(materials_by_source[source_id], key=lambda item: item.material_id),
            candidates, reviews, created_at=now,
            claims_by_id=claims_by_id,
            input_fn=input_fn, output_fn=output_fn,
        )
    while True:
        _print_wording_summary(candidates, reviews, material_by_id, output_fn=output_fn)
        action = _wording_choice(
            "[s]ave/[e]dit/[q]uit: ", {"s", "e"},
            input_fn=input_fn, output_fn=output_fn,
        )
        if action == "s":
            break
        while True:
            for index, source_id in enumerate(source_ids, 1):
                output_fn(f"{index}. {source_by_id[source_id].record_type.value} {dict(source_by_id[source_id].identity)}")
            raw = input_fn("Source number [b]ack/[q]uit: ").strip().lower()
            if raw == "q":
                raise ResumeWordingSessionCancelled
            if raw == "b":
                break
            try:
                index = int(raw)
                if index < 1 or index > len(source_ids):
                    raise ValueError
            except ValueError:
                output_fn(f"Please enter a number from 1 to {len(source_ids)}, b, or q.")
                continue
            source_id = source_ids[index - 1]
            _manage_wording_source(
                source_id, sorted(materials_by_source[source_id], key=lambda item: item.material_id),
                candidates, reviews, created_at=now,
                claims_by_id=claims_by_id,
                input_fn=input_fn, output_fn=output_fn,
            )
            break
    artifact = build_resume_wording_review(
        resume_material=material, candidates=list(candidates.values()),
        reviews=list(reviews.values()), created_at=now,
        superseded_wording_review=previous, **context,
    )
    save_resume_wording_review(
        artifact, arguments.output, resume_material=material,
        superseded_wording_review=previous, **context,
    )
    output_fn(f"Resume Wording Review saved to: {arguments.output}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
    extractor: NarrativeExtractor | None = None,
    mapper: ProfileDimensionMapper | None = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    if arguments.command == "recommend":
        try:
            return _run_recommend(arguments, mapper=mapper, output_fn=output_fn)
        except (
            OSError,
            ProfileValidationError,
            Phase2ValidationError,
            LLMConfigurationError,
            LLMRequestError,
        ) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "review-evidence":
        try:
            return _run_review_evidence(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (KeyboardInterrupt, EOFError):
            output_fn("Evidence review cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "decide":
        try:
            return _run_decide(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (DecisionSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Decision session cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "analyze-gaps":
        try:
            return _run_analyze_gaps(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (GapAnalysisSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Gap Analysis cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "build-evidence-bank":
        try:
            return _run_build_evidence_bank(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (EvidenceBankSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Evidence Bank cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "enrich-evidence":
        try:
            return _run_enrich_evidence(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (EvidenceEnrichmentSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Evidence Enrichment cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "prepare-resume-materials":
        try:
            return _run_prepare_resume_materials(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (ResumeMaterialSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Resume Material preparation cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    if arguments.command == "review-resume-wording":
        try:
            return _run_review_resume_wording(
                arguments, input_fn=input_fn, output_fn=output_fn
            )
        except (ResumeWordingSessionCancelled, KeyboardInterrupt, EOFError):
            output_fn("Resume Wording review cancelled. No files were changed.")
            return 130
        except (OSError, ProfileValidationError, Phase2ValidationError) as error:
            output_fn(f"Error: {error}")
            return 1

    narrative_mode = arguments.narrative or arguments.narrative_file is not None
    if arguments.debug_extraction and not (narrative_mode or arguments.follow_up):
        parser.error("--debug-extraction requires --narrative, --narrative-file, or --follow-up")
    if arguments.debug_full_profile and not (arguments.follow_up and arguments.debug_extraction):
        parser.error("--debug-full-profile requires --follow-up and --debug-extraction")
    try:
        if arguments.follow_up:
            if not arguments.profile.exists():
                raise FileNotFoundError(f"Follow-up Profile does not exist: {arguments.profile}")
            run_follow_up_discovery(
                arguments.profile,
                extractor,
                input_fn=input_fn,
                output_fn=output_fn,
                debug_extraction=arguments.debug_extraction,
                debug_full_profile=arguments.debug_full_profile,
            )
        elif narrative_mode:
            narrative = read_narrative_file(arguments.narrative_file) if arguments.narrative_file else None
            narrative_extractor = extractor or OpenAINarrativeExtractor()
            run_narrative_discovery(
                arguments.profile,
                narrative_extractor,
                input_fn=input_fn,
                output_fn=output_fn,
                debug_extraction=arguments.debug_extraction,
                narrative=narrative,
            )
        else:
            run_discovery(arguments.profile, input_fn=input_fn, output_fn=output_fn)
    except ExtractionDebugError as error:
        output_fn("Warning: debug output may contain personal information from your input.")
        output_fn(f"Aarvia version: {__version__}")
        output_fn(f"Provider model: {error.model}")
        output_fn(f"Provider base URL host: {error.base_url_host}")
        output_fn(f"Extraction protocol: {error.protocol}")
        output_fn(f"Structured output mode: {error.structured_output_mode}")
        output_fn(f"Extraction stage: {error.stage}")
        output_fn(f"JSON decode succeeded: {'yes' if error.json_decode_succeeded else 'no'}")
        output_fn(f"Failure reason: {error.detail}")
        output_fn("Raw response.output_text:")
        output_fn(error.raw_output if error.raw_output is not None else "<unavailable>")
        output_fn(f"Error: {error}")
        return 1
    except KeyboardInterrupt:
        if arguments.follow_up or narrative_mode:
            output_fn("Session cancelled. No files were changed.")
        else:
            output_fn("Session cancelled. Previously saved progress was kept.")
        return 130
    except (
        OSError,
        ProfileValidationError,
        json.JSONDecodeError,
        LLMConfigurationError,
        LLMRequestError,
        NarrativeFileError,
    ) as error:
        output_fn(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
