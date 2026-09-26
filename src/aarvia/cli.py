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
from .profile_dimension_mapping import (
    OpenAIProfileDimensionMapper,
    ProfileDimensionMapper,
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
    recommend.add_argument(
        "--mapping-candidates",
        type=Path,
        help="validated offline Profile-to-Dimension mapping candidate JSON",
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
    return parser


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
            profile = load_profile(arguments.profile)
            catalog = production_role_catalog()
            rubric = production_capability_rubric()
            output_path = arguments.output or arguments.profile.with_suffix(".recommendation.json")
            if output_path.exists() and not arguments.overwrite:
                raise FileExistsError(
                    f"Recommendation output already exists: {output_path}. Use --overwrite to replace it."
                )
            if arguments.mapping_candidates is not None:
                candidates = load_mapping_candidates(
                    arguments.mapping_candidates, profile=profile, rubric=rubric, catalog=catalog
                )
            else:
                active_mapper = mapper or OpenAIProfileDimensionMapper(
                    diagnostics_dir=arguments.provider_diagnostics_dir
                )
                candidates = active_mapper.map(profile, rubric, catalog)
            artifact = build_role_recommendation(
                profile=profile,
                mapping_candidates=candidates,
                rubric=rubric,
                catalog=catalog,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            save_role_recommendation(
                artifact, output_path, profile=profile, rubric=rubric, catalog=catalog
            )
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
            return 0
        except (
            OSError,
            ProfileValidationError,
            Phase2ValidationError,
            LLMConfigurationError,
            LLMRequestError,
        ) as error:
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
