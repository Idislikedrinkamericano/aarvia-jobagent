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
from .capability_rubric import production_capability_rubric
from .evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
    load_evidence_binding_reviews,
    save_evidence_binding_reviews,
)
from .phase2_storage import save_phase2_json_transaction
from .profile_dimension_mapping import (
    AllocatedProfileCriterionEvidenceBinding,
    EvidenceTrustLevel,
    OpenAIProfileDimensionMapper,
    ProfileDimensionMapper,
    ProfileCriterionEvidenceBinding,
    load_mapping_candidates,
    mapping_validation_report_from_warnings,
)
from .role_recommendation import build_role_recommendation, save_role_recommendation
from .storage import load_profile

DEFAULT_PROFILE_PATH = Path("data/profiles/default.json")
MAX_NARRATIVE_FILE_BYTES = 2 * 1024 * 1024


class NarrativeFileError(ValueError):
    """Raised when a narrative text file cannot be used safely."""


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
        help="existing Mapping schema 3/4 JSON; reuses it without calling the Provider",
    )
    recommend.add_argument(
        "--mapping-output",
        type=Path,
        help="new Mapping schema 4 output path (default: PROFILE with .mapping.json suffix)",
    )
    recommend.add_argument(
        "--review-artifact",
        type=Path,
        help="Evidence Binding Review JSON for the supplied --mapping-artifact",
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
        help="review provisional semantic bindings from a Mapping schema 3/4 artifact",
    )
    review.add_argument("--profile", type=Path, required=True, help="existing Career Profile JSON path")
    review.add_argument("--mapping", type=Path, required=True, help="Mapping schema 3/4 JSON path")
    review.add_argument("--output", type=Path, required=True, help="Evidence Binding Review JSON path")
    review.add_argument(
        "--overwrite", action="store_true", help="replace an existing Review output atomically"
    )
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
        output_fn(f"{rank}: {result.role_id.replace('_', ' ').title()}{marker}")
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


def _provisional_semantic_bindings(candidates) -> tuple[ProfileCriterionEvidenceBinding, ...]:
    return tuple(
        sorted(
            (
                item for item in candidates.mappings
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
            arguments.review_artifact,
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
    if arguments.mapping_output is not None and (
        arguments.mapping_candidates is not None or arguments.mapping_artifact is not None
    ):
        raise Phase2ValidationError(
            "--mapping-output is only valid when creating a new Provider mapping"
        )

    review_artifact = None
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
        if candidates.schema_version not in {3, 4}:
            raise Phase2ValidationError("--mapping-artifact requires Mapping schema 3 or 4")
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
            mapping_schema_version=4,
        )
        candidates = active_mapper.map(profile, rubric, catalog)
        if candidates.schema_version != 4:
            raise Phase2ValidationError(
                "the default recommend flow requires Mapping schema 4"
            )

    baseline = None
    if review_artifact is not None:
        baseline = build_role_recommendation(
            profile=profile,
            mapping_candidates=candidates,
            rubric=rubric,
            catalog=catalog,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    created_at = datetime.now(timezone.utc).isoformat()
    artifact = build_role_recommendation(
        profile=profile,
        mapping_candidates=candidates,
        rubric=rubric,
        catalog=catalog,
        created_at=created_at,
        evidence_reviews=review_artifact,
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
            mapping_candidates=candidates if candidates.schema_version in {3, 4} else None,
            evidence_reviews=review_artifact,
        )

    _print_recommendation(
        artifact, candidates, output_path=output_path, output_fn=output_fn
    )
    provisional_count = len(_provisional_semantic_bindings(candidates))
    if mapping_output_path is not None:
        output_fn(f"Mapping saved to: {mapping_output_path}")
        output_fn(f"Provisional semantic bindings requiring review: {provisional_count}")
        if provisional_count:
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
    _ensure_output_available(
        arguments.output, overwrite=arguments.overwrite, label="Evidence Review"
    )
    if arguments.output in {arguments.profile, arguments.mapping}:
        raise Phase2ValidationError(
            "Profile, Mapping, and Evidence Review paths must be different"
        )
    profile = load_profile(arguments.profile)
    catalog = production_role_catalog()
    rubric = production_capability_rubric()
    candidates = load_mapping_candidates(
        arguments.mapping, profile=profile, rubric=rubric, catalog=catalog
    )
    if candidates.schema_version not in {3, 4}:
        raise Phase2ValidationError("review-evidence requires Mapping schema 3 or 4")
    bindings = _provisional_semantic_bindings(candidates)
    if not bindings:
        output_fn("No provisional semantic bindings require review. No file was written.")
        return 0

    output_fn(f"Provisional semantic bindings to review: {len(bindings)}")
    output_fn("Choose confirmed, rejected, deferred, or q to cancel the whole review.")
    decisions = {}
    reviewed_at = datetime.now(timezone.utc).isoformat()
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
    save_evidence_binding_reviews(
        artifact,
        arguments.output,
        profile=profile,
        rubric=rubric,
        mapping=candidates,
        catalog=catalog,
    )
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
