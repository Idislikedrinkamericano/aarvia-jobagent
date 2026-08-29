"""Natural-language extraction into validated Candidate Profile data."""

from __future__ import annotations

import json
from typing import Any, Protocol

from .llm_client import LLMRequestError, LLMSettings, create_openai_client, safe_llm_error


class NarrativeExtractor(Protocol):
    def extract(self, narrative: str, *, topic: str | None = None) -> dict[str, Any]: ...


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
                    "start_date": {"type": "string"},
                    "expected_graduation_date": {"type": "string"},
                },
                "required": ["institution", "degree", "field_of_study", "start_date", "expected_graduation_date"],
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
                    "start_date": {"type": "string"},
                    "end_date": {"type": ["string", "null"]},
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
                "target_start_date": {"type": ["string", "null"]},
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
Represent projects directly in experience_overview with experience_type set to project.
Put target roles only in career_preferences.currently_considered_roles.
Put target locations and employment type only in their matching constraints fields.
Never output top-level projects, target_roles, target_locations, or target_employment_type fields.
Dates must use YYYY-MM or YYYY-MM-DD. Return only information from the requested topic when one is specified."""


class OpenAINarrativeExtractor:
    def __init__(self, *, settings: LLMSettings | None = None, client: Any | None = None) -> None:
        self.settings = settings or LLMSettings.from_environment()
        self.client = client or create_openai_client(self.settings)

    def extract(self, narrative: str, *, topic: str | None = None) -> dict[str, Any]:
        scope = f"\nExtract only this topic: {topic}." if topic else ""
        try:
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
            result = json.loads(response.output_text)
        except json.JSONDecodeError as error:
            raise LLMRequestError(
                "The LLM provider returned content that does not match the required candidate schema."
            ) from error
        except Exception as error:
            raise safe_llm_error(error) from error
        if not isinstance(result, dict):
            raise LLMRequestError(
                "The LLM provider returned content that does not match the required candidate schema."
            )
        if topic:
            return {topic: result.get(topic)}
        return {key: value for key, value in result.items() if value not in (None, [], {})}
