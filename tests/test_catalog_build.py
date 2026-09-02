from copy import deepcopy

import pytest

from aarvia.catalog_build import (
    BuildBlockerCode,
    CatalogDraft,
    RoleSample,
    build_catalog_draft,
    deduplicate_sources,
    load_catalog_draft,
    prevalence_for_counts,
    save_catalog_draft,
    tier_a_share_is_sufficient,
)
from aarvia.jd_curation import CurationArtifact
from aarvia.jd_sources import (
    JDSourceCollection,
    content_sha256,
    generate_canonical_job_id,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from phase2b_fixtures import (
    FAKE_HASH,
    NOW,
    approved_candidate_data,
    cluster_data,
    curation_data,
    source_collection_data,
    source_data,
)


def contexts(company_count: int, *, tier_a_count: int | None = None, support_count: int | None = None):
    if tier_a_count is None:
        tier_a_count = company_count
    sources_data = [
        source_data(index, tier="tier_a_official" if index <= tier_a_count else "tier_b_verified_platform")
        for index in range(1, company_count + 1)
    ]
    sources = JDSourceCollection.from_dict(source_collection_data(sources_data))
    curation = CurationArtifact.from_dict(curation_data(sources_data, support_count=support_count))
    samples = tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in sources_data)
    return sources_data, sources, curation, samples


def test_exact_dedup_and_ambiguous_review_are_separate() -> None:
    official = source_data(1)
    platform = source_data(
        1,
        tier="tier_b_verified_platform",
        canonical_job_id=official["canonical_job_id"],
        canonical_source_reference=official["source_id"],
    )
    platform["verification_method"] = "platform_redirect_to_official"
    official["discovery_source_references"] = [platform["source_id"]]

    ambiguous = source_data(2)
    ambiguous["company_id"] = official["company_id"]
    ambiguous["company_display_name"] = official["company_display_name"]
    ambiguous["canonical_job_id"] = generate_canonical_job_id(
        company_id=ambiguous["company_id"],
        requisition_id=ambiguous["requisition_id"],
        canonical_url=ambiguous["source_url"],
        platform_name=ambiguous["platform_name"],
        platform_job_id=None,
    )
    collection = JDSourceCollection.from_dict(source_collection_data([official, platform, ambiguous]))
    result = deduplicate_sources(collection.sources)

    assert any(set(group) == {official["source_id"], platform["source_id"]} for group in result.canonical_groups)
    assert any(item.second_source_id == ambiguous["source_id"] for item in result.review_candidates)


def test_prevalence_uses_integer_company_boundaries() -> None:
    assert prevalence_for_counts(5, 5).value == "unknown"
    assert prevalence_for_counts(10, 7).value == "common"
    assert prevalence_for_counts(10, 6).value == "frequent"
    assert prevalence_for_counts(10, 4).value == "frequent"
    assert prevalence_for_counts(10, 3).value == "variable"


def test_tier_a_share_uses_canonical_sample_counts() -> None:
    assert tier_a_share_is_sufficient(6, 10)
    assert not tier_a_share_is_sufficient(5, 10)


def test_builder_blocks_five_companies_and_insufficient_tier_a_share() -> None:
    _, sources, curation, samples = contexts(5)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.INSUFFICIENT_COMPANIES in {item.code for item in result.blockers}

    _, sources, curation, samples = contexts(10, tier_a_count=5)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.INSUFFICIENT_TIER_A_SHARE in {item.code for item in result.blockers}


def test_builder_blocks_empty_and_ineligible_samples() -> None:
    data, sources, curation, _ = contexts(1)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=(), sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.NO_SAMPLES in {item.code for item in result.blockers}

    career_page = source_data(1, source_type="official_career_page")
    career_sources = JDSourceCollection.from_dict(source_collection_data([career_page]))
    career_curation = CurationArtifact.from_dict(curation_data([career_page]))
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=(RoleSample(data[0]["source_id"], "applied_ai_engineer"),),
        sources=career_sources, curation=career_curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.INELIGIBLE_SAMPLE in {item.code for item in result.blockers}


def test_same_company_contributes_only_one_role_sample() -> None:
    data, sources, curation, samples = contexts(6)
    duplicate = RoleSample(data[1]["source_id"], "applied_ai_engineer")
    altered = deepcopy(data[1])
    altered["company_id"] = data[0]["company_id"]
    altered["company_display_name"] = data[0]["company_display_name"]
    altered["canonical_job_id"] = generate_canonical_job_id(
        company_id=altered["company_id"], requisition_id=altered["requisition_id"],
        canonical_url=altered["source_url"], platform_name=altered["platform_name"], platform_job_id=None,
    )
    rebuilt_data = [data[0], altered, *data[2:]]
    rebuilt_sources = JDSourceCollection.from_dict(source_collection_data(rebuilt_data))
    rebuilt_curation = CurationArtifact.from_dict(curation_data(rebuilt_data))
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in rebuilt_data),
        sources=rebuilt_sources, curation=rebuilt_curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.DUPLICATE_COMPANY_SAMPLE in {item.code for item in result.blockers}


def test_approved_only_builder_produces_deterministic_draft_and_prevalence() -> None:
    _, sources, curation, samples = contexts(10, tier_a_count=6, support_count=7)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.blockers == ()
    assert result.draft is not None
    requirement = result.draft.requirements[0]
    assert requirement.sampled_company_count == 10
    assert requirement.supporting_company_count == 7
    assert requirement.prevalence.value == "common"
    assert requirement.importance.value == "core"


def test_discovery_source_does_not_inflate_company_or_tier_a_counts() -> None:
    data, _, _, samples = contexts(6)
    official = data[0]
    platform = source_data(
        1,
        tier="tier_b_verified_platform",
        canonical_job_id=official["canonical_job_id"],
        canonical_source_reference=official["source_id"],
    )
    platform["verification_method"] = "platform_redirect_to_official"
    official["discovery_source_references"] = [platform["source_id"]]
    all_sources = [*data, platform]
    sources = JDSourceCollection.from_dict(source_collection_data(all_sources))
    curation = CurationArtifact.from_dict(curation_data(data))

    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is not None
    assert result.draft.requirements[0].sampled_company_count == 6


def test_unapproved_candidate_does_not_enter_catalog_draft() -> None:
    data, sources, _, samples = contexts(6)
    curation_raw = curation_data(data)
    curation_raw["candidates"][0].update(
        status="review_pending", reviewer_decision="pending", decision_reason=None, reviewed_at=None
    )
    curation = CurationArtifact.from_dict(curation_raw)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.UNAPPROVED_CANDIDATE in {item.code for item in result.blockers}


def test_tier_c_cannot_be_the_only_requirement_evidence() -> None:
    data, _, _, samples = contexts(6)
    official = data[0]
    discovery = source_data(99, tier="tier_c_discovery_only")
    discovery.update(
        source_status="captured",
        content_hash=FAKE_HASH,
        canonical_job_id=official["canonical_job_id"],
        canonical_source_reference=official["source_id"],
        company_id=official["company_id"],
        company_display_name=official["company_display_name"],
        exact_job_title=official["exact_job_title"],
    )
    all_sources_data = [*data, discovery]
    sources = JDSourceCollection.from_dict(source_collection_data(all_sources_data))
    candidate = approved_candidate_data(discovery)
    curation_raw = curation_data([])
    curation_raw["candidates"] = [candidate]
    curation_raw["clusters"] = [cluster_data([candidate["candidate_id"]])]
    curation = CurationArtifact.from_dict(curation_raw)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.TIER_C_ONLY_EVIDENCE in {item.code for item in result.blockers}


def test_candidate_outside_selected_sample_is_a_structured_blocker() -> None:
    data, sources, curation, _ = contexts(7)
    samples = tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in data[:6])
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.CANDIDATE_OUTSIDE_SAMPLE in {item.code for item in result.blockers}


def test_published_catalog_draft_requires_human_publication_approval() -> None:
    _, sources, curation, samples = contexts(6)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is not None
    published = result.draft.to_dict()
    published["status"] = "published"
    with pytest.raises(Phase2ValidationError, match="publication approval"):
        CatalogDraft.from_dict(published)

    published.update(
        publication_decision="approve",
        publication_decision_reason="Fixture reviewer approved publication.",
        publication_reviewed_at=NOW,
    )
    CatalogDraft.from_dict(published).validate(sources, curation, production_role_catalog())


def test_catalog_draft_requires_newer_semantic_version() -> None:
    _, sources, curation, samples = contexts(6)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is not None
    data = result.draft.to_dict()

    data["target_catalog_version"] = "1.0.0"
    with pytest.raises(Phase2ValidationError, match="must be newer"):
        CatalogDraft.from_dict(data)
    data["target_catalog_version"] = "0.9.0"
    with pytest.raises(Phase2ValidationError, match="must be newer"):
        CatalogDraft.from_dict(data)

    data["base_catalog_version"] = "1.9.0"
    data["target_catalog_version"] = "1.10.0"
    assert CatalogDraft.from_dict(data).target_catalog_version == "1.10.0"


def test_catalog_draft_requires_approved_requirements() -> None:
    data, sources, _, samples = contexts(6)
    empty_curation_data = curation_data(data)
    empty_curation_data["candidates"] = []
    empty_curation_data["clusters"] = []
    empty_curation = CurationArtifact.from_dict(empty_curation_data)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=empty_curation,
        catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.NO_APPROVED_EVIDENCE in {item.code for item in result.blockers}

    valid = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources,
        curation=CurationArtifact.from_dict(curation_data(data)),
        catalog=production_role_catalog(),
    )
    assert valid.draft is not None
    bypass = valid.draft.to_dict()
    bypass["requirements"] = []
    with pytest.raises(Phase2ValidationError, match="approved requirement"):
        CatalogDraft.from_dict(bypass)


def test_catalog_draft_typed_storage_round_trip_and_context(tmp_path, monkeypatch) -> None:
    _, sources, curation, samples = contexts(6)
    result = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is not None
    path = save_catalog_draft(
        result.draft, tmp_path / "draft.json", sources=sources,
        curation=curation, catalog=production_role_catalog(),
    )
    duplicate = save_catalog_draft(
        result.draft, tmp_path / "copy.json", sources=sources,
        curation=curation, catalog=production_role_catalog(),
    )
    assert load_catalog_draft(
        path, sources=sources, curation=curation, catalog=production_role_catalog()
    ) == result.draft
    assert path.read_bytes() == duplicate.read_bytes()

    tampered = result.draft.to_dict()
    tampered["requirements"][0]["supporting_company_count"] = 0
    with pytest.raises(Phase2ValidationError, match="prevalence|counts do not match evidence"):
        CatalogDraft.from_dict(tampered).validate(sources, curation, production_role_catalog())

    original = path.read_bytes()
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_catalog_draft(
            result.draft, path, sources=sources,
            curation=curation, catalog=production_role_catalog(),
        )
    assert path.read_bytes() == original
