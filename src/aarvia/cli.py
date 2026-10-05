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
    save_career_gap_analysis,
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
