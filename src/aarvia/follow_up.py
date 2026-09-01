"""Adaptive, deterministic follow-up discovery for an existing CareerProfile."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .candidate_normalization import normalize_candidate_data
from .candidates import CandidateProfile
from .confirmation import (
    CANCEL_REFINEMENT,
    CANCELLED,
    NarrativeConfirmationWorkflow,
    RETRY_REFINEMENT,
    _candidate_diff,
    _validate_supported_changes,
    merge_candidate_topic,
    merge_refinement_topic,
)
from .constraint_evidence import apply_constraint_evidence, constraint_change_is_supported
from .discovery import generate_open_questions
from .discovery_state import (
    DISCOVERY_PATHS,
    DiscoveryState,
    FollowUpStatus,
    load_discovery_state,
    recover_follow_up_transaction,
    save_profile_and_state,
    state_path_for,
)
from .interview import InputFunction, OutputFunction
from .llm_client import LLMRequestError
from .narrative_extraction import NarrativeExtractor, OpenAINarrativeExtractor
from .profile import CareerProfile, ProfileValidationError
from .presentation import format_follow_up_candidate, format_session_summary


FOLLOW_UP_QUESTIONS = {
    "basic_profile.name": "What name would you like Aarvia to use?",
    "basic_profile.current_location": "Where are you currently based?",
    "basic_profile.current_status": (
        "What are you currently doing—for example, studying, working, or looking for work?"
    ),
    "career_preferences.interested_fields": "What career fields are you interested in?",
    "career_preferences.preferred_work_activities": "What kinds of work activities do you most enjoy?",
    "career_preferences.preferred_industries": (
        "What industries are you most interested in working in? You can name one or more, "
        "or enter :none if industry is not important to you."
    ),
    "career_preferences.currently_considered_roles": "What roles are you currently considering?",
    "constraints.target_locations": "Which locations are you targeting for your next role?",
    "constraints.work_authorization_or_visa_constraints": (
        "What work authorization or sponsorship requirements should Aarvia consider?"
    ),
    "constraints.work_arrangement_preference": "Do you prefer remote, hybrid, onsite, or flexible work?",
    "constraints.employment_type_preference": "Are you seeking an internship, full-time role, or either?",
    "constraints.target_start_date": "When would you like to start your next role?",
    "education_details": (
        "Would you like to add any missing education details, such as study dates, graduation dates, "
        "or an explicitly reported GPA?"
    ),
    "experience_details": (
        "Would you like to add missing dates or factual duties and accomplishments for your existing "
        "experience entries?"
    ),
    "skill_proficiency": (
        "Would you like to describe your proficiency in any listed skills? Only include levels you "
        "are comfortable reporting yourself."
    ),
}

PROFILE_TOPIC = {
    "education_details": "education",
    "experience_details": "experience_overview",
    "skill_proficiency": "skills",
}

COMPLETED_STATES = {FollowUpStatus.ANSWERED, FollowUpStatus.CONFIRMED_NONE}
END_SESSION = object()
RETRY_ANSWER = object()
CANCEL_ANSWER = object()


OPEN_QUESTION_PATHS = {
    "What is your current location?": "basic_profile.current_location",
    "What is your current education or employment status?": "basic_profile.current_status",
    "Which career fields interest you?": "career_preferences.interested_fields",
    "What kinds of work activities do you prefer?": "career_preferences.preferred_work_activities",
    "Which industries interest you?": "career_preferences.preferred_industries",
    "Are there any roles you are currently considering?": "career_preferences.currently_considered_roles",
    "Which locations are you targeting?": "constraints.target_locations",
    "Do you have any work authorization or visa constraints?": (
        "constraints.work_authorization_or_visa_constraints"
    ),
    "Do you prefer remote, hybrid, or onsite work?": "constraints.work_arrangement_preference",
    "Are you seeking an internship, a full-time role, or either?": (
        "constraints.employment_type_preference"
    ),
    "When would you like to start your next role?": "constraints.target_start_date",
}


def missing_information_paths(profile: CareerProfile) -> list[str]:
    questions = generate_open_questions(profile)
    missing = {OPEN_QUESTION_PATHS[question] for question in questions if question in OPEN_QUESTION_PATHS}
    if any("education background" in question for question in questions):
        missing.add("education_details")
    if any(
        question.startswith(("What work, internship", "What did you do or accomplish as "))
        for question in questions
    ):
        missing.add("experience_details")
    if any(question.startswith("What skills") for question in questions):
        missing.add("skill_proficiency")
    if not profile.education or any(
        item.start_date is None or item.expected_graduation_date is None
        for item in profile.education
    ):
        missing.add("education_details")
    if not profile.experience_overview or any(
        item.short_factual_summary is None or item.start_date is None
        for item in profile.experience_overview
    ):
        missing.add("experience_details")
    if not profile.skills or any(
        item.self_reported_proficiency is None for item in profile.skills
    ):
        missing.add("skill_proficiency")
    return [path for path in DISCOVERY_PATHS if path in missing]


def missing_follow_up_topics(profile: CareerProfile) -> list[str]:
    """Backward-compatible name for the now path-level missing detector."""
    return missing_information_paths(profile)


def select_next_topic(
    profile: CareerProfile,
    state: DiscoveryState,
    asked_this_session: set[str] | None = None,
) -> str | None:
    asked = asked_this_session or set()
    for path in missing_information_paths(profile):
        if path in asked or state.fields[path] in COMPLETED_STATES:
            continue
        return path
    return None


def _profile_topic(discovery_path: str) -> str:
    return PROFILE_TOPIC.get(discovery_path, discovery_path.split(".", 1)[0])


def project_candidate_to_path(
    candidate_data: dict[str, Any], discovery_path: str
) -> dict[str, Any]:
    """Keep only the active discovery path without changing its value."""
    topic = _profile_topic(discovery_path)
    if discovery_path in PROFILE_TOPIC:
        if topic not in candidate_data or candidate_data.get(topic) is None:
            return {}
        return {topic: candidate_data.get(topic)}

    section, field_name = discovery_path.split(".", 1)
    section_value = candidate_data.get(section)
    if section_value is None:
        return {}
    if not isinstance(section_value, dict):
        raise ProfileValidationError(f"{section} must be an object")
    return {section: {field_name: section_value.get(field_name)}}


def _profile_value(profile: CareerProfile, discovery_path: str) -> Any:
    if "." in discovery_path:
        section, field_name = discovery_path.split(".", 1)
        return getattr(getattr(profile, section), field_name)
    section = PROFILE_TOPIC[discovery_path]
    return getattr(profile, section)


def _changed_discovery_paths(
    before: CareerProfile, after: CareerProfile
) -> list[str]:
    return [
        path for path in DISCOVERY_PATHS
        if _profile_value(before, path) != _profile_value(after, path)
    ]


def _reconcile_answered_fields(
    profile: CareerProfile, state: DiscoveryState
) -> None:
    missing = set(missing_information_paths(profile))
    for path in DISCOVERY_PATHS:
        if "." in path and _profile_value(profile, path) not in (None, "", []):
            state.mark(path, FollowUpStatus.ANSWERED)
        elif "." not in path and path not in missing:
            state.mark(path, FollowUpStatus.ANSWERED)


def _actionable_missing_paths(
    profile: CareerProfile, state: DiscoveryState
) -> list[str]:
    return [
        path for path in missing_information_paths(profile)
        if state.fields[path] == FollowUpStatus.UNANSWERED
    ]


class AdaptiveFollowUpWorkflow(NarrativeConfirmationWorkflow):
    def __init__(
        self,
        profile_path: str | Path,
        extractor: NarrativeExtractor | None,
        *,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
        debug_extraction: bool = False,
        debug_full_profile: bool = False,
    ) -> None:
        path = Path(profile_path)
        if not path.exists():
            raise FileNotFoundError(f"Follow-up Profile does not exist: {path}")
        self.state_path = state_path_for(path)
        journal = path.with_name(f".{path.name}.follow-up-transaction.json")
        self.recovery_performed = journal.exists()
        recover_follow_up_transaction(journal)
        super().__init__(
            path,
            extractor,
            input_fn=input_fn,
            output_fn=output_fn,
            debug_extraction=debug_extraction,
        )
        self.original_profile = self.profile
        missing_paths = set(missing_information_paths(self.original_profile))
        self.original_state = load_discovery_state(
            self.state_path,
            profile=self.original_profile,
            missing_paths=missing_paths,
        )
        self.state_requires_persistence = self._state_file_requires_persistence()
        self.session_state = deepcopy(self.original_state)
        self.asked_this_session: set[str] = set()
        self.confirmed_fields: list[str] = []
        self.user_state_changed = False
        self.debug_full_profile = debug_full_profile
        _reconcile_answered_fields(self.session_profile, self.session_state)

    def run(self) -> bool:
        if self.debug_extraction:
            self.output(
                "Warning: follow-up debug output may contain personal information from your input. "
                "It is displayed only in this terminal and is not saved."
            )
        while True:
            discovery_path = select_next_topic(
                self.session_profile, self.session_state, self.asked_this_session
            )
            if discovery_path is None:
                break
            question = FOLLOW_UP_QUESTIONS[discovery_path]
            self.output(f"\n{question}")
            self.output(
                "Answer in one or more lines, then type .done. "
                "Commands: .cancel, :none, :skip, :decline, :finish, or q."
            )
            answer = self._read_follow_up_answer()
            if answer is CANCEL_ANSWER:
                self.output("Current answer cancelled. Let's try this question again.")
                continue
            if answer is END_SESSION:
                break
            if answer == "q":
                self.output("Session cancelled. The original Profile and discovery state were not changed.")
                return False
            if answer in {":none", ":skip", ":decline"}:
                status = {
                    ":none": FollowUpStatus.CONFIRMED_NONE,
                    ":skip": FollowUpStatus.SKIPPED,
                    ":decline": FollowUpStatus.DECLINED,
                }[answer]
                self.session_state.mark(discovery_path, status)
                self.user_state_changed = True
                self.asked_this_session.add(discovery_path)
                continue

            profile_topic = _profile_topic(discovery_path)
            self._debug("selected field path", discovery_path)
            self._debug("allowed field paths", [discovery_path])
            try:
                candidate = self._extract_follow_up(
                    answer, question, profile_topic, discovery_path
                )
            except (LLMRequestError, ProfileValidationError) as error:
                self._debug_provider_diagnostics("follow-up extraction failed")
                self.output(f"Follow-up extraction failed: {error}")
                self.output("Nothing changed. Please answer again, skip, decline, finish, or quit.")
                continue
            if not candidate.topics:
                self._debug(
                    "empty-result decision reason",
                    "the selected field remained empty after projection; only :none can confirm no value",
                )
                self.output(
                    "Aarvia could not extract an answer for this question. Nothing changed."
                )
                self.output(
                    "Please try again, or use :none, :skip, :decline, :finish, or q."
                )
                continue
            before_profile = self.session_profile
            accepted = self._review_candidate(
                candidate, profile_topic, discovery_path, question, answer
            )
            if accepted is RETRY_ANSWER:
                continue
            if accepted is None:
                return False
            self.asked_this_session.add(discovery_path)
            if accepted:
                changed = _changed_discovery_paths(before_profile, self.session_profile)
                _reconcile_answered_fields(self.session_profile, self.session_state)
                for path in changed:
                    if path not in self.confirmed_fields:
                        self.confirmed_fields.append(path)
            else:
                self.session_state.mark(discovery_path, FollowUpStatus.DECLINED)
                self.user_state_changed = True

        return self._finish()

    def _read_follow_up_answer(self) -> str | object:
        lines: list[str] = []
        prompt = "> "
        while True:
            line = self.input(prompt)
            command = line.strip().lower()
            if command in {"q", ":quit"}:
                return "q"
            if command == ":finish":
                return END_SESSION
            if command in {":none", ":skip", ":decline"}:
                return command
            if command == ".cancel":
                return CANCEL_ANSWER
            if command == ".done":
                answer = "\n".join(lines).strip()
                if answer:
                    return answer
                self.output("Answer cannot be empty. Enter text or use a command.")
                prompt = "> "
                continue
            lines.append(line.rstrip())
            prompt = "Add another line, or type .done to submit: "

    def _extract_follow_up(
        self,
        answer: str,
        question: str,
        profile_topic: str,
        discovery_path: str,
    ) -> CandidateProfile:
        self.output("Extracting information...")
        active_extractor = self._get_extractor()
        extractor = getattr(active_extractor, "extract_follow_up", None)
        if extractor is None:
            extracted = active_extractor.extract(answer, topic=profile_topic)
        else:
            extracted = extractor(
                answer,
                question=question,
                topic=profile_topic,
                formal_profile=self.original_profile.to_dict(),
                session_draft=self.session_profile.to_dict(),
                field_path=discovery_path,
            )
        self._debug("follow-up extraction stage", "raw provider output received")
        self._debug_provider_diagnostics("follow-up extraction")
        if profile_topic == "constraints":
            extracted, patch = apply_constraint_evidence(extracted, answer)
            self._debug("constraint evidence patch", patch)
        normalized = normalize_candidate_data(extracted)
        self._debug("normalized topic Candidate", normalized)
        normalized_candidate = CandidateProfile.from_extracted(normalized)
        if normalized_candidate.topics and normalized_candidate.topics != [profile_topic]:
            raise ProfileValidationError(
                f"follow-up answer must contain only {profile_topic}"
            )
        projected = project_candidate_to_path(normalized, discovery_path)
        self._debug("field projection result", projected)
        candidate = CandidateProfile.from_extracted(projected)
        return candidate

    def _review_candidate(
        self,
        candidate: CandidateProfile,
        profile_topic: str,
        discovery_path: str,
        question: str,
        answer: str,
    ) -> bool | object | None:
        while True:
            for line in format_follow_up_candidate(
                discovery_path, profile_topic, candidate.data[profile_topic]
            ):
                self.output(line)
            choice = self._read_choice("Accept this information? [y/n/e/q] ")
            if choice == "y":
                if discovery_path in PROFILE_TOPIC:
                    merged = merge_refinement_topic(
                        self.session_profile,
                        profile_topic,
                        candidate.data[profile_topic],
                        input_fn=self.input,
                        output_fn=self.output,
                    )
                    if merged is RETRY_REFINEMENT:
                        self.output("Please describe the existing record details again.")
                        return RETRY_ANSWER
                    if merged is CANCEL_REFINEMENT:
                        self.output(
                            "Session cancelled. The original Profile and discovery state were not changed."
                        )
                        return None
                else:
                    merged = merge_candidate_topic(
                        self.session_profile,
                        profile_topic,
                        candidate.data[profile_topic],
                        input_fn=self.input,
                        output_fn=self.output,
                    )
                if merged is None:
                    self.output("Conflict unresolved. The session draft was not changed.")
                    continue
                self.session_profile = merged
                merged_topic = self.session_profile.to_dict()[profile_topic]
                self._debug("current-topic merge result", merged_topic)
                if self.debug_full_profile:
                    self._debug("full Profile merge result", self.session_profile.to_dict())
                return True
            if choice == "n":
                return False
            if choice == "q":
                self.output("Session cancelled. The original Profile and discovery state were not changed.")
                return None
            if choice == "e":
                correction = self._read_correction()
                if correction is CANCELLED:
                    continue
                try:
                    corrected, constraint_patch = self._extract_follow_up_correction(
                        correction,
                        topic=profile_topic,
                        discovery_path=discovery_path,
                        current_topic=candidate.data[profile_topic],
                        original_narrative=f"{question}\n{answer}",
                    )
                    corrected_value = corrected.data[profile_topic]
                    changes = _candidate_diff(
                        candidate.data[profile_topic], corrected_value, profile_topic
                    )
                    self._debug("correction patch", {
                        "constraint_evidence": constraint_patch,
                        "diff": changes,
                    })
                    _validate_supported_changes(
                        changes,
                        answer,
                        correction,
                        semantic_support=(
                            constraint_change_is_supported
                            if profile_topic == "constraints"
                            else None
                        ),
                    )
                    self._debug("evidence-validation result", "accepted")
                except (LLMRequestError, ProfileValidationError) as error:
                    self._debug("evidence-validation result", f"rejected: {error}")
                    self._debug_provider_diagnostics("correction failed")
                    self.output(f"Correction failed: {error}")
                    self.output("The current Candidate is unchanged. Try again or choose y, n, or q.")
                    continue
                if not changes:
                    self.output("The correction produced no changes. The current Candidate is unchanged.")
                    continue
                self._show_diff(
                    changes,
                    topic=profile_topic,
                    before=candidate.data[profile_topic],
                    after=corrected_value,
                )
                apply_choice = self._read_choice(
                    "Apply this correction to the session Candidate? [y/N] "
                )
                if apply_choice not in {"y", "yes"}:
                    self.output("Correction discarded. The current Candidate is unchanged.")
                    continue
                candidate.replace_topic(profile_topic, corrected_value)
                continue
            self.output("Invalid input: enter y, n, e, or q.")

    def _extract_follow_up_correction(
        self,
        correction: str,
        *,
        topic: str,
        discovery_path: str,
        current_topic: Any,
        original_narrative: str,
    ) -> tuple[CandidateProfile, dict[str, Any]]:
        self.output("Extracting information...")
        active_extractor = self._get_extractor()
        extractor = getattr(active_extractor, "extract_correction", None)
        if extractor is None:
            extracted = active_extractor.extract(correction, topic=topic)
        else:
            extracted = extractor(
                correction,
                topic=topic,
                current_topic=current_topic,
                original_narrative=original_narrative,
                field_path=discovery_path,
            )
        self._debug_provider_diagnostics("correction extraction")
        patch: dict[str, Any] = {}
        if topic == "constraints":
            extracted, patch = apply_constraint_evidence(extracted, correction)
        normalized = normalize_candidate_data(extracted)
        self._debug("normalized topic Candidate", normalized)
        normalized_candidate = CandidateProfile.from_extracted(normalized)
        if normalized_candidate.topics and normalized_candidate.topics != [topic]:
            raise ProfileValidationError(f"correction must contain only {topic}")
        projected = project_candidate_to_path(normalized, discovery_path)
        self._debug("field projection result", projected)
        candidate = CandidateProfile.from_extracted(projected)
        if topic not in candidate.data:
            self._debug(
                "empty-result decision reason",
                "the correction did not contain a value for the selected field",
            )
            raise ProfileValidationError("correction did not contain the requested information")
        return candidate, patch

    def _debug_provider_diagnostics(self, stage: str) -> None:
        diagnostics = getattr(self.extractor, "diagnostics", None)
        self._debug("follow-up extraction stage", stage)
        self._debug("raw provider output", getattr(diagnostics, "raw_output", None))

    def _debug(self, label: str, value: Any) -> None:
        if not self.debug_extraction:
            return
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        api_key = getattr(getattr(self.extractor, "settings", None), "api_key", None)
        if api_key:
            rendered = rendered.replace(api_key, "[REDACTED]")
        self.output(f"[debug] {label}:")
        self.output(rendered)

    def _finish(self) -> bool:
        if self._is_noop():
            self.output("Career Profile is already complete.")
            self.output("No changes were made.")
            return True
        self.output("")
        for line in format_session_summary(
            self.original_profile,
            self.session_profile,
            confirmed_topics=self.confirmed_fields,
            original_state_fields=self.original_state.fields,
            state_fields=self.session_state.fields,
            still_missing_paths=_actionable_missing_paths(
                self.session_profile, self.session_state
            ),
        ):
            self.output(line)
        while True:
            choice = self._read_choice("Save all confirmed follow-up changes? [y/n] ")
            if choice in {"y", "yes"}:
                save_profile_and_state(
                    self.session_profile,
                    self.profile_path,
                    self.session_state,
                    self.state_path,
                )
                self.output(f"Saved Profile to: {self.profile_path}")
                self.output(f"Saved discovery state to: {self.state_path}")
                return True
            if choice in {"n", "no", "q", ":quit"}:
                self.output("Session cancelled. The original Profile and discovery state were not changed.")
                return False
            self.output("Invalid input: enter y or n.")

    def _get_extractor(self) -> NarrativeExtractor:
        if self.extractor is None:
            self.extractor = OpenAINarrativeExtractor()
        return self.extractor

    def _state_file_requires_persistence(self) -> bool:
        if not self.state_path.exists():
            return False
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        return raw != self.original_state.to_dict()

    def _is_noop(self) -> bool:
        return (
            select_next_topic(self.session_profile, self.session_state) is None
            and self.session_profile == self.original_profile
            and not self.user_state_changed
            and not self.state_requires_persistence
            and not self.recovery_performed
        )


def run_follow_up_discovery(
    profile_path: str | Path,
    extractor: NarrativeExtractor | None = None,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
    debug_extraction: bool = False,
    debug_full_profile: bool = False,
) -> bool:
    return AdaptiveFollowUpWorkflow(
        profile_path,
        extractor,
        input_fn=input_fn,
        output_fn=output_fn,
        debug_extraction=debug_extraction,
        debug_full_profile=debug_full_profile,
    ).run()
