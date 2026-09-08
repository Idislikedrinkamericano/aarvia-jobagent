"""Synthetic-only Phase 2B-1 source and curation fixtures."""

from __future__ import annotations

from aarvia.jd_curation import ExtractionMetadata, RequirementCandidate
from aarvia.jd_sources import content_sha256, generate_canonical_job_id, generate_source_id


NOW = "2026-09-01T12:00:00+00:00"
FAKE_JD = "Build reliable machine learning applications.\nUse Python.\n"
FAKE_HASH = content_sha256(FAKE_JD)


def confirmed_assignments(sources, *, role_id="applied_ai_engineer", specialization_id="agentic_ai"):
    """Build synthetic human-confirmed assignments and their capture content."""
    from aarvia.role_assignments import (
        RoleAssignmentArtifact,
        RoleEvidenceReference,
        confirm_initial_role_assignment,
    )
    from aarvia.role_catalog import CatalogType, production_role_catalog

    artifact = RoleAssignmentArtifact(
        artifact_id="fixture_role_assignments",
        artifact_type=CatalogType.TEST_FIXTURE,
        source_collection_id=sources.collection_id,
        catalog_version="1.0.0",
        created_at=NOW,
        assignments=(),
        review_records=(),
    )
    contents = {capture.capture_id: FAKE_JD for capture in sources.captures}
    excerpt = "Build reliable machine learning applications."
    for item in sources.sources:
        if item.canonical_job_id is None or item.canonical_source_reference not in {None, item.source_id}:
            continue
        capture = sources.matching_captures(item.source_id, FAKE_HASH)[0]
        evidence = RoleEvidenceReference(
            source_id=item.source_id,
            capture_id=capture.capture_id,
            content_hash=capture.content_hash,
            start_offset=0,
            end_offset=len(excerpt),
            exact_value_snapshot=excerpt,
        )
        artifact = confirm_initial_role_assignment(
            artifact,
            canonical_job_id=item.canonical_job_id,
            source_id=item.source_id,
            role_id=role_id,
            specialization_id=specialization_id,
            evidence_references=(evidence,),
            reviewer_reference="fixture.role_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture human confirmed the Role mapping.",
            sources=sources,
            catalog=production_role_catalog(),
            capture_contents=contents,
        )
    return artifact, contents


def source_data(
    index: int = 1,
    *,
    tier: str = "tier_a_official",
    source_type: str | None = None,
    platform: str | None = None,
    canonical_job_id: str | None = None,
    canonical_source_reference: str | None = None,
    discovery_source_references: list[str] | None = None,
) -> dict:
    company_id = f"fixture_company_{index}"
    if tier == "tier_a_official":
        source_type = source_type or "official_job_posting"
        platform = platform or "workday"
        url = f"https://jobs.example.test/company-{index}/job-{index}"
        platform_job_id = None
        requisition_id = f"REQ-{index}"
        application_url = f"https://jobs.example.test/company-{index}/job-{index}/apply"
        method = "official_ats_direct"
    elif tier == "tier_b_verified_platform":
        source_type = source_type or "platform_job_posting"
        platform = platform or "linkedin"
        url = f"https://platform.example.test/jobs/{index}"
        platform_job_id = f"PLATFORM-{index}"
        requisition_id = None
        application_url = f"https://platform.example.test/jobs/{index}/apply"
        method = "platform_apply_available"
    else:
        source_type = source_type or "aggregator_listing"
        platform = platform or "other"
        url = f"https://aggregator.example.test/jobs/{index}"
        platform_job_id = None
        requisition_id = None
        application_url = None
        method = "not_verified"
    source_id = generate_source_id(url, platform_name=platform, platform_job_id=platform_job_id)
    if canonical_job_id is None and tier != "tier_c_discovery_only":
        canonical_job_id = generate_canonical_job_id(
            company_id=company_id,
            requisition_id=requisition_id,
            canonical_url=url if tier == "tier_a_official" else None,
            platform_name=platform,
            platform_job_id=platform_job_id,
        )
    return {
        "source_id": source_id,
        "source_url": url,
        "source_type": source_type,
        "source_tier": tier,
        "source_status": "verified" if tier != "tier_c_discovery_only" else "discovered",
        "company_id": company_id,
        "company_display_name": f"Fixture Company {index}",
        "exact_job_title": "Fixture AI Engineer" if tier != "tier_c_discovery_only" else None,
        "platform_name": platform,
        "platform_job_id": platform_job_id,
        "requisition_id": requisition_id,
        "application_url": application_url,
        "location": "Example City, US",
        "country_code": "US",
        "market": "united_states_early_career",
        "career_stage": "early_career",
        "experience_range": {"status": "explicit", "minimum_years": 0, "maximum_years": 2},
        "canonical_job_id": canonical_job_id,
        "canonical_source_reference": canonical_source_reference,
        "discovery_source_references": discovery_source_references or [],
        "captured_at": NOW,
        "last_verified_at": NOW if tier != "tier_c_discovery_only" else None,
        "verification_method": method,
        "content_hash": FAKE_HASH if tier != "tier_c_discovery_only" else None,
        "hash_normalization_version": "jd-text-v1",
        "identity_normalization_version": "jd-identity-v1",
        "is_test_fixture": True,
    }


def source_collection_data(sources: list[dict]) -> dict:
    return {
        "schema": "aarvia.jd_sources",
        "schema_version": 2,
        "collection_id": "fixture_source_collection",
        "collection_type": "test_fixture",
        "created_at": NOW,
        "sources": sources,
    }


def live_v2_data(source: dict) -> dict:
    return {
        "schema": "aarvia.live_jobs",
        "schema_version": 2,
        "collection_id": "fixture_live_jobs_v2",
        "catalog_version": "1.0.0",
        "collection_type": "test_fixture",
        "source_collection_id": "fixture_source_collection",
        "captured_at": NOW,
        "jobs": [{
            "job_id": source["canonical_job_id"],
            "canonical_job_id": source["canonical_job_id"],
            "canonical_source_reference": source["source_id"],
            "discovery_source_references": [],
            "company_id": source["company_id"],
            "company": source["company_display_name"],
            "exact_job_title": source["exact_job_title"],
            "job_url": source["source_url"],
            "application_url": source["application_url"],
            "application_url_status": "verified_active",
            "application_url_last_verified_at": source["last_verified_at"],
            "application_url_source_reference": source["source_id"],
            "location": source["location"],
            "employment_type": "internship",
            "posting_date": None,
            "expiration_date": None,
            "last_verified_at": source["last_verified_at"],
            "listing_status": "verified_official_open",
            "mapped_role_id": "applied_ai_engineer",
            "mapped_specialization_id": "agentic_ai",
            "eligibility_status": "unknown",
            "preliminary_match_status": "not_analyzed",
            "structured_jd_requirements": [],
        }],
    }


def extraction_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(
        extractor="fixture_extractor",
        provider="mock_provider",
        model="mock_model",
        prompt_version="fixture-v1",
        extracted_at=NOW,
    )


def extracted_candidate(source: dict, *, name: str = "Python", role_id: str = "applied_ai_engineer") -> RequirementCandidate:
    return RequirementCandidate.from_extraction(
        {
            "evidence": {
                "source_content_hash": source["content_hash"],
                "section": "Qualifications",
                "start_offset": 0,
                "end_offset": 10,
                "minimal_excerpt": "Use Python.",
            },
            "proposed_name": name,
            "proposed_description": f"Use {name} in production work.",
            "proposed_category": "technical_skill",
            "proposed_importance": "core",
            "mapped_role_id": role_id,
            "mapped_specialization_id": "agentic_ai",
        },
        source_id=source["source_id"],
        source_content_hash=source["content_hash"],
        extraction=extraction_metadata(),
    )


def approved_candidate_data(source: dict, *, cluster_id: str = "cluster_python") -> dict:
    data = extracted_candidate(source).to_dict()
    data.update(
        status="approved",
        cluster_id=cluster_id,
        reviewer_decision="approve",
        decision_reason="Fixture human review approved the normalized requirement.",
        reviewed_at=NOW,
    )
    return data


def cluster_data(candidate_ids: list[str], *, cluster_id: str = "cluster_python") -> dict:
    return {
        "cluster_id": cluster_id,
        "normalized_name": "Python",
        "normalized_description": "Uses Python to build production software.",
        "role_id": "applied_ai_engineer",
        "specialization_id": "agentic_ai",
        "category": "technical_skill",
        "importance": "core",
        "candidate_ids": candidate_ids,
        "status": "confirmed",
        "reviewer_decision": "approve",
        "decision_reason": "Fixture human review confirmed the cluster.",
        "reviewed_at": NOW,
    }


def curation_data(sources: list[dict], *, support_count: int | None = None) -> dict:
    selected = sources if support_count is None else sources[:support_count]
    candidates = [approved_candidate_data(source) for source in selected]
    return {
        "schema": "aarvia.jd_curation",
        "schema_version": 2,
        "artifact_id": "fixture_curation",
        "artifact_type": "test_fixture",
        "source_collection_id": "fixture_source_collection",
        "catalog_version": "1.0.0",
        "created_at": NOW,
        "candidates": candidates,
        "clusters": [cluster_data([item["candidate_id"] for item in candidates])],
    }
