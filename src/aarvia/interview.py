"""Interactive, deterministic career profile interview."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from .discovery import create_profile, generate_open_questions
from .profile import Education, ExperienceOverview, ProfileValidationError, Skill
from .storage import load_profile, save_profile

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]

SKIP = object()


class QuitInterview(Exception):
    """Internal control flow for a user-requested, progress-preserving exit."""


def parse_comma_list(value: str) -> list[str]:
    """Split a comma list, dropping blanks and exact duplicates in stable order."""
    result: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    if not result:
        raise ValueError("enter at least one value separated by commas")
    return result


class CareerDiscoveryInterview:
    """Stateful questionnaire with injectable terminal input and output."""

    def __init__(
        self,
        profile_path: str | Path,
        *,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
    ) -> None:
        self.profile_path = Path(profile_path)
        self.draft_path = self.profile_path.with_name(f"{self.profile_path.stem}.interview.json")
        self.input = input_fn
        self.output = output_fn
        self.profile = load_profile(self.profile_path) if self.profile_path.exists() else create_profile()
        self.draft = self._load_draft()
        save_profile(self.profile, self.profile_path)

    def run(self) -> bool:
        """Run one interview pass; return True only when the profile is complete."""
        try:
            self._collect_basic_profile()
            self._collect_records(
                "education",
                Education,
                [
                    ("institution", "Institution:", True, None),
                    ("degree", "Degree:", True, None),
                    ("field_of_study", "Field of study:", True, None),
                    ("start_date", "Start date (YYYY-MM or YYYY-MM-DD):", True, None),
                    (
                        "expected_graduation_date",
                        "Expected graduation date (YYYY-MM or YYYY-MM-DD):",
                        True,
                        None,
                    ),
                ],
                "Add another education entry? [y/N]",
            )
            self._collect_records(
                "experience_overview",
                ExperienceOverview,
                [
                    ("experience_type", "Experience type:", True, None),
                    ("organization_or_project_name", "Organization or project name:", True, None),
                    ("title_or_role", "Title or role:", True, None),
                    ("short_factual_summary", "Factual summary:", True, None),
                    ("start_date", "Start date (YYYY-MM or YYYY-MM-DD):", True, None),
                    ("end_date", "End date (YYYY-MM or YYYY-MM-DD, or :skip if ongoing):", False, None),
                ],
                "Add another experience entry? [y/N]",
            )
            self._collect_records(
                "skills",
                Skill,
                [
                    ("skill_name", "Skill name:", True, None),
                    ("category", "Skill category:", True, None),
                    ("self_reported_proficiency", "Self-reported proficiency (optional):", False, None),
                ],
                "Add another skill? [y/N]",
            )
            self._collect_preferences()
            self._collect_constraints()
        except QuitInterview:
            self.output("Progress saved.")
            self._show_status()
            return False

        return self._show_status()

    def _collect_basic_profile(self) -> None:
        self._set_missing_field("basic_profile", "current_location", "Current location:")
        self._set_missing_field("basic_profile", "current_status", "Current status:")

    def _collect_preferences(self) -> None:
        if not any(
            question in self.profile.open_questions
            for question in (
                "Which career fields interest you?",
                "What kinds of work activities do you prefer?",
                "Which industries interest you?",
                "Are there any roles you are currently considering?",
            )
        ):
            return
        fields = [
            ("interested_fields", "Interested fields (comma-separated):"),
            ("preferred_work_activities", "Preferred work activities (comma-separated):"),
            ("preferred_industries", "Preferred industries (comma-separated):"),
            ("fields_or_activities_to_avoid", "Fields or activities to avoid (comma-separated, optional):"),
            ("currently_considered_roles", "Currently considered roles (comma-separated):"),
        ]
        for field_name, prompt in fields:
            self._set_missing_field("career_preferences", field_name, prompt, parse_comma_list)

    def _collect_constraints(self) -> None:
        constraints = self.profile.constraints
        constraint_questions = {
            "Which locations are you targeting?",
            "Do you have any work authorization or visa constraints?",
            "Do you prefer remote, hybrid, or onsite work?",
            "Are you seeking an internship, a full-time role, or either?",
            "When would you like to start your next role?",
        }
        if not constraint_questions.intersection(self.profile.open_questions):
            return
        fields: list[tuple[str, str, Callable[[str], Any] | None]] = [
            ("target_locations", "Target locations (comma-separated):", parse_comma_list),
            ("work_authorization_or_visa_constraints", "Work authorization or visa constraints:", None),
            (
                "work_arrangement_preference",
                "Work arrangement [remote/hybrid/onsite/flexible]:",
                lambda value: value.lower(),
            ),
            (
                "employment_type_preference",
                "Employment type [internship/full-time/either]:",
                lambda value: value.lower(),
            ),
            ("target_start_date", "Target start date (YYYY-MM or YYYY-MM-DD):", None),
            ("other_constraints", "Other constraints (comma-separated, optional):", parse_comma_list),
        ]
        for field_name, prompt, parser in fields:
            current = getattr(constraints, field_name)
            if current not in (None, []):
                continue
            self._set_missing_field("constraints", field_name, prompt, parser)

    def _collect_records(
        self,
        section: str,
        model: type,
        fields: list[tuple[str, str, bool, Callable[[str], Any] | None]],
        another_prompt: str,
    ) -> None:
        if getattr(self.profile, section):
            if self.draft.pop(section, None) is not None:
                self._save_draft()
            return
        defaults = self._record_validation_defaults(model)
        while True:
            partial = self.draft.setdefault(section, {})
            for field_name, prompt, _required, parser in fields:
                if field_name in partial:
                    continue
                response = self._read(prompt, parser)
                if response is SKIP:
                    continue
                candidate = {**partial, field_name: response}
                try:
                    model.from_dict({**defaults, **candidate}, section)
                except ProfileValidationError as error:
                    self.output(f"Invalid input: {error}")
                    self._retry_record_field(section, field_name, prompt, parser, model, defaults)
                else:
                    partial[field_name] = response
                    self._save_draft()

            required = {name for name, _prompt, is_required, _parser in fields if is_required}
            if not required.issubset(partial):
                if not partial:
                    self.draft.pop(section, None)
                    self._save_draft()
                return

            record = model.from_dict(partial, section)
            data = self._profile_data()
            data[section].append(asdict(record))
            self.profile = create_profile(data)
            save_profile(self.profile, self.profile_path)
            self.draft.pop(section, None)
            self._save_draft()
            if not self._ask_yes_no(another_prompt):
                return

    def _retry_record_field(
        self,
        section: str,
        field_name: str,
        prompt: str,
        parser: Callable[[str], Any] | None,
        model: type,
        defaults: dict[str, Any],
    ) -> None:
        while True:
            response = self._read(prompt, parser)
            if response is SKIP:
                return
            partial = self.draft[section]
            try:
                model.from_dict({**defaults, **partial, field_name: response}, section)
            except ProfileValidationError as error:
                self.output(f"Invalid input: {error}")
                continue
            partial[field_name] = response
            self._save_draft()
            return

    def _set_missing_field(
        self,
        section: str,
        field_name: str,
        prompt: str,
        parser: Callable[[str], Any] | None = None,
    ) -> None:
        current = getattr(getattr(self.profile, section), field_name)
        if current not in (None, []):
            return
        while True:
            response = self._read(prompt, parser)
            if response is SKIP:
                return
            data = self._profile_data()
            data[section][field_name] = response
            try:
                candidate = create_profile(data)
            except ProfileValidationError as error:
                self.output(f"Invalid input: {error}")
                continue
            self.profile = candidate
            save_profile(self.profile, self.profile_path)
            return

    def _read(self, prompt: str, parser: Callable[[str], Any] | None = None) -> Any:
        while True:
            value = self.input(f"{prompt} ").strip()
            if value == ":quit":
                raise QuitInterview
            if not value or value == ":skip":
                return SKIP
            try:
                return parser(value) if parser else value
            except ValueError as error:
                self.output(f"Invalid input: {error}")

    def _ask_yes_no(self, prompt: str) -> bool:
        while True:
            response = self._read(prompt)
            if response is SKIP:
                return False
            normalized = response.lower()
            if normalized in {"y", "yes"}:
                return True
            if normalized in {"n", "no"}:
                return False
            self.output("Invalid input: enter y or n.")

    def _profile_data(self) -> dict[str, Any]:
        data = self.profile.to_dict()
        data.pop("open_questions")
        return data

    @staticmethod
    def _record_validation_defaults(model: type) -> dict[str, Any]:
        if model is Education:
            return {
                "institution": "pending", "degree": "pending", "field_of_study": "pending",
                "start_date": "2000-01", "expected_graduation_date": "2000-01",
            }
        if model is ExperienceOverview:
            return {
                "experience_type": "pending", "organization_or_project_name": "pending",
                "title_or_role": "pending", "short_factual_summary": "pending", "start_date": "2000-01",
            }
        if model is Skill:
            return {"skill_name": "pending", "category": "pending"}
        raise TypeError(f"unsupported record model: {model!r}")

    def _load_draft(self) -> dict[str, dict[str, Any]]:
        if not self.draft_path.exists():
            return {}
        data = json.loads(self.draft_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
            raise ProfileValidationError(f"{self.draft_path} contains an invalid interview draft")
        return data

    def _save_draft(self) -> None:
        if not self.draft:
            if self.draft_path.exists():
                self.draft_path.unlink()
            return
        self.draft_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.draft_path.parent, delete=False
        ) as handle:
            json.dump(self.draft, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.draft_path)

    def _show_status(self) -> bool:
        remaining = len(generate_open_questions(self.profile))
        if remaining == 0:
            self.output("Career profile complete.")
        else:
            self.output(f"Career profile incomplete: {remaining} open question(s).")
        self.output(f"Saved to: {self.profile_path}")
        return remaining == 0


def run_discovery(
    profile_path: str | Path,
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> bool:
    """Run an interactive discovery session for one profile path."""
    return CareerDiscoveryInterview(profile_path, input_fn=input_fn, output_fn=output_fn).run()
