"""Natural-language extraction into validated Candidate Profile data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from .llm_client import (
    LLMRequestError,
    LLMSettings,
    create_openai_client,
    is_bailian_endpoint,
    safe_llm_error,
)


class NarrativeExtractor(Protocol):
    def extract(self, narrative: str, *, topic: str | None = None) -> dict[str, Any]: ...

    def extract_correction(
        self,
        correction: str,
        *,
        topic: str,
        current_topic: Any,
        original_narrative: str,
        field_path: str | None = None,
    ) -> dict[str, Any]: ...

    def extract_follow_up(
        self,
        answer: str,
        *,
        question: str,
        topic: str,
        formal_profile: dict[str, Any],
        session_draft: dict[str, Any],
        field_path: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass
class ExtractionDiagnostics:
    stage: str = "raw parsing"
    raw_output: str | None = None
    json_decode_succeeded: bool = False
    detail: str | None = None
    protocol: str = "responses"
    structured_output_mode: str = "json_schema"


class ExtractionDebugError(LLMRequestError):
    """Safe structured diagnostics displayed only after explicit CLI opt-in."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        raw_output: str | None,
        json_decode_succeeded: bool,
        detail: str,
        model: str,
        base_url_host: str,
        protocol: str,
        structured_output_mode: str,
        api_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.raw_output = _redact_secret(raw_output, api_key)
        self.json_decode_succeeded = json_decode_succeeded
        self.detail = _redact_secret(detail, api_key) or "unavailable"
        self.model = model
        self.base_url_host = base_url_host
        self.protocol = protocol
        self.structured_output_mode = structured_output_mode


def _redact_secret(value: str | None, api_key: str | None) -> str | None:
    if value is None or not api_key:
        return value
    return value.replace(api_key, "[REDACTED]")


def _strip_single_json_fence(output: str) -> str:
    stripped = output.strip()
    if not stripped.startswith("```json"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[0].strip() != "```json" or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


class ProviderOutputStructureError(TypeError):
    """JSON decoded successfully but its top-level structure is invalid."""


def parse_provider_output(output: Any) -> dict[str, Any]:
    """Parse plain JSON or one complete json code fence without semantic repair."""
    if not isinstance(output, str):
        raise TypeError("response.output_text must be a string")
    result = json.loads(_strip_single_json_fence(output))
    if not isinstance(result, dict):
        raise ProviderOutputStructureError("candidate top level must be a JSON object")
    return result


PROFILE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "basic_profile": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": ["string", "null"]},
                "current_location": {"type": ["string", "null"]},
                "current_status": {"type": ["string", "null"]},
            },
            "required": ["name", "current_location", "current_status"],
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": "string"},
                    "field_of_study": {"type": "string"},
                    "start_date": {
                        "type": ["string", "null"],
                        "description": "A known ISO 8601 calendar month or full date; null when unknown.",
                    },
                    "expected_graduation_date": {
                        "type": ["string", "null"],
                        "description": "A known ISO 8601 calendar month or full date; null when unknown.",
                    },
                    "gpa": {"type": ["string", "null"]},
                },
                "required": [
                    "institution", "degree", "field_of_study", "start_date",
                    "expected_graduation_date", "gpa"
                ],
            },
        },
        "experience_overview": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "experience_type": {"type": "string"},
                    "organization_or_project_name": {"type": "string"},
                    "title_or_role": {"type": "string"},
                    "short_factual_summary": {"type": ["string", "null"]},
                    "start_date": {
                        "type": ["string", "null"],
                        "description": "A known ISO 8601 calendar month or full date; null when unknown.",
                    },
                    "end_date": {
                        "type": ["string", "null"],
                        "description": "A known ISO 8601 calendar month or full date; null when unknown.",
                    },
                },
                "required": [
                    "experience_type", "organization_or_project_name", "title_or_role",
                    "short_factual_summary", "start_date", "end_date"
                ],
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "skill_name": {"type": "string"},
                    "category": {"type": "string"},
                    "self_reported_proficiency": {"type": ["string", "null"]},
                },
                "required": ["skill_name", "category", "self_reported_proficiency"],
            },
        },
        "career_preferences": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "interested_fields": {"type": "array", "items": {"type": "string"}},
                "preferred_work_activities": {"type": "array", "items": {"type": "string"}},
                "preferred_industries": {"type": "array", "items": {"type": "string"}},
                "fields_or_activities_to_avoid": {"type": "array", "items": {"type": "string"}},
                "currently_considered_roles": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "interested_fields", "preferred_work_activities", "preferred_industries",
                "fields_or_activities_to_avoid", "currently_considered_roles"
            ],
        },
        "constraints": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "target_locations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every target location explicitly stated by the user; preserve multiple locations.",
                },
                "work_authorization_or_visa_constraints": {"type": ["string", "null"]},
                "work_arrangement_preference": {
                    "type": ["string", "null"],
                    "description": "remote, hybrid, onsite, or flexible; use flexible when all three modes are acceptable.",
                },
                "employment_type_preference": {"type": ["string", "null"]},
                "target_start_date": {
                    "type": ["string", "null"],
                    "description": "An explicitly stated ISO calendar month or full date; a named month and year may be converted exactly, but a season must remain null.",
                },
                "other_constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "target_locations", "work_authorization_or_visa_constraints", "work_arrangement_preference",
                "employment_type_preference", "target_start_date", "other_constraints"
            ],
        },
    },
    "required": [
        "basic_profile", "education", "experience_overview", "skills", "career_preferences", "constraints"
    ],
}


EXTRACTION_INSTRUCTIONS = """Extract only facts explicitly stated by the user into the provided schema.
Never infer interests, proficiency, dates, locations, visa status, outcomes, scale, or technologies.
If the user gives multiple names without explicitly choosing one as preferred, leave name null.
Never concatenate multiple names or choose a nickname on the user's behalf.
Do not output fields outside the provided JSON Schema, including contact_info.
Omit optional topics with no information, or use only empty values allowed by the formal schema.
Do not create placeholder objects for information the user did not provide.
Use null or empty arrays only where the formal schema allows them. Do not embellish factual summaries.
Use null for short_factual_summary when the user did not provide specific duties or accomplishments.
Never invent a summary from the organization, role, or experience type.
Only include education or experience records when every required field is explicit.
Use field_of_study, never major. Preserve an explicitly stated GPA in gpa exactly as written;
use null when GPA is absent, and never convert, round, infer, or evaluate it.
Represent projects directly in experience_overview with experience_type set to project.
Put target roles only in career_preferences.currently_considered_roles.
Put target locations and employment type only in their matching constraints fields.
Never output top-level projects, target_roles, target_locations, or target_employment_type fields.
For every date field, use JSON null when the user did not state a date. Never guess a date.
Never emit format placeholders such as YYYY-MM or YYYY-MM-DD as field values.
Known dates must use YYYY-MM or YYYY-MM-DD. Return only information from the requested topic when one is specified."""

CORRECTION_INSTRUCTIONS = """Apply a user's correction only to the supplied current Candidate topic.
The original narrative is evidence, the current topic is the baseline, and the correction defines the requested change.
Return the complete corrected topic, preserving every field and record the user did not ask to change.
Do not add a value unless it is supported by the original narrative or the correction.
Do not reinterpret, summarize, or regenerate unchanged facts. Follow the supplied JSON Schema exactly."""

FOLLOW_UP_INSTRUCTIONS = """Extract only the requested follow-up topic from the user's answer.
The formal Profile and session draft are context only and must not be rewritten or echoed.
Do not return or modify any other topic. Do not infer facts the user did not state in the answer.
When a selected field path is supplied, populate only that exact path inside its canonical parent
topic. Keep every sibling field empty or null as allowed by the schema.
Missing information must remain null, an empty list, or absent as allowed by the schema.
For Constraints, preserve every explicitly stated target location. If onsite, hybrid, and remote are
all acceptable, use flexible. Convert an explicit named month and year to its exact calendar month,
but never convert a season such as Summer to a guessed month.
Follow the supplied JSON Schema exactly."""


class OpenAINarrativeExtractor:
    def __init__(self, *, settings: LLMSettings | None = None, client: Any | None = None) -> None:
        self.settings = settings or LLMSettings.from_environment()
        self.client = client or create_openai_client(self.settings)
        self.diagnostics = self._new_diagnostics()

    def _new_diagnostics(self) -> ExtractionDiagnostics:
        protocol = "chat_completions" if is_bailian_endpoint(self.settings.base_url) else "responses"
        return ExtractionDiagnostics(protocol=protocol)

    @property
    def base_url_host(self) -> str:
        if self.settings.base_url is None:
            return "default OpenAI endpoint"
        return urlparse(self.settings.base_url).hostname or "invalid or unavailable"

    def extract(self, narrative: str, *, topic: str | None = None) -> dict[str, Any]:
        scope = f"\nExtract only this topic: {topic}." if topic else ""
        return self._extract_with_messages(
            narrative,
            instructions=EXTRACTION_INSTRUCTIONS + scope,
            topic=topic,
        )

    def extract_correction(
        self,
        correction: str,
        *,
        topic: str,
        current_topic: Any,
        original_narrative: str,
        field_path: str | None = None,
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "topic": topic,
                "selected_field_path": field_path,
                "original_narrative": original_narrative,
                "current_candidate_topic": current_topic,
                "user_correction": correction,
            },
            ensure_ascii=False,
        )
        field_scope = (
            f"\nCorrect only this field path: {field_path}."
            if field_path else ""
        )
        return self._extract_with_messages(
            payload,
            instructions=(
                CORRECTION_INSTRUCTIONS
                + f"\nCorrect only this topic: {topic}."
                + field_scope
            ),
            topic=topic,
        )

    def extract_follow_up(
        self,
        answer: str,
        *,
        question: str,
        topic: str,
        formal_profile: dict[str, Any],
        session_draft: dict[str, Any],
        field_path: str | None = None,
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "current_question": question,
                "selected_field_path": field_path,
                "user_answer": answer,
                "formal_profile": formal_profile,
                "session_draft": session_draft,
            },
            ensure_ascii=False,
        )
        field_scope = (
            "\nThe selected field path is "
            f"{field_path}. Populate only that path inside {topic}; keep sibling fields empty."
            if field_path else ""
        )
        return self._extract_with_messages(
            payload,
            instructions=(
                FOLLOW_UP_INSTRUCTIONS
                + f"\nExtract only this topic: {topic}."
                + field_scope
            ),
            topic=topic,
        )

    def _extract_with_messages(
        self,
        narrative: str,
        *,
        instructions: str,
        topic: str | None,
    ) -> dict[str, Any]:
        self.diagnostics = self._new_diagnostics()
        try:
            if self.diagnostics.protocol == "chat_completions":
                completion = self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=[
                        {
                            "role": "system",
                            "content": instructions
                            + "\nReturn one JSON object that strictly follows the supplied JSON Schema.",
                        },
                        {"role": "user", "content": narrative},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "career_profile_candidate",
                            "strict": True,
                            "schema": PROFILE_JSON_SCHEMA,
                        },
                    },
                    extra_body={"enable_thinking": False},
                )
                raw_output = completion.choices[0].message.content
            else:
                response = self.client.responses.create(
                    model=self.settings.model,
                    instructions=instructions,
                    input=narrative,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "career_profile_candidate",
                            "strict": True,
                            "schema": PROFILE_JSON_SCHEMA,
                        }
                    },
                    store=False,
                )
                raw_output = response.output_text
            self.diagnostics.raw_output = raw_output
            result = parse_provider_output(raw_output)
            self.diagnostics.json_decode_succeeded = True
        except ProviderOutputStructureError as error:
            self.diagnostics.json_decode_succeeded = True
            self.diagnostics.detail = str(error)
            raise LLMRequestError(
                "The LLM provider returned content that does not match the required candidate schema."
            ) from error
        except (json.JSONDecodeError, TypeError) as error:
            self.diagnostics.detail = str(error)
            raise LLMRequestError(
                "The LLM provider returned content that does not match the required candidate schema."
            ) from error
        except Exception as error:
            raise safe_llm_error(error) from error
        if topic:
            return {topic: result.get(topic)}
        return {key: value for key, value in result.items() if value not in (None, [], {})}
