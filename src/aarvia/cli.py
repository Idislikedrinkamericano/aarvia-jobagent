"""Command-line entry point for Aarvia."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .confirmation import run_narrative_discovery
from .interview import InputFunction, OutputFunction, run_discovery
from .llm_client import LLMConfigurationError, LLMRequestError
from .narrative_extraction import NarrativeExtractor, OpenAINarrativeExtractor
from .profile import ProfileValidationError

DEFAULT_PROFILE_PATH = Path("data/profiles/default.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aarvia", description="Aarvia career navigation tools")
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
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
    extractor: NarrativeExtractor | None = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.narrative:
            narrative_extractor = extractor or OpenAINarrativeExtractor()
            run_narrative_discovery(
                arguments.profile,
                narrative_extractor,
                input_fn=input_fn,
                output_fn=output_fn,
            )
        else:
            run_discovery(arguments.profile, input_fn=input_fn, output_fn=output_fn)
    except (
        OSError,
        ProfileValidationError,
        json.JSONDecodeError,
        LLMConfigurationError,
        LLMRequestError,
    ) as error:
        output_fn(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
