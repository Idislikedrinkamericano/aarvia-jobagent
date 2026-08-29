"""Candidate review, conflict resolution, and confirmed Profile merging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .candidates import (
    CandidateProfile,
    ConfirmationStatus,
    DiscoveryAnswerState,
    DiscoveryState,
)
from .candidate_normalization import normalize_candidate_data
from .discovery import create_profile, generate_open_questions
from .interview import InputFunction, OutputFunction
from .narrative_extraction import NarrativeExtractor
from .profile import CareerProfile, ProfileValidationError
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


def _deduplicate(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _record_key(topic: str, record: dict[str, Any]) -> tuple[Any, ...]:
    if topic == "education":
        return (record["institution"].casefold(), record["degree"].casefold())
    if topic == "experience_overview":
        return (
            record["organization_or_project_name"].casefold(),
            record["title_or_role"].casefold(),
            record["start_date"],
        )
    if topic == "skills":
        return (record["skill_name"].casefold(),)
    raise ValueError(f"unsupported record topic: {topic}")


def _resolve_conflict(
    label: str,
    existing: Any,
    candidate: Any,
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> Any:
    output_fn(f"Conflict in {label}:")
    output_fn(f"Existing value: {json.dumps(existing, ensure_ascii=False)}")
    output_fn(f"Candidate value: {json.dumps(candidate, ensure_ascii=False)}")
    while True:
        choice = input_fn("Keep existing or use candidate? [e/c/q] ").strip().lower()
        if choice == "e":
            return existing
        if choice == "c":
            return candidate
        if choice == "q":
            return UNRESOLVED
        output_fn("Invalid input: enter e, c, or q.")


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
                    f"{topic}.{field_name}", current, value, input_fn=input_fn, output_fn=output_fn
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
            resolved = _resolve_conflict(
                f"{topic} record",
                existing[matching_index],
                candidate_record,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            if resolved is UNRESOLVED:
                return None
            existing[matching_index] = resolved
    else:
        raise ProfileValidationError(f"unknown candidate topic: {topic}")

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
    ) -> None:
        self.profile_path = Path(profile_path)
        self.extractor = extractor
        self.input = input_fn
        self.output = output_fn
        self.profile = load_profile(self.profile_path) if self.profile_path.exists() else create_profile()
        self.state = DiscoveryState()

    def run(self) -> bool:
        self.output("Tell me about your education, experience, projects, and skills.")
        self.output("You can answer in your own words.")
        narrative = self.input("> ").strip()
        if not narrative or narrative in {":quit", "q"}:
            self.output("No confirmed information was saved.")
            return False

        candidate = CandidateProfile.from_extracted(
            normalize_candidate_data(self.extractor.extract(narrative))
        )
        if not candidate.topics:
            self.output("No explicit profile information could be extracted.")
            return False

        for topic in candidate.topics:
            while True:
                self._show_topic(topic, candidate.data[topic])
                choice = self.input("Accept this topic? [y/n/e/q] ").strip().lower()
                if choice == "y":
                    merged = merge_candidate_topic(
                        self.profile,
                        topic,
                        candidate.data[topic],
                        input_fn=self.input,
                        output_fn=self.output,
                    )
                    if merged is None:
                        self.output("Conflict unresolved. Candidate information was not saved.")
                        return False
                    self.profile = merged
                    candidate.mark(topic, ConfirmationStatus.ACCEPTED)
                    self.state.mark(topic, DiscoveryAnswerState.ANSWERED)
                    save_profile(self.profile, self.profile_path)
                    break
                if choice == "n":
                    candidate.mark(topic, ConfirmationStatus.REJECTED)
                    self.state.mark(topic, DiscoveryAnswerState.DECLINED)
                    break
                if choice == "e":
                    correction = self.input("Describe the correct information in one sentence: ").strip()
                    if not correction:
                        self.output("Correction cannot be empty.")
                        continue
                    corrected = normalize_candidate_data(
                        self.extractor.extract(correction, topic=topic)
                    )
                    candidate.replace_topic(topic, corrected.get(topic))
                    continue
                if choice == "q":
                    self.output("Pending candidate information was not saved.")
                    return False
                self.output("Invalid input: enter y, n, e, or q.")

        self._show_result(candidate)
        return True

    def _show_topic(self, topic: str, value: Any) -> None:
        self.output(f"\n{TOPIC_TITLES[topic]}")
        self.output(json.dumps(value, ensure_ascii=False, indent=2))

    def _show_result(self, candidate: CandidateProfile) -> None:
        accepted = [
            topic for topic, status in candidate.topic_statuses.items()
            if status == ConfirmationStatus.ACCEPTED
        ]
        self.output("Profile updated successfully.")
        self.output("Confirmed:")
        if not accepted:
            self.output("- No candidate topics")
        for topic in accepted:
            value = candidate.data[topic]
            count = len(value) if isinstance(value, list) else 1
            self.output(f"- {count} {TOPIC_TITLES[topic].lower()} item(s)")
        self.output(f"{missing_topic_count(self.profile)} discovery topics still need information.")
        if accepted:
            self.output(f"Saved to: {self.profile_path}")


def run_narrative_discovery(
    profile_path: str | Path,
    extractor: NarrativeExtractor,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> bool:
    return NarrativeConfirmationWorkflow(
        profile_path,
        extractor,
        input_fn=input_fn,
        output_fn=output_fn,
    ).run()
