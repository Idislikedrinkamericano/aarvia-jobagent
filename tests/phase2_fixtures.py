"""Explicitly test-only Phase 2A source, requirement, Profile, and job data."""

from __future__ import annotations

from copy import deepcopy

from aarvia import create_profile
from aarvia.role_catalog import RoleCatalog, production_role_catalog


def profile_fixture():
    return create_profile(
        {
            "basic_profile": {
                "name": "Lin",
                "current_location": "Example City",
                "current_status": "Graduate student",
            },
            "education": [],
            "experience_overview": [],
            "skills": [
                {
                    "skill_name": "Python",
                    "category": "programming language",
                    "self_reported_proficiency": None,
                }
            ],
            "career_preferences": {
                "interested_fields": ["Applied AI"],
                "preferred_work_activities": ["Building software"],
                "preferred_industries": [],
                "fields_or_activities_to_avoid": [],
                "currently_considered_roles": ["AI Engineer"],
            },
            "constraints": {
                "target_locations": ["Example City"],
                "work_authorization_or_visa_constraints": None,
                "work_arrangement_preference": "hybrid",
                "employment_type_preference": "full-time",
                "target_start_date": None,
                "other_constraints": [],
            },
        }
    )


def catalog_fixture_data() -> dict:
    data = deepcopy(production_role_catalog().to_dict())
    data["catalog_type"] = "test_fixture"
    data["sources"] = [
        {
            "source_id": "fixture_job_1",
            "source_type": "test_fixture",
            "company": "Fixture Company",
            "job_title": "Fixture AI Engineer",
            "official_url": "https://jobs.example.test/fixture-ai-engineer",
            "captured_date": "2026-09-01",
            "publication_date": "2026-08-20",
            "expiration_date": None,
            "notes": "Synthetic test data; never load into a production catalog.",
            "status": "test_fixture",
            "is_test_fixture": True,
        }
    ]
    data["requirements"] = [
        {
            "requirement_id": "applied_ai.python_fixture",
            "role_id": "applied_ai_engineer",
            "name": "Python fixture requirement",
            "description": "Synthetic requirement used only to test references.",
            "category": "technical_skill",
            "importance": "core",
            "prevalence": "frequent",
            "applicable_specializations": ["agentic_ai"],
            "source_references": ["fixture_job_1"],
            "notes": "Not a market claim.",
        }
    ]
    data["roles"][0]["requirement_ids"] = ["applied_ai.python_fixture"]
    data["roles"][0]["source_references"] = ["fixture_job_1"]
    return data


def catalog_fixture() -> RoleCatalog:
    return RoleCatalog.from_dict(catalog_fixture_data())


def job_source_fixture(*, status: str = "active") -> dict:
    fixture = catalog_fixture_data()["sources"][0]
    fixture = deepcopy(fixture)
    fixture.update(
        {
            "source_id": "fixture_live_job_1",
            "source_type": "official_job_posting",
            "status": status,
            "is_test_fixture": True,
        }
    )
    return fixture


def live_job_fixture_data(*, source_status: str = "active") -> dict:
    return {
        "schema": "aarvia.live_jobs",
        "schema_version": 1,
        "collection_id": "fixture_live_jobs",
        "catalog_version": "1.0.0",
        "collection_type": "test_fixture",
        "captured_at": "2026-09-01T12:00:00+08:00",
        "sources": [job_source_fixture(status=source_status)],
        "jobs": [
            {
                "job_id": "fixture_job_internal_1",
                "company": "Fixture Company",
                "exact_job_title": "Fixture AI Engineer",
                "official_job_url": "https://jobs.example.test/fixture-ai-engineer",
                "official_application_url": "https://jobs.example.test/fixture-ai-engineer/apply",
                "application_url_status": "provided_unverified",
                "application_url_last_verified_at": None,
                "application_url_source_reference": "fixture_live_job_1",
                "location": "Example City",
                "employment_type": "full_time",
                "posting_date": "2026-08-20",
                "expiration_date": None,
                "last_verified_at": "2026-09-01T12:00:00+08:00",
                "listing_status": "verified_open",
                "source_reference": "fixture_live_job_1",
                "mapped_role_id": "applied_ai_engineer",
                "mapped_specialization_id": "agentic_ai",
                "eligibility_status": "unknown",
                "preliminary_match_status": "not_analyzed",
                "structured_jd_requirements": [
                    {
                        "job_requirement_id": "fixture_jd_python",
                        "name": "Python",
                        "description": "Synthetic JD requirement.",
                        "category": "technical_skill",
                        "importance": "core",
                        "explicitly_required": True,
                    }
                ],
            }
        ],
    }
