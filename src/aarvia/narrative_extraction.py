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
                    "short_factual_summary": {"type": "string"},
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
                "target_locations": {"type": "array", "items": {"type": "string"}},
                "work_authorization_or_visa_constraints": {"type": ["string", "null"]},
                "work_arrangement_preference": {"type": ["string", "null"]},
                "employment_type_preference": {"type": ["string", "null"]},
                "target_start_date": {
                    "type": ["string", "null"],
                    "description": "A known ISO 8601 calendar month or full date; null when unknown.",
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
Do not output fields outside the provided JSON Schema, including contact_info.
Omit optional topics with no information, or use only empty values allowed by the formal schema.
Do not create placeholder objects for information the user did not provide.
Use null or empty arrays only where the formal schema allows them. Do not embellish factual summaries.
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
        self.diagnostics = self._new_diagnostics()
        try:
            if self.diagnostics.protocol == "chat_completions":
                completion = self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=[
                        {
                            "role": "system",
                            "content": EXTRACTION_INSTRUCTIONS
                            + scope
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
                    instructions=EXTRACTION_INSTRUCTIONS + scope,
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
