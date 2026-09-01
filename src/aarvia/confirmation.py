"""Candidate review, conflict resolution, and confirmed Profile merging."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .candidates import (
    CandidateProfile,
    ConfirmationStatus,
    DiscoveryAnswerState,
    DiscoveryState,
)
from .candidate_normalization import normalize_candidate_data
from .discovery import create_profile, generate_open_questions
from .interview import InputFunction, OutputFunction
from .llm_client import LLMRequestError
from .narrative_extraction import ExtractionDebugError, NarrativeExtractor
from .profile import CareerProfile, ProfileValidationError
from .presentation import (
    format_ambiguous_record_matches,
    format_conflict,
    format_topic,
    format_unmatched_record,
    format_user_friendly_diff,
)
from .record_matching import (
    find_refinement_matches,
    identity_field_values_equivalent,
)
from .storage import load_profile, save_profile

TOPIC_TITLES = {
    "basic_profile": "Basic information",
    "education": "Education",
    "experience_overview": "Experience",
    "skills": "Skills",
    "career_preferences": "Career preferences",
    "constraints": "Constraints",
}

UNRESOLVED = object()
CANCELLED = object()
RETRY_REFINEMENT = object()
CANCEL_REFINEMENT = object()


def _read_nonempty_choice(input_fn: InputFunction, prompt: str) -> str:
    """Ignore blank lines left in the terminal input queue before a menu choice."""
    while True:
        choice = input_fn(prompt).strip().lower()
        if choice:
            return choice


def _deduplicate(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _record_key(topic: str, record: dict[str, Any]) -> tuple[Any, ...]:
    if topic == "education":
        return (
            record["institution"].casefold(),
            record["degree"].casefold(),
            record["field_of_study"].casefold(),
        )
    if topic == "experience_overview":
        return (
            record["organization_or_project_name"].casefold(),
            record["title_or_role"].casefold(),
            record["experience_type"].casefold(),
        )
    if topic == "skills":
        return (record["skill_name"].casefold(),)
    raise ValueError(f"unsupported record topic: {topic}")


def _resolve_conflict(
    topic: str,
    field: str,
    existing: Any,
    candidate: Any,
    *,
    record: dict[str, Any] | None = None,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> Any:
    for line in format_conflict(topic, record, field, existing, candidate):
        output_fn(line)
    while True:
        choice = _read_nonempty_choice(
            input_fn, "[k] Keep current  [u] Use new  [q] Cancel session: "
        )
        if choice in {"k", "e"}:
            return existing
        if choice in {"u", "c"}:
            return candidate
        if choice == "q":
            return UNRESOLVED
        output_fn("Invalid input: enter k, u, or q.")


def merge_candidate_topic(
    profile: CareerProfile,
    topic: str,
    candidate_value: Any,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> CareerProfile | None:
    """Merge one accepted topic, returning None if a conflict remains unresolved."""
    data = profile.to_dict()
    data.pop("open_questions")
    existing = data[topic]

    if topic in {"basic_profile", "constraints"}:
        for field_name, value in candidate_value.items():
            if value in (None, [], ""):
                continue
            current = existing[field_name]
            if current in (None, [], ""):
                existing[field_name] = value
            elif current == value:
                continue
            elif isinstance(current, list) and isinstance(value, list):
                existing[field_name] = _deduplicate(current + value)
            else:
                resolved = _resolve_conflict(
                    topic,
                    field_name,
                    current,
                    value,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if resolved is UNRESOLVED:
                    return None
                existing[field_name] = resolved
    elif topic == "career_preferences":
        for field_name, values in candidate_value.items():
            existing[field_name] = _deduplicate(existing[field_name] + values)
    elif topic in {"education", "experience_overview", "skills"}:
        for candidate_record in candidate_value:
            if candidate_record in existing:
                continue
            key = _record_key(topic, candidate_record)
            matching_index = next(
                (index for index, record in enumerate(existing) if _record_key(topic, record) == key),
                None,
            )
            if matching_index is None:
                existing.append(candidate_record)
                continue
            existing_record = existing[matching_index]
            for field_name, value in candidate_record.items():
                if value in (None, [], ""):
                    continue
                current = existing_record[field_name]
                if current in (None, [], ""):
                    existing_record[field_name] = value
                elif current == value:
                    continue
                elif isinstance(current, list) and isinstance(value, list):
                    existing_record[field_name] = _deduplicate(current + value)
                else:
                    resolved = _resolve_conflict(
                        topic,
                        field_name,
                        current,
                        value,
                        record=existing_record,
                        input_fn=input_fn,
                        output_fn=output_fn,
                    )
                    if resolved is UNRESOLVED:
                        return None
                    existing_record[field_name] = resolved
    else:
        raise ProfileValidationError(f"unknown candidate topic: {topic}")

    return create_profile(data)


def merge_refinement_topic(
    profile: CareerProfile,
    topic: str,
    candidate_value: list[dict[str, Any]],
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> CareerProfile | object:
    """Merge detail enrichment without silently creating unmatched records."""
    if topic not in {"education", "experience_overview", "skills"}:
        raise ProfileValidationError(f"unsupported refinement topic: {topic}")
    data = profile.to_dict()
    data.pop("open_questions")
    existing = data[topic]
    plan: list[tuple[dict[str, Any], int | None]] = []

    for candidate_record in candidate_value:
        matches = find_refinement_matches(topic, existing, candidate_record)
        if len(matches) == 1:
            plan.append((candidate_record, matches[0]))
            continue
        if not matches:
            for line in format_unmatched_record(topic, candidate_record):
                output_fn(line)
            while True:
                choice = _read_nonempty_choice(
                    input_fn,
                    "[r] Re-describe  [a] Add as new  [q] Cancel session: "
                )
                if choice == "r":
                    return RETRY_REFINEMENT
                if choice == "a":
                    plan.append((candidate_record, None))
                    break
                if choice == "q":
                    return CANCEL_REFINEMENT
                output_fn("Invalid input: enter r, a, or q.")
            continue

        for line in format_ambiguous_record_matches(
            topic, candidate_record, [existing[index] for index in matches]
        ):
            output_fn(line)
        while True:
            choice = _read_nonempty_choice(
                input_fn,
                f"Choose 1-{len(matches)}, [r] re-describe, or [q] cancel session: "
            )
            if choice == "r":
                return RETRY_REFINEMENT
            if choice == "q":
                return CANCEL_REFINEMENT
            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                plan.append((candidate_record, matches[int(choice) - 1]))
                break
            output_fn(f"Invalid input: choose 1-{len(matches)}, r, or q.")

    for candidate_record, matching_index in plan:
        if matching_index is None:
            existing.append(candidate_record)
            continue
        existing_record = existing[matching_index]
        for field_name, value in candidate_record.items():
            if value in (None, [], ""):
                continue
            current = existing_record[field_name]
            if current in (None, [], ""):
                existing_record[field_name] = value
            elif current == value or identity_field_values_equivalent(
                topic, field_name, current, value
            ):
                continue
            elif isinstance(current, list) and isinstance(value, list):
                existing_record[field_name] = _deduplicate(current + value)
            else:
                resolved = _resolve_conflict(
                    topic,
                    field_name,
                    current,
                    value,
                    record=existing_record,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if resolved is UNRESOLVED:
                    return CANCEL_REFINEMENT
                existing_record[field_name] = resolved

    return create_profile(data)


def missing_topic_count(profile: CareerProfile) -> int:
    questions = generate_open_questions(profile)
    groups = set()
    for question in questions:
        if question.startswith("What is your current"):
            groups.add("basic_profile")
        elif "education background" in question:
            groups.add("education")
        elif question.startswith("What work, internship"):
            groups.add("experience_overview")
        elif question.startswith("What did you do or accomplish as "):
            groups.add("experience_overview")
        elif question.startswith("What skills"):
            groups.add("skills")
        elif question in {
            "Which career fields interest you?",
            "What kinds of work activities do you prefer?",
            "Which industries interest you?",
            "Are there any roles you are currently considering?",
        }:
            groups.add("career_preferences")
        else:
            groups.add("constraints")
    return len(groups)


class NarrativeConfirmationWorkflow:
    def __init__(
        self,
        profile_path: str | Path,
        extractor: NarrativeExtractor,
        *,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
        debug_extraction: bool = False,
        narrative: str | None = None,
    ) -> None:
        self.profile_path = Path(profile_path)
        self.extractor = extractor
        self.input = input_fn
        self.output = output_fn
        self.debug_extraction = debug_extraction
        self.narrative = narrative
        self.profile = load_profile(self.profile_path) if self.profile_path.exists() else create_profile()
        self.session_profile = self.profile
        self.state = DiscoveryState()

    def run(self) -> bool:
        if self.narrative is None:
            self.output("Tell me about your education, experience, projects, and skills.")
            self.output("You can answer in your own words.")
            narrative = self.input("> ").strip()
        else:
            narrative = self.narrative.strip()
        if not narrative or narrative in {":quit", "q"}:
            self.output("No confirmed information was saved.")
            return False

        candidate = self._extract_candidate(narrative)
        if not candidate.topics:
            self.output("No explicit profile information could be extracted.")
            return False

        for topic in candidate.topics:
            while True:
                self._show_topic(topic, candidate.data[topic])
                choice = self._read_choice("Accept this topic? [y/n/e/q] ")
                if choice == "y":
                    merged = merge_candidate_topic(
                        self.session_profile,
                        topic,
                        candidate.data[topic],
                        input_fn=self.input,
                        output_fn=self.output,
                    )
                    if merged is None:
                        self.output("Conflict unresolved. The session draft was not changed.")
                        continue
                    self.session_profile = merged
                    candidate.mark(topic, ConfirmationStatus.ACCEPTED)
                    self.state.mark(topic, DiscoveryAnswerState.ANSWERED)
                    break
                if choice == "n":
                    candidate.mark(topic, ConfirmationStatus.REJECTED)
                    self.state.mark(topic, DiscoveryAnswerState.DECLINED)
                    break
                if choice == "e":
                    correction = self._read_correction()
                    if correction is CANCELLED:
                        continue
                    try:
                        corrected = self._extract_correction(
                            correction,
                            topic=topic,
                            current_topic=candidate.data[topic],
                            original_narrative=narrative,
                        )
                        corrected_value = corrected.data.get(topic)
                        changes = _candidate_diff(candidate.data[topic], corrected_value, topic)
                        _validate_supported_changes(changes, narrative, correction)
                    except (LLMRequestError, ProfileValidationError) as error:
                        self.output(f"Correction failed: {error}")
                        self.output("The current Candidate is unchanged. Try again or choose y, n, or q.")
                        continue
                    if not changes:
                        self.output("The correction produced no changes. The current Candidate is unchanged.")
                        continue
                    self._show_diff(
                        changes,
                        topic=topic,
                        before=candidate.data[topic],
                        after=corrected_value,
                    )
                    apply_choice = self._read_choice(
                        "Apply this correction to the session Candidate? [y/N] "
                    )
                    if apply_choice not in {"y", "yes"}:
                        self.output("Correction discarded. The current Candidate is unchanged.")
                        continue
                    candidate.replace_topic(topic, corrected_value)
                    continue
                if choice == "q":
                    self.output("Session cancelled. The original Profile was not changed.")
                    return False
                self.output("Invalid input: enter y, n, e, or q.")

        self._show_result(candidate, saved=False)
        if not any(
            status == ConfirmationStatus.ACCEPTED
            for status in candidate.topic_statuses.values()
        ):
            self.output("No confirmed information to save.")
            return True
        while True:
            choice = self._read_choice("Save all confirmed topics to the Profile? [y/n] ")
            if choice in {"y", "yes"}:
                save_profile(self.session_profile, self.profile_path)
                self.profile = self.session_profile
                self.output(f"Saved to: {self.profile_path}")
                return True
            if choice in {"n", "no", "q", ":quit"}:
                self.output("Session cancelled. The original Profile was not changed.")
                return False
            self.output("Invalid input: enter y or n.")

    def _read_correction(self) -> str | object:
        self.output("Enter the correction in one or more lines.")
        self.output("Type .done on a line by itself to finish, or .cancel to return without changes.")
        lines: list[str] = []
        while True:
            line = self.input("> ")
            command = line.strip().lower()
            if command == ".done":
                correction = "\n".join(lines).strip()
                if correction:
                    return correction
                self.output("Correction cannot be empty. Enter text, then finish with .done.")
                continue
            if command in {".cancel", ":quit"}:
                self.output("Correction cancelled. The current Candidate is unchanged.")
                return CANCELLED
            lines.append(line.rstrip())

    def _read_choice(self, prompt: str) -> str:
        return _read_nonempty_choice(self.input, prompt)

    def _extract_correction(
        self,
        correction: str,
        *,
        topic: str,
        current_topic: Any,
        original_narrative: str,
    ) -> CandidateProfile:
        self.output("Extracting information...")
        extract_correction = getattr(self.extractor, "extract_correction", None)
        if extract_correction is None:
            extracted = self.extractor.extract(correction, topic=topic)
        else:
            extracted = extract_correction(
                correction,
                topic=topic,
                current_topic=current_topic,
                original_narrative=original_narrative,
            )
        normalized = normalize_candidate_data(extracted)
        candidate = CandidateProfile.from_extracted(normalized)
        if topic not in candidate.data:
            raise ProfileValidationError(f"correction did not contain {topic}")
        return candidate

    def _show_diff(
        self,
        changes: list[tuple[str, Any, Any]],
        *,
        topic: str,
        before: Any,
        after: Any,
    ) -> None:
        for line in format_user_friendly_diff(
            changes,
            topic=topic,
            before=before,
            after=after,
            debug_paths=self.debug_extraction,
        ):
            self.output(line)

    def _extract_candidate(self, narrative: str, *, topic: str | None = None) -> CandidateProfile:
        stage = "raw parsing"
        diagnostics = None

        def set_stage(value: str) -> None:
            nonlocal stage, diagnostics
            stage = value
            if diagnostics is not None:
                diagnostics.stage = value

        try:
            self.output("Extracting information...")
            extracted = self.extractor.extract(narrative, topic=topic)
            diagnostics = getattr(self.extractor, "diagnostics", None)
            normalized = normalize_candidate_data(extracted, stage_callback=set_stage)
            stage = "candidate validation"
            if diagnostics is not None:
                diagnostics.stage = stage
            return CandidateProfile.from_extracted(normalized)
        except (LLMRequestError, ProfileValidationError) as error:
            if not self.debug_extraction:
                raise
            diagnostics = getattr(self.extractor, "diagnostics", None)
            diagnostic_stage = getattr(diagnostics, "stage", None) or stage
            if stage != "raw parsing" and diagnostic_stage == "raw parsing":
                diagnostic_stage = stage
            raw_output = getattr(diagnostics, "raw_output", None)
            decoded = bool(getattr(diagnostics, "json_decode_succeeded", False))
            detail = getattr(diagnostics, "detail", None) or str(error)
            settings = getattr(self.extractor, "settings", None)
            model = getattr(settings, "model", "unavailable")
            api_key = getattr(settings, "api_key", None)
            host = getattr(self.extractor, "base_url_host", "unavailable")
            protocol = getattr(diagnostics, "protocol", "unavailable")
            output_mode = getattr(diagnostics, "structured_output_mode", "unavailable")
            raise ExtractionDebugError(
                str(error),
                stage=diagnostic_stage,
                raw_output=raw_output,
                json_decode_succeeded=decoded,
                detail=detail,
                model=model,
                base_url_host=host,
                protocol=protocol,
                structured_output_mode=output_mode,
                api_key=api_key,
            ) from error

    def _show_topic(self, topic: str, value: Any) -> None:
        self.output(f"\n{TOPIC_TITLES[topic]}")
        for line in format_topic(topic, value):
            self.output(line)

    def _show_result(self, candidate: CandidateProfile, *, saved: bool) -> None:
        accepted = [
            topic for topic, status in candidate.topic_statuses.items()
            if status == ConfirmationStatus.ACCEPTED
        ]
        self.output("Session draft summary:")
        self.output("Confirmed for this session:")
        if not accepted:
            self.output("- No candidate topics")
        for topic in accepted:
            value = candidate.data[topic]
            count = len(value) if isinstance(value, list) else 1
            self.output(f"- {count} {TOPIC_TITLES[topic].lower()} item(s)")
        self.output(f"{missing_topic_count(self.session_profile)} discovery topics still need information.")


def _candidate_diff(before: Any, after: Any, path: str) -> list[tuple[str, Any, Any]]:
    if type(before) is not type(after):
        return [(path, before, after)]
    if isinstance(before, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(set(before) | set(after)):
            changes.extend(_candidate_diff(before.get(key), after.get(key), f"{path}.{key}"))
        return changes
    if isinstance(before, list):
        if before == after:
            return []
        changes = []
        for index in range(max(len(before), len(after))):
            old = before[index] if index < len(before) else None
            new = after[index] if index < len(after) else None
            changes.extend(_candidate_diff(old, new, f"{path}[{index}]"))
        return changes
    return [] if before == after else [(path, before, after)]


def _validate_supported_changes(
    changes: list[tuple[str, Any, Any]],
    original_narrative: str,
    correction: str,
    *,
    semantic_support: Callable[[str, Any, str], bool] | None = None,
) -> None:
    evidence = f"{original_narrative}\n{correction}".casefold()
    requested = correction.casefold()
    unsupported: list[str] = []
    for path, before, after in changes:
        if semantic_support is not None and semantic_support(path, after, correction):
            continue
        values = _scalar_values(after)
        if values and all(value in evidence and value in requested for value in values):
            continue
        if not values and before is None:
            continue
        unsupported.append(path)
    if unsupported:
        raise ProfileValidationError(
            "correction introduced values not supported by the original narrative or correction at: "
            + ", ".join(unsupported)
        )


def _scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _scalar_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _scalar_values(child)]
    if value is None:
        return []
    return [str(value).strip().casefold()]


def run_narrative_discovery(
    profile_path: str | Path,
    extractor: NarrativeExtractor,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
    debug_extraction: bool = False,
    narrative: str | None = None,
) -> bool:
    return NarrativeConfirmationWorkflow(
        profile_path,
        extractor,
        input_fn=input_fn,
        output_fn=output_fn,
        debug_extraction=debug_extraction,
        narrative=narrative,
    ).run()
