from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from aarvia.jd_curation import (
    CandidateLifecycleStatus,
    CandidateReviewAction,
    CandidateReviewRecord,
    CandidateRevision,
    CurationArtifact,
    CurationArtifactV3,
    EvidenceLocator,
    RequirementCandidate,
    ReviewerDecision,
    approve_candidate,
    generate_candidate_id,
    load_curation_artifact,
    migrate_v2_to_v3,
    reject_candidate,
    revise_candidate,
    save_curation_artifact,
    split_candidate,
)
from aarvia.jd_sources import JDSourceCollection
from aarvia.role_catalog import (
    Phase2ValidationError,
    RequirementCategory,
    RequirementImportance,
    production_role_catalog,
)
from phase2b_fixtures import (
    NOW,
    approved_candidate_data,
    cluster_data,
    extracted_candidate,
    source_collection_data,
    source_data,
)


REVIEWER = "fixture_reviewer"
REASON = "Fixture reviewer completed a deliberate candidate review."


def pending_v2_context(*, source_count: int = 1):
    source_rows = [source_data(index) for index in range(1, source_count + 1)]
    sources = JDSourceCollection.from_dict(source_collection_data(source_rows))
    candidates = []
    clusters = []
    for index, source in enumerate(source_rows, 1):
        candidate = extracted_candidate(source, name=f"Python {index}")
        cluster_id = f"cluster_python_{index}"
        candidate = replace(
            candidate,
            status=CandidateLifecycleStatus.REVIEW_PENDING,
            cluster_id=cluster_id,
        )
        candidates.append(candidate.to_dict())
        cluster = cluster_data([candidate.candidate_id], cluster_id=cluster_id)
        cluster.update(
            status="proposed",
            reviewer_decision="pending",
            decision_reason=None,
            reviewed_at=None,
        )
        clusters.append(cluster)
    artifact = CurationArtifact.from_dict(
        {
            "schema": "aarvia.jd_curation",
            "schema_version": 2,
            "artifact_id": "fixture_curation",
            "artifact_type": "test_fixture",
            "source_collection_id": "fixture_source_collection",
            "catalog_version": "1.0.0",
            "created_at": NOW,
            "candidates": candidates,
            "clusters": clusters,
        }
    )
    return source_rows, sources, artifact


def revision(name: str, *, evidence: EvidenceLocator | None = None) -> CandidateRevision:
    return CandidateRevision(
        proposed_name=name,
        proposed_description=f"Reviewed requirement for {name}.",
        proposed_category=RequirementCategory.TECHNICAL_SKILL,
        proposed_importance=RequirementImportance.CORE,
        evidence=evidence,
    )


def approve(artifact, candidate_id, sources):
    return approve_candidate(
        artifact,
        candidate_id,
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )


def test_pending_only_v2_migration_preserves_candidate_facts_and_v2_round_trip() -> None:
    _, sources, v2 = pending_v2_context()
    rebuilt_v2 = CurationArtifact.from_dict(v2.to_dict())
    assert rebuilt_v2 == v2
    assert rebuilt_v2.to_dict() == v2.to_dict()

    v3 = migrate_v2_to_v3(v2)
    v3.validate(sources, production_role_catalog())
    assert v3.schema_version == 3
    assert v3.review_records == ()
    assert [item.to_dict() for item in v3.candidates] == [
        item.to_dict() for item in v2.candidates
    ]


def test_v2_terminal_candidate_migration_requires_re_review() -> None:
    source = source_data()
    terminal = CurationArtifact.from_dict(
        {
            "schema": "aarvia.jd_curation",
            "schema_version": 2,
            "artifact_id": "fixture_curation",
            "artifact_type": "test_fixture",
            "source_collection_id": "fixture_source_collection",
            "catalog_version": "1.0.0",
            "created_at": NOW,
            "candidates": [approved_candidate_data(source)],
            "clusters": [cluster_data([approved_candidate_data(source)["candidate_id"]])],
        }
    )
    with pytest.raises(Phase2ValidationError, match="require re-review"):
        migrate_v2_to_v3(terminal)


def test_approve_and_reject_create_deterministic_terminal_reviews() -> None:
    _, sources, v2 = pending_v2_context(source_count=2)
    v3 = migrate_v2_to_v3(v2)
    approved = approve(v3, v3.candidates[0].candidate_id, sources)
    rejected = reject_candidate(
        approved,
        approved.candidates[1].candidate_id,
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    assert rejected.candidates[0].status == CandidateLifecycleStatus.APPROVED
    assert rejected.candidates[1].status == CandidateLifecycleStatus.REJECTED
    assert [item.action for item in rejected.review_records] == [
        CandidateReviewAction.APPROVE,
        CandidateReviewAction.REJECT,
    ]
    repeated = approve(migrate_v2_to_v3(v2), v3.candidates[0].candidate_id, sources)
    assert repeated.review_records[0].review_id == approved.review_records[0].review_id


def test_one_to_one_revise_creates_deterministic_approved_leaf() -> None:
    _, sources, v2 = pending_v2_context()
    v3 = migrate_v2_to_v3(v2)
    result = revise_candidate(
        v3,
        v3.candidates[0].candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    parent, child = result.candidates
    assert parent.status == CandidateLifecycleStatus.SUPERSEDED
    assert child.status == CandidateLifecycleStatus.APPROVED
    assert child.candidate_id == generate_candidate_id(
        parent.source_id, parent.evidence, "Python programming"
    )
    assert result.review_records[0].action == CandidateReviewAction.REVISE
    assert result.review_records[0].successor_candidate_ids == (child.candidate_id,)


def test_approved_successor_can_be_revised_into_a_valid_lineage_chain() -> None:
    _, sources, v2 = pending_v2_context()
    first = revise_candidate(
        migrate_v2_to_v3(v2),
        v2.candidates[0].candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    first_successor_id = first.review_records[0].successor_candidate_ids[0]
    second = revise_candidate(
        first,
        first_successor_id,
        revision("Python software development"),
        reviewer_reference="fixture_second_reviewer",
        reviewed_at="2026-09-02T12:00:00+00:00",
        decision_reason="The first revision needed a narrower capability name.",
        sources=sources,
        catalog=production_role_catalog(),
    )

    second.validate(sources, production_role_catalog())
    assert second.candidates[1].status == CandidateLifecycleStatus.SUPERSEDED
    assert second.candidates[2].status == CandidateLifecycleStatus.APPROVED
    assert len(second.review_records) == 2


def test_one_to_many_split_preserves_source_hash_evidence_and_mapping() -> None:
    _, sources, v2 = pending_v2_context()
    v3 = migrate_v2_to_v3(v2)
    result = split_candidate(
        v3,
        v3.candidates[0].candidate_id,
        (revision("Python fundamentals"), revision("ML tooling experience")),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    parent = result.candidates[0]
    children = result.candidates[1:]
    assert parent.status == CandidateLifecycleStatus.SUPERSEDED
    assert len(children) == 2
    assert all(item.status == CandidateLifecycleStatus.APPROVED for item in children)
    assert all(item.source_id == parent.source_id for item in children)
    assert all(item.source_content_hash == parent.source_content_hash for item in children)
    assert all(item.evidence == parent.evidence for item in children)
    assert all(item.mapped_role_id == parent.mapped_role_id for item in children)


def test_split_and_revise_cardinality_is_strict() -> None:
    _, sources, v2 = pending_v2_context()
    v3 = migrate_v2_to_v3(v2)
    with pytest.raises(Phase2ValidationError, match="at least two"):
        split_candidate(
            v3,
            v3.candidates[0].candidate_id,
            (revision("Only child"),),
            reviewer_reference=REVIEWER,
            reviewed_at=NOW,
            decision_reason=REASON,
            sources=sources,
            catalog=production_role_catalog(),
        )
    base = {
        "review_id": "placeholder",
        "parent_candidate_id": v3.candidates[0].candidate_id,
        "reviewer_reference": REVIEWER,
        "reviewed_at": NOW,
        "decision_reason": REASON,
    }
    for action, successors, message in (
        ("split", [], "at least two"),
        ("split", ["candidate_one"], "at least two"),
        ("revise", ["candidate_one", "candidate_two"], "exactly one"),
    ):
        with pytest.raises(Phase2ValidationError, match=message):
            CandidateReviewRecord.from_dict(
                {**base, "action": action, "successor_candidate_ids": successors}
            )


def test_missing_successor_and_orphan_superseded_are_rejected() -> None:
    _, sources, v2 = pending_v2_context()
    result = revise_candidate(
        migrate_v2_to_v3(v2),
        v2.candidates[0].candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    missing = result.to_dict()
    missing["candidates"].pop()
    with pytest.raises(Phase2ValidationError, match="missing successor"):
        CurationArtifactV3.from_dict(missing).validate(sources, production_role_catalog())

    orphan = migrate_v2_to_v3(v2).to_dict()
    orphan["candidates"][0].update(status="superseded")
    with pytest.raises(Phase2ValidationError, match="requires revise or split"):
        CurationArtifactV3.from_dict(orphan).validate(sources, production_role_catalog())


def test_lineage_rejects_evidence_outside_parent_and_role_mapping_change() -> None:
    _, sources, v2 = pending_v2_context()
    parent = v2.candidates[0]
    outside = EvidenceLocator(
        parent.source_content_hash,
        parent.evidence.section,
        parent.evidence.end_offset,
        parent.evidence.end_offset + 5,
        "other",
    )
    with pytest.raises(Phase2ValidationError, match="outside parent evidence"):
        revise_candidate(
            migrate_v2_to_v3(v2),
            parent.candidate_id,
            revision("Outside", evidence=outside),
            reviewer_reference=REVIEWER,
            reviewed_at=NOW,
            decision_reason=REASON,
            sources=sources,
            catalog=production_role_catalog(),
        )

    valid = revise_candidate(
        migrate_v2_to_v3(v2),
        parent.candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    ).to_dict()
    valid["candidates"][1]["mapped_role_id"] = "research_engineer"
    valid["candidates"][1]["mapped_specialization_id"] = "ai_research_systems"
    with pytest.raises(Phase2ValidationError, match="incompatible role mapping"):
        CurationArtifactV3.from_dict(valid).validate(sources, production_role_catalog())


def test_lineage_rejects_cross_source_and_content_hash_changes() -> None:
    source_rows, sources, v2 = pending_v2_context(source_count=2)
    result = revise_candidate(
        migrate_v2_to_v3(v2),
        v2.candidates[0].candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    data = result.to_dict()
    child = data["candidates"][-1]
    child["source_id"] = source_rows[1]["source_id"]
    child["candidate_id"] = generate_candidate_id(
        child["source_id"], result.candidates[-1].evidence, child["proposed_name"]
    )
    data["review_records"][0]["successor_candidate_ids"] = [child["candidate_id"]]
    record = data["review_records"][0]
    record["review_id"] = CandidateReviewRecord.create(
        parent_candidate_id=record["parent_candidate_id"],
        action=CandidateReviewAction.REVISE,
        successor_candidate_ids=record["successor_candidate_ids"],
        reviewer_reference=record["reviewer_reference"],
        reviewed_at=record["reviewed_at"],
        decision_reason=record["decision_reason"],
    ).review_id
    with pytest.raises(Phase2ValidationError, match="cannot cross source"):
        CurationArtifactV3.from_dict(data).validate(sources, production_role_catalog())

    data = result.to_dict()
    data["candidates"][-1]["source_content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(Phase2ValidationError, match="content hash"):
        CurationArtifactV3.from_dict(data).validate(sources, production_role_catalog())


def test_duplicate_parent_status_contradiction_and_lineage_cycle_are_rejected() -> None:
    _, sources, v2 = pending_v2_context()
    approved = approve(migrate_v2_to_v3(v2), v2.candidates[0].candidate_id, sources)
    duplicate = approved.to_dict()
    first = duplicate["review_records"][0]
    second = CandidateReviewRecord.create(
        parent_candidate_id=first["parent_candidate_id"],
        action=CandidateReviewAction.APPROVE,
        successor_candidate_ids=(),
        reviewer_reference="another_reviewer",
        reviewed_at=NOW,
        decision_reason="Another terminal review.",
    )
    duplicate["review_records"].append(second.to_dict())
    with pytest.raises(Phase2ValidationError, match="multiple terminal reviews"):
        CurationArtifactV3.from_dict(duplicate).validate(sources, production_role_catalog())

    duplicate_id = approved.to_dict()
    duplicate_id["review_records"].append(deepcopy(duplicate_id["review_records"][0]))
    with pytest.raises(Phase2ValidationError, match="duplicate review IDs"):
        CurationArtifactV3.from_dict(duplicate_id).validate(
            sources, production_role_catalog()
        )

    contradiction = approved.to_dict()
    contradiction["candidates"][0].update(
        status="rejected", reviewer_decision="reject"
    )
    with pytest.raises(Phase2ValidationError, match="status contradicts"):
        CurationArtifactV3.from_dict(contradiction).validate(
            sources, production_role_catalog()
        )

    revised = revise_candidate(
        migrate_v2_to_v3(v2),
        v2.candidates[0].candidate_id,
        revision("Python programming"),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason=REASON,
        sources=sources,
        catalog=production_role_catalog(),
    )
    cycle = revised.to_dict()
    parent_id = cycle["candidates"][0]["candidate_id"]
    child_id = cycle["candidates"][1]["candidate_id"]
    cycle["candidates"][1].update(
        status="superseded",
        reviewer_decision="pending",
        decision_reason=None,
        reviewed_at=None,
    )
    reverse = CandidateReviewRecord.create(
        parent_candidate_id=child_id,
        action=CandidateReviewAction.REVISE,
        successor_candidate_ids=(parent_id,),
        reviewer_reference=REVIEWER,
        reviewed_at=NOW,
        decision_reason="Fixture cycle attempt.",
    )
    cycle["review_records"].append(reverse.to_dict())
    with pytest.raises(Phase2ValidationError, match="cycle"):
        CurationArtifactV3.from_dict(cycle).validate(sources, production_role_catalog())


@pytest.mark.parametrize(
    "field,value",
    [
        ("successor_candidate_ids", []),
        ("reviewer_reference", "provider"),
        ("reviewed_at", NOW),
        ("action", "approve"),
        ("status", "approved"),
    ],
)
def test_provider_payload_cannot_inject_human_lineage_fields(field, value) -> None:
    source = source_data()
    candidate = extracted_candidate(source)
    payload = {
        "evidence": candidate.evidence.to_dict(),
        "proposed_name": "Python",
        "proposed_description": "Use Python.",
        "proposed_category": "technical_skill",
        "proposed_importance": "core",
        "mapped_role_id": "applied_ai_engineer",
        "mapped_specialization_id": "agentic_ai",
        field: value,
    }
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        RequirementCandidate.from_extraction(
            payload,
            source_id=source["source_id"],
            source_content_hash=source["content_hash"],
            extraction=candidate.extraction,
        )


def test_v2_v3_dispatch_is_explicit_and_typed_load_rejects_tampering(tmp_path) -> None:
    _, sources, v2 = pending_v2_context()
    v3 = migrate_v2_to_v3(v2)
    v2_with_reviews = v2.to_dict()
    v2_with_reviews["review_records"] = []
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        CurationArtifact.from_dict(v2_with_reviews)
    v3_without_reviews = v3.to_dict()
    del v3_without_reviews["review_records"]
    with pytest.raises(Phase2ValidationError, match="must be a list"):
        CurationArtifactV3.from_dict(v3_without_reviews)

    reviewed = approve(v3, v3.candidates[0].candidate_id, sources)
    path = tmp_path / "curation-v3.json"
    path.write_text(json.dumps(reviewed.to_dict()), encoding="utf-8")
    loaded = load_curation_artifact(
        path, sources=sources, catalog=production_role_catalog()
    )
    assert loaded == reviewed
    tampered = reviewed.to_dict()
    tampered["review_records"][0]["reviewer_reference"] = "forged_reviewer"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="review_id"):
        load_curation_artifact(path, sources=sources, catalog=production_role_catalog())


def test_v3_atomic_save_failure_preserves_existing_file(tmp_path, monkeypatch) -> None:
    _, sources, v2 = pending_v2_context()
    reviewed = approve(
        migrate_v2_to_v3(v2), v2.candidates[0].candidate_id, sources
    )
    path = tmp_path / "curation-v3.json"
    path.write_bytes(b'{"existing": true}\n')
    original = path.read_bytes()
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        save_curation_artifact(
            reviewed, path, sources=sources, catalog=production_role_catalog()
        )
    assert path.read_bytes() == original
