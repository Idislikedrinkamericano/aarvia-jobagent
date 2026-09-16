from copy import deepcopy
from dataclasses import replace

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
from aarvia.jd_curation import (
    CandidateLifecycleStatus,
    CandidateReviewAction,
    CandidateReviewRecord,
    CurationArtifact,
    CurationArtifactV3,
    CurationArtifactV4,
    CurationArtifactV5,
    CurationArtifactV6,
    CandidateLogicGroup,
    ClusterLifecycleStatus,
    EvidenceLocator,
    RequirementClusterV5,
    ReviewerDecision,
    migrate_v2_to_v3,
    approve_candidate,
    generate_candidate_id,
    generate_candidate_v4_id,
    generate_cluster_v5_id,
    migrate_v3_to_v4,
    migrate_curation_v4_to_v5,
    migrate_curation_v5_to_v6,
    confirm_candidate_logic_group,
    reject_candidate_logic_group,
)
from aarvia.jd_sources import (
    JDSourceCollection,
    JDSourceCollectionV3,
    content_sha256,
    generate_canonical_job_id,
    migrate_source_v2_to_v3,
    normalize_jd_content,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.requirement_logic import RequirementLogicOperator, RequirementModality
from aarvia.curation_workflow import (
    ClusterReviewAction,
    ClusterReviewRecord,
    CurationArtifactV7,
    confirm_candidate_logic_group_v7,
    confirm_cluster,
    migrate_curation_v6_to_v7,
    quarantine_candidate_logic_group,
    reject_logic_group_and_members,
    release_logic_group_members,
)
from phase2b_fixtures import (
    FAKE_HASH,
    NOW,
    approved_candidate_data,
    cluster_data,
    confirmed_assignments,
    curation_data,
    source_collection_data,
    source_data,
)
from test_role_assignments import context as role_assignment_context


def reviewed_fixture_v3(curation: CurationArtifact) -> CurationArtifactV3:
    data = curation.to_dict()
    data["schema_version"] = 3
    data["review_records"] = [
        CandidateReviewRecord.create(
            parent_candidate_id=candidate.candidate_id,
            action=(
                CandidateReviewAction.APPROVE
                if candidate.status.value == "approved"
                else CandidateReviewAction.REJECT
            ),
            successor_candidate_ids=(),
            reviewer_reference="fixture_legacy_reviewer",
            reviewed_at=candidate.reviewed_at,
            decision_reason=candidate.decision_reason,
        ).to_dict()
        for candidate in curation.candidates
        if candidate.status.value in {"approved", "rejected"}
    ]
    return CurationArtifactV3.from_dict(data)


def upgrade_for_builder(
    sources_data: list[dict], curation: CurationArtifactV3
) -> tuple[JDSourceCollectionV3, CurationArtifactV4]:
    legacy_sources = JDSourceCollection.from_dict(source_collection_data(sources_data))
    sources = migrate_source_v2_to_v3(
        legacy_sources,
        scope_by_source_id={item["source_id"]: "full_job_description" for item in sources_data if item["content_hash"] is not None},
        content_length_by_source_id={item["source_id"]: len(normalize_jd_content("Build reliable machine learning applications.\nUse Python.\n")) for item in sources_data if item["content_hash"] is not None},
    )
    return sources, migrate_v3_to_v4(
        curation, sources=sources, catalog=production_role_catalog()
    )


def build_fixture(**kwargs):
    curation = kwargs["curation"]
    if isinstance(curation, CurationArtifactV4) and not isinstance(curation, CurationArtifactV5):
        assignments, contents = confirmed_assignments(kwargs["sources"])
        kwargs["curation"] = migrate_curation_v4_to_v5(
            curation,
            assignments=assignments,
            sources=kwargs["sources"],
            catalog=kwargs["catalog"],
            capture_contents=contents,
        )
        kwargs["assignments"] = assignments
        kwargs["capture_contents"] = contents
    if isinstance(kwargs["curation"], CurationArtifactV5) and not isinstance(
        kwargs["curation"], CurationArtifactV6
    ):
        kwargs["curation"] = migrate_curation_v5_to_v6(
            kwargs["curation"],
            sources=kwargs["sources"],
            catalog=kwargs["catalog"],
            assignments=kwargs["assignments"],
            capture_contents=kwargs["capture_contents"],
        )
    if isinstance(kwargs["curation"], CurationArtifactV6) and not isinstance(
        kwargs["curation"], CurationArtifactV7
    ):
        v6 = kwargs["curation"]
        proposed_clusters = tuple(
            replace(
                item,
                status=ClusterLifecycleStatus.PROPOSED,
                reviewer_decision=ReviewerDecision.PENDING,
                decision_reason=None,
                reviewed_at=None,
            )
            for item in v6.clusters
        )
        v6 = replace(v6, clusters=proposed_clusters)
        v7 = migrate_curation_v6_to_v7(
            v6,
            sources=kwargs["sources"],
            catalog=kwargs["catalog"],
            assignments=kwargs["assignments"],
            capture_contents=kwargs["capture_contents"],
        )
        candidate_map = {item.candidate_id: item for item in v7.candidates}
        for cluster in tuple(v7.clusters):
            if all(
                candidate_map[item].status == CandidateLifecycleStatus.APPROVED
                for item in cluster.candidate_ids
            ):
                v7 = confirm_cluster(
                    v7,
                    cluster.cluster_id,
                    reviewer_reference="fixture.cluster_reviewer",
                    reviewed_at=NOW,
                    decision_reason="Fixture reviewer confirmed this semantic Cluster.",
                    sources=kwargs["sources"],
                    catalog=kwargs["catalog"],
                    assignments=kwargs["assignments"],
                    capture_contents=kwargs["capture_contents"],
                )
        kwargs["curation"] = v7
    return build_catalog_draft(**kwargs)


def upgrade_curation_to_v7(curation, sources, assignments, contents):
    if isinstance(curation, CurationArtifactV4) and not isinstance(
        curation, CurationArtifactV5
    ):
        curation = migrate_curation_v4_to_v5(
            curation,
            assignments=assignments,
            sources=sources,
            catalog=production_role_catalog(),
            capture_contents=contents,
        )
    if isinstance(curation, CurationArtifactV5) and not isinstance(
        curation, CurationArtifactV6
    ):
        curation = migrate_curation_v5_to_v6(
            curation,
            assignments=assignments,
            sources=sources,
            catalog=production_role_catalog(),
            capture_contents=contents,
        )
    if isinstance(curation, CurationArtifactV6) and not isinstance(
        curation, CurationArtifactV7
    ):
        curation = replace(
            curation,
            clusters=tuple(
                replace(
                    item,
                    status=ClusterLifecycleStatus.PROPOSED,
                    reviewer_decision=ReviewerDecision.PENDING,
                    decision_reason=None,
                    reviewed_at=None,
                )
                for item in curation.clusters
            ),
        )
        curation = migrate_curation_v6_to_v7(
            curation,
            sources=sources,
            catalog=production_role_catalog(),
            assignments=assignments,
            capture_contents=contents,
        )
        candidate_map = {item.candidate_id: item for item in curation.candidates}
        for cluster in tuple(curation.clusters):
            if all(
                candidate_map[candidate_id].status
                == CandidateLifecycleStatus.APPROVED
                for candidate_id in cluster.candidate_ids
            ):
                curation = confirm_cluster(
                    curation,
                    cluster.cluster_id,
                    reviewer_reference="fixture.cluster_reviewer",
                    reviewed_at=NOW,
                    decision_reason="Fixture reviewer confirmed this semantic Cluster.",
                    sources=sources,
                    catalog=production_role_catalog(),
                    assignments=assignments,
                    capture_contents=contents,
                )
    return curation


def contexts(company_count: int, *, tier_a_count: int | None = None, support_count: int | None = None):
    if tier_a_count is None:
        tier_a_count = company_count
    sources_data = [
        source_data(index, tier="tier_a_official" if index <= tier_a_count else "tier_b_verified_platform")
        for index in range(1, company_count + 1)
    ]
    curation_v3 = reviewed_fixture_v3(
        CurationArtifact.from_dict(
            curation_data(sources_data, support_count=support_count)
        )
    )
    sources, curation = upgrade_for_builder(sources_data, curation_v3)
    samples = tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in sources_data)
    return sources_data, sources, curation, samples


def curation_v3_with_split_leaf(curation: CurationArtifactV4) -> CurationArtifactV4:
    data = curation.to_dict()
    parent = data["candidates"][0]
    parent.update(
        status="superseded",
        cluster_id=None,
        reviewer_decision="pending",
        decision_reason=None,
        reviewed_at=None,
    )
    successor_ids = []
    for name in ("Python fundamentals", "Python software development"):
        child = deepcopy(parent)
        child["proposed_name"] = name
        child["proposed_description"] = f"Reviewed fixture requirement for {name}."
        child["candidate_id"] = generate_candidate_v4_id(
            child["source_id"], child["capture_id"], EvidenceLocator.from_dict(child["evidence"]), name
        )
        child.update(
            status="approved",
            cluster_id="cluster_python",
            reviewer_decision="approve",
            decision_reason="Fixture reviewer split a compound capability.",
            reviewed_at=NOW,
        )
        successor_ids.append(child["candidate_id"])
        data["candidates"].append(child)
    data["clusters"][0]["candidate_ids"] = [
        *data["clusters"][0]["candidate_ids"][1:],
        *successor_ids,
    ]
    reviews = [
        CandidateReviewRecord.create(
            parent_candidate_id=parent["candidate_id"],
            action=CandidateReviewAction.SPLIT,
            successor_candidate_ids=successor_ids,
            reviewer_reference="fixture_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture reviewer split a compound capability.",
        ).to_dict()
    ]
    for candidate in data["candidates"][1:-2]:
        reviews.append(
            CandidateReviewRecord.create(
                parent_candidate_id=candidate["candidate_id"],
                action=CandidateReviewAction.APPROVE,
                successor_candidate_ids=(),
                reviewer_reference="fixture_reviewer",
                reviewed_at=candidate["reviewed_at"],
                decision_reason=candidate["decision_reason"],
            ).to_dict()
        )
    data["review_records"] = reviews
    return CurationArtifactV4.from_dict(data)


def logic_group_builder_context(
    operator: RequirementLogicOperator = RequirementLogicOperator.ANY_OF,
):
    _, sources, curation, samples = contexts(6)
    split = curation_v3_with_split_leaf(curation)
    assignments, contents = confirmed_assignments(sources)
    v5 = migrate_curation_v4_to_v5(
        split,
        assignments=assignments,
        sources=sources,
        catalog=production_role_catalog(),
        capture_contents=contents,
    )
    v6 = migrate_curation_v5_to_v6(
        v5,
        assignments=assignments,
        sources=sources,
        catalog=production_role_catalog(),
        capture_contents=contents,
    )
    v6 = replace(
        v6,
        clusters=tuple(
            replace(
                item,
                status=ClusterLifecycleStatus.PROPOSED,
                reviewer_decision=ReviewerDecision.PENDING,
                decision_reason=None,
                reviewed_at=None,
            )
            for item in v6.clusters
        ),
    )
    members = v6.candidates[-2:]
    exact = normalize_jd_content(contents[members[0].capture_id])[:10]
    group = CandidateLogicGroup.create_proposed(
        operator=operator,
        member_candidate_references=tuple(item.candidate_id for item in members),
        source_reference=members[0].source_id,
        capture_reference=members[0].capture_id,
        source_content_hash=members[0].source_content_hash,
        role_assignment_reference=members[0].role_assignment_reference,
        mapped_role_id=members[0].mapped_role_id,
        mapped_specialization_id=members[0].mapped_specialization_id,
        evidence=EvidenceLocator(
            members[0].source_content_hash,
            "Qualifications",
            0,
            10,
            exact,
        ),
        modality=RequirementModality.REQUIRED,
    )
    proposed_v6 = replace(v6, logic_groups=(group,))
    proposed_v6.validate(sources, production_role_catalog(), assignments, contents)
    proposed = migrate_curation_v6_to_v7(
        proposed_v6, sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    candidate_map = {item.candidate_id: item for item in proposed.candidates}
    for cluster in tuple(proposed.clusters):
        if all(candidate_map[item].status == CandidateLifecycleStatus.APPROVED for item in cluster.candidate_ids):
            proposed = confirm_cluster(
                proposed, cluster.cluster_id,
                reviewer_reference="fixture.cluster_reviewer", reviewed_at=NOW,
                decision_reason="Fixture reviewer confirmed this semantic Cluster.",
                sources=sources, catalog=production_role_catalog(),
                assignments=assignments, capture_contents=contents,
            )
    return sources, samples, assignments, contents, proposed, proposed.logic_groups[0]


def build_logic_fixture(curation, sources, samples, assignments, contents):
    return build_catalog_draft(
        draft_id="fixture_logic_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=curation,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )


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
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.INSUFFICIENT_COMPANIES in {item.code for item in result.blockers}

    _, sources, curation, samples = contexts(10, tier_a_count=5)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.INSUFFICIENT_TIER_A_SHARE in {item.code for item in result.blockers}


def test_builder_blocks_empty_and_ineligible_samples() -> None:
    data, sources, curation, _ = contexts(1)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=(), sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.NO_SAMPLES in {item.code for item in result.blockers}

    career_page = source_data(1, source_type="official_career_page")
    career_curation_v3 = reviewed_fixture_v3(
        CurationArtifact.from_dict(curation_data([career_page]))
    )
    career_sources, career_curation = upgrade_for_builder(
        [career_page], career_curation_v3
    )
    result = build_fixture(
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
    rebuilt_curation_v3 = reviewed_fixture_v3(
        CurationArtifact.from_dict(curation_data(rebuilt_data))
    )
    rebuilt_sources, rebuilt_curation = upgrade_for_builder(
        rebuilt_data, rebuilt_curation_v3
    )
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in rebuilt_data),
        sources=rebuilt_sources, curation=rebuilt_curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.DUPLICATE_COMPANY_SAMPLE in {item.code for item in result.blockers}


def test_approved_only_builder_produces_deterministic_draft_and_prevalence() -> None:
    _, sources, curation, samples = contexts(10, tier_a_count=6, support_count=7)
    result = build_fixture(
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


def test_schema_v2_approved_candidate_is_blocked_from_catalog_building() -> None:
    data = [source_data(index) for index in range(1, 7)]
    sources = JDSourceCollection.from_dict(source_collection_data(data))
    legacy = CurationArtifact.from_dict(curation_data(data))
    samples = tuple(
        RoleSample(item["source_id"], "applied_ai_engineer") for item in data
    )

    first = build_fixture(
        draft_id="fixture_legacy_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=legacy,
        catalog=production_role_catalog(),
    )
    second = build_fixture(
        draft_id="fixture_legacy_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=legacy,
        catalog=production_role_catalog(),
    )

    assert first.draft is None
    assert first.blockers == second.blockers
    assert [item.to_dict() for item in first.blockers] == [
        {
            "code": "curation_schema_upgrade_required",
                "message": "Catalog building requires Curation schema 7 workflow provenance",
            "references": ["fixture_curation", "schema_version=2"],
        }
    ]


@pytest.mark.parametrize("status", ["review_pending", "rejected"])
def test_schema_v2_non_approved_states_cannot_produce_requirements(status) -> None:
    data = [source_data(index) for index in range(1, 7)]
    legacy_sources = JDSourceCollection.from_dict(source_collection_data(data))
    sources = migrate_source_v2_to_v3(
        legacy_sources,
        content_length_by_source_id={item["source_id"]: len(normalize_jd_content("Build reliable machine learning applications.\nUse Python.\n")) for item in data},
    )
    raw = curation_data(data)
    for candidate in raw["candidates"]:
        candidate["status"] = status
        candidate["reviewer_decision"] = "reject" if status == "rejected" else "pending"
        if status == "review_pending":
            candidate["decision_reason"] = None
            candidate["reviewed_at"] = None
    legacy = CurationArtifact.from_dict(raw)

    result = build_fixture(
        draft_id="fixture_legacy_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=tuple(
            RoleSample(item["source_id"], "applied_ai_engineer") for item in data
        ),
        sources=sources,
        curation=legacy,
        catalog=production_role_catalog(),
    )

    assert result.draft is None
    assert result.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED


def test_pending_v2_requires_explicit_migration_and_review_before_building() -> None:
    data = [source_data(index) for index in range(1, 7)]
    legacy_sources = JDSourceCollection.from_dict(source_collection_data(data))
    raw = curation_data(data)
    for candidate in raw["candidates"]:
        candidate.update(
            status="review_pending",
            reviewer_decision="pending",
            decision_reason=None,
            reviewed_at=None,
        )
    raw["clusters"][0].update(
        status="proposed",
        reviewer_decision="pending",
        decision_reason=None,
        reviewed_at=None,
    )
    v3 = migrate_v2_to_v3(CurationArtifact.from_dict(raw))
    for candidate in tuple(v3.candidates):
        v3 = approve_candidate(
            v3,
            candidate.candidate_id,
            reviewer_reference="fixture_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture reviewer approved this evidence.",
            sources=legacy_sources,
            catalog=production_role_catalog(),
        )
    reviewed = v3.to_dict()
    reviewed["clusters"][0].update(
        status="confirmed",
        reviewer_decision="approve",
        decision_reason="Fixture reviewer confirmed this cluster.",
        reviewed_at=NOW,
    )
    v3 = CurationArtifactV3.from_dict(reviewed)
    sources = migrate_source_v2_to_v3(
        legacy_sources,
        scope_by_source_id={item["source_id"]: "full_job_description" for item in data},
        content_length_by_source_id={item["source_id"]: len(normalize_jd_content("Build reliable machine learning applications.\nUse Python.\n")) for item in data},
    )
    v4 = migrate_v3_to_v4(
        v3, sources=sources, catalog=production_role_catalog()
    )

    result = build_fixture(
        draft_id="fixture_reviewed_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=tuple(
            RoleSample(item["source_id"], "applied_ai_engineer") for item in data
        ),
        sources=sources,
        curation=v4,
        catalog=production_role_catalog(),
    )

    assert result.blockers == ()
    assert result.draft is not None


def test_v3_approved_candidate_without_review_record_cannot_build() -> None:
    _, sources, curation, samples = contexts(6)
    tampered = curation.to_dict()
    tampered["review_records"] = []

    with pytest.raises(Phase2ValidationError, match="terminal provenance"):
        build_fixture(
            draft_id="fixture_tampered_draft",
            target_catalog_version="1.1.0",
            created_at=NOW,
            samples=samples,
            sources=sources,
            curation=CurationArtifactV4.from_dict(tampered),
            catalog=production_role_catalog(),
        )


def test_v3_builder_consumes_approved_leaves_without_inflating_company_count() -> None:
    _, sources, curation, samples = contexts(6)
    v3 = curation_v3_with_split_leaf(curation)
    v3.validate(sources, production_role_catalog())

    result = build_fixture(
        draft_id="fixture_v3_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=v3,
        catalog=production_role_catalog(),
    )

    assert result.blockers == ()
    assert result.draft is not None
    requirement = result.draft.requirements[0]
    assert len(requirement.candidate_references) == 7
    assert curation.candidates[0].candidate_id not in requirement.candidate_references
    assert requirement.sampled_company_count == 6
    assert requirement.supporting_company_count == 6


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
    curation_v3 = reviewed_fixture_v3(CurationArtifact.from_dict(curation_data(data)))
    sources, curation = upgrade_for_builder(all_sources, curation_v3)

    result = build_fixture(
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
    curation_v3 = reviewed_fixture_v3(CurationArtifact.from_dict(curation_raw))
    _, curation = upgrade_for_builder(data, curation_v3)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.UNCONFIRMED_CLUSTER in {item.code for item in result.blockers}


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
    legacy_sources = JDSourceCollection.from_dict(source_collection_data(all_sources_data))
    candidate = approved_candidate_data(discovery)
    curation_raw = curation_data([])
    curation_raw["candidates"] = [candidate]
    curation_raw["clusters"] = [cluster_data([candidate["candidate_id"]])]
    curation_v3 = reviewed_fixture_v3(CurationArtifact.from_dict(curation_raw))
    sources, curation = upgrade_for_builder(all_sources_data, curation_v3)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert BuildBlockerCode.TIER_C_ONLY_EVIDENCE in {item.code for item in result.blockers}


def test_candidate_outside_selected_sample_is_a_structured_blocker() -> None:
    data, sources, curation, _ = contexts(7)
    samples = tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in data[:6])
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.CANDIDATE_OUTSIDE_SAMPLE in {item.code for item in result.blockers}


def test_published_catalog_draft_requires_human_publication_approval() -> None:
    _, sources, curation, samples = contexts(6)
    result = build_fixture(
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
    assignments, contents = confirmed_assignments(sources)
    curation_v7 = upgrade_curation_to_v7(
        curation, sources, assignments, contents
    )
    CatalogDraft.from_dict(published).validate(
        sources, curation_v7, production_role_catalog(), assignments, contents
    )


def test_catalog_draft_requires_newer_semantic_version() -> None:
    _, sources, curation, samples = contexts(6)
    result = build_fixture(
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
    data, sources, curation, samples = contexts(6)
    empty_curation_data = curation_data(data)
    empty_curation_data["candidates"] = []
    empty_curation_data["clusters"] = []
    empty_curation_v3 = reviewed_fixture_v3(
        CurationArtifact.from_dict(empty_curation_data)
    )
    _, empty_curation = upgrade_for_builder(data, empty_curation_v3)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=empty_curation,
        catalog=production_role_catalog(),
    )
    assert result.draft is None
    assert BuildBlockerCode.NO_APPROVED_EVIDENCE in {item.code for item in result.blockers}

    valid = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources,
        curation=curation,
        catalog=production_role_catalog(),
    )
    assert valid.draft is not None
    bypass = valid.draft.to_dict()
    bypass["requirements"] = []
    with pytest.raises(Phase2ValidationError, match="approved requirement"):
        CatalogDraft.from_dict(bypass)


def test_catalog_draft_typed_storage_round_trip_and_context(tmp_path, monkeypatch) -> None:
    data, sources, curation, samples = contexts(6)
    result = build_fixture(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=curation, catalog=production_role_catalog(),
    )
    assert result.draft is not None
    assignments, contents = confirmed_assignments(sources)
    curation_v7 = upgrade_curation_to_v7(
        curation, sources, assignments, contents
    )
    path = save_catalog_draft(
        result.draft, tmp_path / "draft.json", sources=sources,
        curation=curation_v7, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    duplicate = save_catalog_draft(
        result.draft, tmp_path / "copy.json", sources=sources,
        curation=curation_v7, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    assert load_catalog_draft(
        path, sources=sources, curation=curation_v7, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    ) == result.draft
    assert path.read_bytes() == duplicate.read_bytes()

    legacy = CurationArtifact.from_dict(curation_data(data))
    with pytest.raises(Phase2ValidationError, match="Curation schema 7"):
        load_catalog_draft(
            path,
            sources=sources,
            curation=legacy,
            catalog=production_role_catalog(),
            assignments=assignments,
            capture_contents=contents,
        )
    with pytest.raises((Phase2ValidationError, TypeError), match="Curation schema 7|CurationArtifactV7"):
        save_catalog_draft(
            result.draft,
            tmp_path / "legacy-bypass.json",
            sources=sources,
            curation=legacy,
            catalog=production_role_catalog(),
            assignments=assignments,
            capture_contents=contents,
        )

    tampered = result.draft.to_dict()
    tampered["requirements"][0]["supporting_company_count"] = 0
    with pytest.raises(Phase2ValidationError, match="prevalence|counts do not match evidence"):
        CatalogDraft.from_dict(tampered).validate(sources, curation_v7, production_role_catalog(), assignments, contents)

    original = path.read_bytes()
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_catalog_draft(
            result.draft, path, sources=sources,
            curation=curation_v7, catalog=production_role_catalog(),
            assignments=assignments, capture_contents=contents,
        )
    assert path.read_bytes() == original


@pytest.mark.parametrize("operator", list(RequirementLogicOperator))
def test_builder_never_flattens_candidate_logic_groups(operator) -> None:
    sources, samples, assignments, contents, proposed, group = (
        logic_group_builder_context(operator)
    )
    blocked = build_logic_fixture(
        proposed, sources, samples, assignments, contents
    )
    assert blocked.draft is None
    assert [item.code for item in blocked.blockers].count(
        BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP
    ) == 1

    confirmed = confirm_candidate_logic_group_v7(
        proposed, group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer", reviewed_at=NOW,
        decision_reason="Fixture reviewer confirmed the alternatives.",
        sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    blocked = build_logic_fixture(
        confirmed, sources, samples, assignments, contents
    )
    assert blocked.draft is None
    logic_blockers = [
        item for item in blocked.blockers
        if item.code == BuildBlockerCode.PRODUCTION_REQUIREMENT_LOGIC_CONTRACT_REQUIRED
    ]
    assert [item.to_dict() for item in logic_blockers] == [{
        "code": "production_requirement_logic_contract_required",
        "message": "Catalog schema 1 cannot preserve a confirmed Candidate Logic Group",
        "references": [group.logic_group_id],
    }]

    rejected = quarantine_candidate_logic_group(
        proposed, group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer", reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected the proposed relationship.",
        sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    blocked = build_logic_fixture(
        rejected, sources, samples, assignments, contents
    )
    assert blocked.draft is None
    resolution_blockers = [
        item
        for item in blocked.blockers
        if item.code
        == BuildBlockerCode.QUARANTINED_LOGIC_GROUP
    ]
    assert [item.to_dict() for item in resolution_blockers] == [
        {
            "code": "quarantined_logic_group",
            "message": (
                "Quarantined Candidate Logic Group requires an explicit final resolution"
            ),
            "references": [group.logic_group_id],
        }
    ]


def test_proposed_cluster_does_not_obscure_logic_group_blocker() -> None:
    sources, samples, assignments, contents, proposed, group = (
        logic_group_builder_context()
    )
    cluster = proposed.clusters[0]
    proposed_cluster = replace(
        cluster,
        status=ClusterLifecycleStatus.PROPOSED,
        reviewer_decision=ReviewerDecision.PENDING,
        decision_reason=None,
        reviewed_at=None,
    )
    curation = replace(
        proposed, clusters=(proposed_cluster,), cluster_review_records=()
    )
    curation.validate(sources, production_role_catalog(), assignments, contents)

    result = build_logic_fixture(
        curation, sources, samples, assignments, contents
    )
    codes = [item.code for item in result.blockers]
    assert result.draft is None
    assert codes.count(BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP) == 1
    assert codes.count(BuildBlockerCode.UNCONFIRMED_CLUSTER) == 1
    assert next(
        item for item in result.blockers
        if item.code == BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP
    ).references == (group.logic_group_id,)

    rejected = quarantine_candidate_logic_group(
        curation,
        group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected the proposed relationship.",
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    result = build_logic_fixture(
        rejected, sources, samples, assignments, contents
    )
    codes = [item.code for item in result.blockers]
    assert result.draft is None
    assert (
        BuildBlockerCode.QUARANTINED_LOGIC_GROUP
        in codes
    )
    assert BuildBlockerCode.UNCONFIRMED_CLUSTER in codes


def test_quarantined_logic_group_members_cannot_build_independently() -> None:
    sources, samples, assignments, contents, proposed, group = (
        logic_group_builder_context()
    )
    quarantined = quarantine_candidate_logic_group(
        proposed,
        group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected the proposed relationship.",
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    result = build_logic_fixture(
        quarantined, sources, samples, assignments, contents
    )
    assert result.draft is None
    assert BuildBlockerCode.QUARANTINED_LOGIC_GROUP in {
        item.code for item in result.blockers
    }


def test_logic_group_blockers_are_complete_stable_and_not_per_member() -> None:
    sources, samples, assignments, contents, proposed, first = (
        logic_group_builder_context(RequirementLogicOperator.ANY_OF)
    )
    result = build_logic_fixture(
        proposed, sources, samples, assignments, contents
    )
    logic_blockers = [
        item for item in result.blockers
        if item.code == BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP
    ]
    assert result.draft is None
    assert [item.references for item in logic_blockers] == [
        (first.logic_group_id,)
    ]
    repeated = build_logic_fixture(
        proposed, sources, samples, assignments, contents
    )
    assert [item.to_dict() for item in result.blockers] == [
        item.to_dict() for item in repeated.blockers
    ]

    confirmed = confirm_candidate_logic_group_v7(
        proposed,
        first.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer confirmed the first relationship.",
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    confirmed_result = build_logic_fixture(
        confirmed, sources, samples, assignments, contents
    )
    assert BuildBlockerCode.PRODUCTION_REQUIREMENT_LOGIC_CONTRACT_REQUIRED in {
        item.code for item in confirmed_result.blockers
    }

    quarantined = quarantine_candidate_logic_group(
        proposed,
        first.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected the second relationship.",
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    quarantined_result = build_logic_fixture(
        quarantined, sources, samples, assignments, contents
    )
    assert quarantined_result.draft is None
    assert BuildBlockerCode.QUARANTINED_LOGIC_GROUP in {
        item.code for item in quarantined_result.blockers
    }


def test_candidate_extracted_members_and_proposed_cluster_report_both_blockers() -> None:
    source, sources, contents, _, assignments, _, v5 = role_assignment_context()
    base = migrate_curation_v5_to_v6(
        v5,
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    first = base.candidates[0]
    first = replace(first, status=CandidateLifecycleStatus.CANDIDATE_EXTRACTED)
    second_name = "Second extracted fixture capability"
    second = replace(
        first,
        candidate_id=generate_candidate_v4_id(
            first.source_id, first.capture_id, first.evidence, second_name
        ),
        proposed_name=second_name,
        proposed_description="Second extracted fixture capability.",
        status=CandidateLifecycleStatus.CANDIDATE_EXTRACTED,
        reviewer_decision=ReviewerDecision.PENDING,
        decision_reason=None,
        reviewed_at=None,
    )
    cluster = replace(
        base.clusters[0],
        candidate_ids=tuple(
            sorted((*base.clusters[0].candidate_ids, second.candidate_id))
        ),
        status=ClusterLifecycleStatus.PROPOSED,
        reviewer_decision=ReviewerDecision.PENDING,
        decision_reason=None,
        reviewed_at=None,
    )
    group = CandidateLogicGroup.create_proposed(
        operator=RequirementLogicOperator.ANY_OF,
        member_candidate_references=(first.candidate_id, second.candidate_id),
        source_reference=first.source_id,
        capture_reference=first.capture_id,
        source_content_hash=first.source_content_hash,
        role_assignment_reference=first.role_assignment_reference,
        mapped_role_id=first.mapped_role_id,
        mapped_specialization_id=first.mapped_specialization_id,
        evidence=EvidenceLocator(
            first.source_content_hash,
            "Qualifications",
            first.evidence.start_offset,
            first.evidence.end_offset,
            normalize_jd_content(contents[first.capture_id])[
                first.evidence.start_offset:first.evidence.end_offset
            ],
        ),
        modality=RequirementModality.REQUIRED,
    )
    curation = replace(
        base,
        candidates=(first, second),
        clusters=(cluster,),
        logic_groups=(group,),
    )
    curation.validate(sources, production_role_catalog(), assignments, contents)
    curation = migrate_curation_v6_to_v7(
        curation,
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    result = build_catalog_draft(
        draft_id="fixture_logic_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=(RoleSample(source["source_id"], "applied_ai_engineer"),),
        sources=sources,
        curation=curation,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    codes = [item.code for item in result.blockers]
    assert result.draft is None
    assert BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP in codes
    assert BuildBlockerCode.UNCONFIRMED_CLUSTER in codes


def test_catalog_draft_storage_cannot_bypass_quarantined_logic_group(
    tmp_path,
) -> None:
    _, sources, curation, samples = contexts(6)
    valid = build_fixture(
        draft_id="fixture_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=curation,
        catalog=production_role_catalog(),
    )
    assert valid.draft is not None
    assignments, contents = confirmed_assignments(sources)
    valid_v7 = upgrade_curation_to_v7(
        curation, sources, assignments, contents
    )
    path = save_catalog_draft(
        valid.draft,
        tmp_path / "draft.json",
        sources=sources,
        curation=valid_v7,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    original = path.read_bytes()

    group_sources, _, group_assignments, group_contents, proposed, group = (
        logic_group_builder_context()
    )
    quarantined = quarantine_candidate_logic_group(
        proposed,
        group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected the proposed relationship.",
        sources=group_sources,
        catalog=production_role_catalog(),
        assignments=group_assignments,
        capture_contents=group_contents,
    )
    with pytest.raises(Phase2ValidationError, match="explicit final resolution"):
        valid.draft.validate(
            group_sources,
            quarantined,
            production_role_catalog(),
            group_assignments,
            group_contents,
        )
    with pytest.raises(Phase2ValidationError, match="explicit final resolution"):
        save_catalog_draft(
            valid.draft,
            path,
            sources=group_sources,
            curation=quarantined,
            catalog=production_role_catalog(),
            assignments=group_assignments,
            capture_contents=group_contents,
        )
    assert path.read_bytes() == original
    with pytest.raises(Phase2ValidationError, match="explicit final resolution"):
        load_catalog_draft(
            path,
            sources=group_sources,
            curation=quarantined,
            catalog=production_role_catalog(),
            assignments=group_assignments,
            capture_contents=group_contents,
        )


def test_released_logic_group_members_can_build_as_confirmed_cluster() -> None:
    sources, samples, assignments, contents, proposed, group = (
        logic_group_builder_context()
    )
    released = release_logic_group_members(
        proposed,
        group.logic_group_id,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer released the atomic members.",
        sources=sources,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    result = build_logic_fixture(
        released, sources, samples, assignments, contents
    )
    assert result.blockers == ()
    assert result.draft is not None
    result.draft.validate(
        sources,
        released,
        production_role_catalog(),
        assignments,
        contents,
    )


def test_catalog_draft_validation_rejects_extra_unclustered_approval() -> None:
    _, sources, curation, samples = contexts(6)
    assignments, contents = confirmed_assignments(sources)
    curation_v7 = upgrade_curation_to_v7(
        curation, sources, assignments, contents
    )
    built = build_catalog_draft(
        draft_id="fixture_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=curation_v7,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )
    assert built.draft is not None
    parent = curation_v7.candidates[0]
    name = "Unclustered approved fixture capability"
    candidate_id = generate_candidate_v4_id(
        parent.source_id, parent.capture_id, parent.evidence, name
    )
    extra = replace(
        parent,
        candidate_id=candidate_id,
        proposed_name=name,
        proposed_description="Unclustered approved fixture capability.",
        cluster_id=None,
        reviewed_at=NOW,
        decision_reason="Fixture reviewer approved the capability.",
    )
    review = CandidateReviewRecord.create(
        parent_candidate_id=candidate_id,
        action=CandidateReviewAction.APPROVE,
        successor_candidate_ids=(),
        reviewer_reference="fixture.candidate_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer approved the capability.",
    )
    bypass = replace(
        curation_v7,
        candidates=(*curation_v7.candidates, extra),
        review_records=(*curation_v7.review_records, review),
    )
    bypass.validate(sources, production_role_catalog(), assignments, contents)
    with pytest.raises(Phase2ValidationError, match="not in a confirmed Cluster"):
        built.draft.validate(
            sources,
            bypass,
            production_role_catalog(),
            assignments,
            contents,
        )


def test_rejected_cluster_history_does_not_block_confirmed_current_cluster() -> None:
    _, sources, curation, samples = contexts(6)
    assignments, contents = confirmed_assignments(sources)
    curation_v7 = upgrade_curation_to_v7(
        curation, sources, assignments, contents
    )
    current = curation_v7.clusters[0]
    historical_name = "Rejected historical fixture capability"
    historical = replace(
        current,
        cluster_id=generate_cluster_v5_id(
            normalized_name=historical_name,
            role_id=current.role_id,
            specialization_id=current.specialization_id,
            category=current.category,
            role_assignment_references=current.role_assignment_references,
        ),
        normalized_name=historical_name,
        status=ClusterLifecycleStatus.REJECTED,
        reviewer_decision=ReviewerDecision.REJECT,
        decision_reason="Fixture reviewer rejected the historical Cluster.",
        reviewed_at=NOW,
    )
    historical_review = ClusterReviewRecord.create(
        cluster=historical,
        action=ClusterReviewAction.REJECT,
        reviewer_reference="fixture.cluster_reviewer",
        reviewed_at=NOW,
        decision_reason=historical.decision_reason,
    )
    with_history = replace(
        curation_v7,
        clusters=(*curation_v7.clusters, historical),
        cluster_review_records=(
            *curation_v7.cluster_review_records,
            historical_review,
        ),
    )
    with_history.validate(
        sources, production_role_catalog(), assignments, contents
    )

    result = build_catalog_draft(
        draft_id="fixture_draft_with_rejected_history",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=samples,
        sources=sources,
        curation=with_history,
        catalog=production_role_catalog(),
        assignments=assignments,
        capture_contents=contents,
    )

    assert result.blockers == ()
    assert result.draft is not None
    assert all(
        item.cluster_id != historical.cluster_id
        for item in result.draft.requirements
    )
