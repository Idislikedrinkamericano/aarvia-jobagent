from copy import deepcopy
from dataclasses import replace

import pytest

from aarvia.catalog_build import BuildBlockerCode, RoleSample, build_catalog_draft
from aarvia.jd_curation import (
    CandidateRevision,
    CurationArtifact,
    CurationArtifactV3,
    CurationArtifactV4,
    CurationArtifactV5,
    RequirementCandidateV4,
    approve_candidate,
    load_curation_artifact,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_curation_v4_to_v5,
    migrate_curation_v5_to_v6,
    generate_candidate_v4_id,
    revise_candidate,
    save_curation_artifact,
    split_candidate,
)
from aarvia.jd_sources import (
    CaptureScope,
    JDSourceCapture,
    JDSourceCollection,
    JDSourceCollectionV3,
    content_sha256,
    generate_capture_id,
    load_jd_source_collection,
    migrate_source_v2_to_v3,
    save_jd_source_collection,
    normalize_jd_content,
)
from aarvia.live_jobs import (
    LiveJobCollection,
    LiveJobCollectionV2,
    LiveJobCollectionV3,
    load_live_job_collection,
    migrate_live_jobs_v2_to_v3,
    save_live_job_collection,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from phase2b_fixtures import (
    FAKE_HASH,
    FAKE_JD,
    NOW,
    confirmed_assignments,
    curation_data,
    source_collection_data,
    source_data,
)


LATER = "2026-09-02T12:00:00+00:00"


def source_v3_fixture(
    count: int = 1, *, scope: str = "full_job_description"
) -> tuple[list[dict], JDSourceCollection, JDSourceCollectionV3]:
    data = [source_data(index) for index in range(1, count + 1)]
    legacy = JDSourceCollection.from_dict(source_collection_data(data))
    migrated = migrate_source_v2_to_v3(
        legacy,
        scope_by_source_id={item["source_id"]: scope for item in data},
        content_length_by_source_id={item["source_id"]: len(normalize_jd_content(FAKE_JD)) for item in data},
    )
    return data, legacy, migrated


def pending_v3(data: list[dict]) -> CurationArtifactV3:
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
    return migrate_v2_to_v3(CurationArtifact.from_dict(raw))


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


def second_capture(sources: JDSourceCollectionV3, *, same_hash: bool = False) -> JDSourceCapture:
    first = sources.captures[0]
    digest = first.content_hash if same_hash else content_sha256("A revised fixture job description.\n")
    return JDSourceCapture.from_dict({
        "capture_id": generate_capture_id(
            source_reference=first.source_reference,
            captured_at=LATER,
            content_hash=digest,
            hash_normalization_version="jd-text-v1",
        ),
        "source_reference": first.source_reference,
        "captured_at": LATER,
        "verified_at": LATER,
        "content_hash": digest,
        "content_length": 1000,
        "hash_normalization_version": "jd-text-v1",
        "verification_method": "official_ats_direct",
        "capture_scope": "full_job_description",
        "previous_capture_reference": first.capture_id,
    })


def test_multiple_immutable_captures_and_old_candidate_remain_valid(tmp_path) -> None:
    data, _, sources = source_v3_fixture()
    curation = migrate_v3_to_v4(
        pending_v3(data), sources=sources, catalog=production_role_catalog()
    )
    expanded = replace(sources, captures=(*sources.captures, second_capture(sources)))
    expanded.validate()
    curation.validate(expanded, production_role_catalog())

    path = save_jd_source_collection(sources, tmp_path / "sources.json")
    save_jd_source_collection(expanded, path)
    assert load_jd_source_collection(path) == expanded


def test_source_v3_atomic_replacement_failure_preserves_old_file(tmp_path, monkeypatch) -> None:
    _, _, sources = source_v3_fixture()
    path = save_jd_source_collection(sources, tmp_path / "sources.json")
    original = path.read_bytes()
    expanded = replace(sources, captures=(*sources.captures, second_capture(sources)))
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_jd_source_collection(expanded, path)
    assert path.read_bytes() == original


def test_capture_overwrite_removal_duplicate_and_id_tampering_are_rejected(tmp_path) -> None:
    _, _, sources = source_v3_fixture()
    path = save_jd_source_collection(sources, tmp_path / "sources.json")
    capture = sources.captures[0]
    with pytest.raises(Phase2ValidationError, match="immutable capture"):
        save_jd_source_collection(replace(sources, captures=()), path)
    changed = replace(capture, capture_scope=CaptureScope.STATUS_ONLY)
    with pytest.raises(Phase2ValidationError, match="immutable capture"):
        save_jd_source_collection(replace(sources, captures=(changed,)), path)
    with pytest.raises(Phase2ValidationError, match="duplicate capture"):
        replace(sources, captures=(capture, capture)).validate()
    with pytest.raises(Phase2ValidationError, match="generated by Aarvia"):
        JDSourceCapture.from_dict({**capture.to_dict(), "capture_id": "capture_tampered"})
    with pytest.raises(Phase2ValidationError, match="generated by Aarvia"):
        JDSourceCapture.from_dict({**capture.to_dict(), "content_hash": "sha256:" + "a" * 64})


@pytest.mark.parametrize("fault", ["cross_source", "missing", "time"])
def test_capture_previous_reference_integrity(fault) -> None:
    data, legacy, sources = source_v3_fixture(2)
    capture = second_capture(sources)
    if fault == "cross_source":
        capture = replace(capture, previous_capture_reference=sources.captures[1].capture_id)
        match = "crosses sources"
    elif fault == "missing":
        capture = replace(capture, previous_capture_reference="capture_missing")
        match = "is missing"
    else:
        capture = replace(
            capture,
            captured_at=NOW,
            capture_id=generate_capture_id(
                source_reference=capture.source_reference,
                captured_at=NOW,
                content_hash=capture.content_hash,
                hash_normalization_version=capture.hash_normalization_version,
            ),
        )
        match = "strictly increase"
    with pytest.raises(Phase2ValidationError, match=match):
        replace(sources, captures=(*sources.captures, capture)).validate()


def test_capture_cycle_is_rejected_even_when_objects_are_directly_constructed() -> None:
    _, _, sources = source_v3_fixture()
    first = sources.captures[0]
    second = second_capture(sources)
    first_cycle = replace(first, previous_capture_reference=second.capture_id)
    with pytest.raises(Phase2ValidationError, match="cycle"):
        replace(sources, captures=(first_cycle, second)).validate()


def test_capture_scope_controls_absence_evidence() -> None:
    for scope, expected in (
        ("legacy_unspecified", False),
        ("partial_excerpt", False),
        ("status_only", False),
        ("full_job_description", True),
    ):
        _, _, sources = source_v3_fixture(scope=scope)
        assert sources.capture_supports_not_stated(sources.captures[0].capture_id) is expected


@pytest.mark.parametrize("content_length", [None, 0])
def test_full_capture_requires_normalized_content_length(content_length) -> None:
    _, _, sources = source_v3_fixture()
    capture = sources.captures[0]
    with pytest.raises(Phase2ValidationError, match="requires normalized content_length"):
        JDSourceCapture.from_dict(
            {**capture.to_dict(), "content_length": content_length}
        )


def test_source_v2_migration_defaults_to_legacy_and_requires_explicit_full_scope() -> None:
    _, legacy, _ = source_v3_fixture()
    default = migrate_source_v2_to_v3(legacy)
    assert default.captures[0].capture_scope == CaptureScope.LEGACY_UNSPECIFIED
    explicit = migrate_source_v2_to_v3(
        legacy,
        scope_by_source_id={legacy.sources[0].source_id: "full_job_description"},
        content_length_by_source_id={legacy.sources[0].source_id: 1000},
    )
    assert explicit.captures[0].capture_scope == CaptureScope.FULL_JOB_DESCRIPTION


def test_curation_v3_migration_requires_one_capture_and_preserves_lineage() -> None:
    data, _, sources = source_v3_fixture()
    v3 = pending_v3(data)
    v4 = migrate_v3_to_v4(v3, sources=sources, catalog=production_role_catalog())
    assert len(v4.candidates) == len(v3.candidates)
    assert v4.candidates[0].capture_id == sources.captures[0].capture_id
    assert v4.candidates[0].proposed_name == v3.candidates[0].proposed_name

    ambiguous = replace(sources, captures=(*sources.captures, second_capture(sources, same_hash=True)))
    ambiguous.validate()
    with pytest.raises(Phase2ValidationError, match="exactly one matching capture"):
        migrate_v3_to_v4(v3, sources=ambiguous, catalog=production_role_catalog())
    no_match = replace(sources, captures=())
    with pytest.raises(Phase2ValidationError, match="status requires a capture"):
        no_match.validate()
    unrelated = replace(
        sources,
        captures=(replace(sources.captures[0], content_hash="sha256:" + "b" * 64,
                          capture_id=generate_capture_id(
                              source_reference=sources.captures[0].source_reference,
                              captured_at=sources.captures[0].captured_at,
                              content_hash="sha256:" + "b" * 64,
                              hash_normalization_version="jd-text-v1",
                          )),),
    )
    unrelated.validate()
    with pytest.raises(Phase2ValidationError, match="exactly one matching capture"):
        migrate_v3_to_v4(v3, sources=unrelated, catalog=production_role_catalog())


def test_curation_v3_migration_preserves_review_semantics() -> None:
    data, _, sources = source_v3_fixture()
    v3 = pending_v3(data)
    approved = approve_candidate(
        v3,
        v3.candidates[0].candidate_id,
        reviewer_reference="fixture_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture approval.",
        sources=JDSourceCollection.from_dict(source_collection_data(data)),
        catalog=production_role_catalog(),
    )
    v4 = migrate_v3_to_v4(
        approved, sources=sources, catalog=production_role_catalog()
    )
    assert v4.candidates[0].status.value == "approved"
    assert v4.review_records[0].action.value == "approve"
    assert v4.review_records[0].reviewer_reference == "fixture_reviewer"
    assert v4.review_records[0].reviewed_at == NOW
    assert v4.review_records[0].parent_candidate_id == v4.candidates[0].candidate_id


def test_candidate_capture_hash_locator_and_provider_boundary() -> None:
    data, _, sources = source_v3_fixture()
    v4 = migrate_v3_to_v4(
        pending_v3(data), sources=sources, catalog=production_role_catalog()
    )
    candidate = v4.candidates[0]
    _, _, other_sources = source_v3_fixture(2)
    other_capture = other_sources.captures[1]
    bad_source_data = candidate.to_dict()
    bad_source_data["capture_id"] = other_capture.capture_id
    bad_source_data["candidate_id"] = generate_candidate_v4_id(
        candidate.source_id,
        other_capture.capture_id,
        candidate.evidence,
        candidate.proposed_name,
    )
    bad_source_candidate = RequirementCandidateV4.from_dict(bad_source_data)
    with pytest.raises(Phase2ValidationError, match="belongs to another source"):
        bad_source_candidate.validate(other_sources, production_role_catalog())
    bad_hash = replace(candidate, source_content_hash="sha256:" + "a" * 64)
    with pytest.raises(Phase2ValidationError, match="content hash"):
        bad_hash.validate(sources, production_role_catalog())
    changed_evidence = replace(candidate.evidence, end_offset=1001)
    bad_locator = replace(
        candidate,
        evidence=changed_evidence,
        candidate_id=generate_candidate_v4_id(
            candidate.source_id,
            candidate.capture_id,
            changed_evidence,
            candidate.proposed_name,
        ),
    )
    with pytest.raises(Phase2ValidationError, match="outside capture"):
        bad_locator.validate(sources, production_role_catalog())
    missing_offsets = replace(
        candidate,
        evidence=replace(candidate.evidence, start_offset=None, end_offset=None),
    )
    missing_offsets = replace(
        missing_offsets,
        candidate_id=generate_candidate_v4_id(
            missing_offsets.source_id,
            missing_offsets.capture_id,
            missing_offsets.evidence,
            missing_offsets.proposed_name,
        ),
    )
    with pytest.raises(Phase2ValidationError, match="requires explicit offsets"):
        missing_offsets.validate(sources, production_role_catalog())
    payload = {
        "evidence": candidate.evidence.to_dict(),
        "proposed_name": "Python",
        "proposed_description": "Fixture capability.",
        "proposed_category": "technical_skill",
        "proposed_importance": "core",
        "mapped_role_id": "applied_ai_engineer",
        "mapped_specialization_id": "agentic_ai",
        "capture_id": candidate.capture_id,
    }
    with pytest.raises(Phase2ValidationError, match="unknown fields: capture_id"):
        RequirementCandidateV4.from_extraction(
            payload,
            source_id=candidate.source_id,
            capture_id=candidate.capture_id,
            source_content_hash=candidate.source_content_hash,
            extraction=candidate.extraction,
        )


def test_revise_and_split_successors_preserve_capture() -> None:
    data, _, sources = source_v3_fixture()
    v4 = migrate_v3_to_v4(
        pending_v3(data), sources=sources, catalog=production_role_catalog()
    )
    parent = v4.candidates[0]
    revision = CandidateRevision(
        "Python programming",
        "Fixture revised capability.",
        parent.proposed_category,
        parent.proposed_importance,
    )
    revised = revise_candidate(
        v4, parent.candidate_id, revision,
        reviewer_reference="fixture_reviewer", reviewed_at=NOW,
        decision_reason="Fixture revision.", sources=sources,
        catalog=production_role_catalog(),
    )
    assert isinstance(revised, CurationArtifactV4)
    assert revised.candidates[-1].capture_id == parent.capture_id

    data2, _, sources2 = source_v3_fixture()
    pending = migrate_v3_to_v4(
        pending_v3(data2), sources=sources2, catalog=production_role_catalog()
    )
    parent2 = pending.candidates[0]
    split = split_candidate(
        pending,
        parent2.candidate_id,
        (
            CandidateRevision("Python basics", "Fixture A.", parent2.proposed_category, parent2.proposed_importance),
            CandidateRevision("Python engineering", "Fixture B.", parent2.proposed_category, parent2.proposed_importance),
        ),
        reviewer_reference="fixture_reviewer", reviewed_at=NOW,
        decision_reason="Fixture split.", sources=sources2,
        catalog=production_role_catalog(),
    )
    assert {item.capture_id for item in split.candidates[-2:]} == {parent2.capture_id}


def test_live_job_v2_migration_and_v3_capture_validation(tmp_path) -> None:
    data, legacy_sources, sources = source_v3_fixture()
    legacy_jobs = LiveJobCollection.from_dict(live_v2_data(data[0]))
    assert isinstance(legacy_jobs, LiveJobCollectionV2)
    jobs = migrate_live_jobs_v2_to_v3(
        legacy_jobs,
        legacy_sources=legacy_sources,
        sources=sources,
        catalog=production_role_catalog(),
    )
    capture_id = sources.captures[0].capture_id
    assert jobs.jobs[0].listing_verification_capture_reference == capture_id
    assert jobs.jobs[0].application_verification_capture_reference == capture_id
    path = save_live_job_collection(
        jobs, tmp_path / "jobs.json", catalog=production_role_catalog(), sources=sources
    )
    assert load_live_job_collection(
        path, catalog=production_role_catalog(), sources=sources
    ) == jobs

    bad = replace(jobs.jobs[0], listing_verification_capture_reference="capture_missing")
    with pytest.raises(Phase2ValidationError, match="unknown JD capture"):
        bad.validate(sources, production_role_catalog())
    with pytest.raises(Phase2ValidationError, match="requires JD Source schema 3"):
        save_live_job_collection(jobs, tmp_path / "bad.json", catalog=production_role_catalog(), sources=legacy_sources)


def test_live_job_v2_migration_does_not_invent_unverified_application_evidence() -> None:
    data, legacy_sources, sources = source_v3_fixture()
    raw = live_v2_data(data[0])
    raw["jobs"][0].update(
        listing_status="possibly_open",
        application_url_status="provided_unverified",
        application_url_last_verified_at=None,
    )
    legacy_jobs = LiveJobCollection.from_dict(raw)
    legacy_jobs.validate(production_role_catalog(), legacy_sources)

    jobs = migrate_live_jobs_v2_to_v3(
        legacy_jobs,
        legacy_sources=legacy_sources,
        sources=sources,
        catalog=production_role_catalog(),
    )

    assert jobs.jobs[0].listing_verification_capture_reference == sources.captures[0].capture_id
    assert jobs.jobs[0].application_verification_capture_reference is None
    jobs.validate(production_role_catalog(), sources)


def test_live_job_v3_rejects_wrong_application_capture_and_preserves_file_on_failure(tmp_path, monkeypatch) -> None:
    data, legacy_sources, sources = source_v3_fixture(2)
    legacy_jobs = LiveJobCollection.from_dict(live_v2_data(data[0]))
    jobs = migrate_live_jobs_v2_to_v3(
        legacy_jobs, legacy_sources=legacy_sources, sources=sources,
        catalog=production_role_catalog(),
    )
    wrong = replace(
        jobs.jobs[0],
        application_verification_capture_reference=sources.captures[1].capture_id,
    )
    with pytest.raises(Phase2ValidationError, match="application verification capture is outside job provenance"):
        wrong.validate(sources, production_role_catalog())

    path = save_live_job_collection(
        jobs, tmp_path / "jobs.json", catalog=production_role_catalog(), sources=sources
    )
    original = path.read_bytes()
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_live_job_collection(
            jobs, path, catalog=production_role_catalog(), sources=sources
        )
    assert path.read_bytes() == original


def test_curation_v4_typed_round_trip_and_atomic_failure(tmp_path, monkeypatch) -> None:
    data, _, sources = source_v3_fixture()
    value = migrate_v3_to_v4(
        pending_v3(data), sources=sources, catalog=production_role_catalog()
    )
    path = save_curation_artifact(
        value, tmp_path / "curation.json", sources=sources,
        catalog=production_role_catalog(),
    )
    assert load_curation_artifact(
        path, sources=sources, catalog=production_role_catalog()
    ) == value
    original = path.read_bytes()
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        save_curation_artifact(
            value, path, sources=sources, catalog=production_role_catalog()
        )
    assert path.read_bytes() == original


def test_builder_requires_schema_v6_and_confirmed_role_assignments() -> None:
    data, legacy_sources, sources = source_v3_fixture(6)
    legacy = CurationArtifact.from_dict(curation_data(data))
    # Schema 2 terminal state cannot be migrated automatically, so construct its
    # existing trusted schema 3 fixture records through the established reader.
    raw = legacy.to_dict()
    raw["schema_version"] = 3
    from aarvia.jd_curation import CandidateReviewAction, CandidateReviewRecord
    raw["review_records"] = [
        CandidateReviewRecord.create(
            parent_candidate_id=item.candidate_id,
            action=CandidateReviewAction.APPROVE,
            successor_candidate_ids=(),
            reviewer_reference="fixture_reviewer",
            reviewed_at=NOW,
            decision_reason=item.decision_reason,
        ).to_dict()
        for item in legacy.candidates
    ]
    v3 = CurationArtifactV3.from_dict(raw)
    samples = tuple(RoleSample(item["source_id"], "applied_ai_engineer") for item in data)
    blocked = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=v3,
        catalog=production_role_catalog(),
    )
    assert blocked.draft is None
    assert blocked.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED

    v4 = migrate_v3_to_v4(v3, sources=sources, catalog=production_role_catalog())
    still_blocked = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=v4,
        catalog=production_role_catalog(),
    )
    assert still_blocked.draft is None
    assert still_blocked.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED

    assignments, contents = confirmed_assignments(sources)
    v5 = migrate_curation_v4_to_v5(
        v4, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    blocked_v5 = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=v5,
        catalog=production_role_catalog(), assignments=assignments,
        capture_contents=contents,
    )
    assert blocked_v5.draft is None
    assert blocked_v5.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED
    v6 = migrate_curation_v5_to_v6(
        v5, sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    built = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=sources, curation=v6,
        catalog=production_role_catalog(), assignments=assignments,
        capture_contents=contents,
    )
    assert built.draft is None
    assert built.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED

    expanded_sources = replace(
        sources,
        captures=(*sources.captures, second_capture(sources)),
    )
    expanded_sources.validate()
    expanded_contents = dict(contents)
    rebuilt = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=expanded_sources, curation=v6,
        catalog=production_role_catalog(), assignments=assignments,
        capture_contents=expanded_contents,
    )
    assert rebuilt.draft is None
    assert rebuilt.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED


def test_schema_readers_do_not_silently_mix_versions(tmp_path) -> None:
    data, legacy_sources, sources = source_v3_fixture()
    with pytest.raises(Phase2ValidationError, match="unsupported JD Source"):
        JDSourceCollection.from_dict(sources.to_dict())
    with pytest.raises(Phase2ValidationError, match="unsupported JD Source"):
        JDSourceCollectionV3.from_dict(legacy_sources.to_dict())

    jobs_v2 = LiveJobCollection.from_dict(live_v2_data(data[0]))
    jobs_v3 = migrate_live_jobs_v2_to_v3(
        jobs_v2, legacy_sources=legacy_sources, sources=sources,
        catalog=production_role_catalog(),
    )
    with pytest.raises(Phase2ValidationError, match="unsupported Live Job"):
        LiveJobCollectionV3.from_dict(jobs_v2.to_dict())
    mixed = jobs_v3.to_dict()
    mixed["schema_version"] = 2
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        LiveJobCollectionV2.from_dict(mixed)

    curation_v3 = pending_v3(data)
    curation_v4 = migrate_v3_to_v4(
        curation_v3, sources=sources, catalog=production_role_catalog()
    )
    with pytest.raises(Phase2ValidationError, match="unsupported Curation"):
        CurationArtifactV3.from_dict(curation_v4.to_dict())
    with pytest.raises(Phase2ValidationError, match="unsupported Curation"):
        CurationArtifactV4.from_dict(curation_v3.to_dict())


def test_curation_v4_typed_load_rejects_tampered_capture(tmp_path) -> None:
    data, _, sources = source_v3_fixture()
    value = migrate_v3_to_v4(
        pending_v3(data), sources=sources, catalog=production_role_catalog()
    )
    path = save_curation_artifact(
        value, tmp_path / "curation.json", sources=sources,
        catalog=production_role_catalog(),
    )
    raw = path.read_text(encoding="utf-8").replace(
        value.candidates[0].capture_id, "capture_missing"
    )
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="generated by Aarvia|unknown JD capture"):
        load_curation_artifact(
            path, sources=sources, catalog=production_role_catalog()
        )
