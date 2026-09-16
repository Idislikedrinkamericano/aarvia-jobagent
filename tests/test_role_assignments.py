from dataclasses import replace
import json

import pytest

from aarvia.jd_curation import (
    CandidateLifecycleStatus,
    CandidateReviewAction,
    CandidateReviewRecord,
    ClusterLifecycleStatus,
    CurationArtifact,
    CurationArtifactV5,
    EvidenceLocator,
    CandidateRevision,
    approve_candidate,
    generate_candidate_v4_id,
    migrate_curation_v4_to_v5,
    migrate_curation_v5_to_v6,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    revise_candidate,
    ReviewerDecision,
)
from aarvia.jd_sources import (
    JDSourceCollection,
    content_sha256,
    migrate_source_v2_to_v3,
    normalize_jd_content,
)
from aarvia.live_jobs import (
    LiveJobCollectionV2,
    LiveJobCollectionV4,
    load_live_job_collection,
    migrate_live_jobs_v2_to_v3,
    migrate_live_jobs_v3_to_v4,
    save_live_job_collection,
)
from aarvia.role_assignments import (
    RoleAssignmentArtifact,
    RoleAssignmentProposal,
    RoleAssignmentStatus,
    RoleEvidenceReference,
    add_reclassified_assignment,
    confirm_initial_role_assignment,
    generate_role_assignment_id,
    generate_role_assignment_review_id,
    load_role_assignment_artifact,
    save_role_assignment_artifact,
)
from aarvia.role_catalog import CatalogType, Phase2ValidationError, production_role_catalog
from aarvia.curation_workflow import (
    confirm_cluster,
    migrate_curation_v6_to_v7,
)
from aarvia.role_direction_context import (
    RoleReclassificationBlockerCode,
    reclassify_job_role,
    save_role_reclassification,
    validate_role_direction_context,
)
from aarvia.jd_curation import load_curation_artifact, save_curation_artifact
from aarvia.catalog_build import BuildBlockerCode, RoleSample, build_catalog_draft
from phase2b_fixtures import (
    FAKE_JD,
    NOW,
    curation_data,
    confirmed_assignments,
    live_v2_data,
    source_collection_data,
    source_data,
)


LATER = "2026-09-02T12:00:00+00:00"
LATEST = "2026-09-03T12:00:00+00:00"


def context():
    source_dict = source_data(1)
    legacy_sources = JDSourceCollection.from_dict(source_collection_data([source_dict]))
    sources = migrate_source_v2_to_v3(
        legacy_sources,
        scope_by_source_id={source_dict["source_id"]: "full_job_description"},
        content_length_by_source_id={source_dict["source_id"]: len(normalize_jd_content(FAKE_JD))},
    )
    capture = sources.captures[0]
    contents = {capture.capture_id: FAKE_JD}
    excerpt = "Build reliable machine learning applications."
    evidence = RoleEvidenceReference(
        source_id=source_dict["source_id"],
        capture_id=capture.capture_id,
        content_hash=capture.content_hash,
        start_offset=0,
        end_offset=len(excerpt),
        exact_value_snapshot=excerpt,
    )
    empty = RoleAssignmentArtifact(
        artifact_id="fixture_role_assignments",
        artifact_type=CatalogType.TEST_FIXTURE,
        source_collection_id=sources.collection_id,
        catalog_version="1.0.0",
        created_at=NOW,
        assignments=(),
        review_records=(),
    )
    assignments = confirm_initial_role_assignment(
        empty,
        canonical_job_id=source_dict["canonical_job_id"],
        source_id=source_dict["source_id"],
        role_id="applied_ai_engineer",
        specialization_id="agentic_ai",
        evidence_references=(evidence,),
        reviewer_reference="fixture.role_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer confirmed the initial mapping.",
        sources=sources,
        catalog=production_role_catalog(),
        capture_contents=contents,
    )
    jobs_v2 = LiveJobCollectionV2.from_dict(live_v2_data(source_dict))
    jobs_v3 = migrate_live_jobs_v2_to_v3(
        jobs_v2, legacy_sources=legacy_sources, sources=sources,
        catalog=production_role_catalog(),
    )
    jobs_v4 = migrate_live_jobs_v3_to_v4(
        jobs_v3, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    raw = curation_data([source_dict])
    raw["candidates"][0].update(
        status="review_pending", reviewer_decision="pending",
        decision_reason=None, reviewed_at=None,
    )
    raw["clusters"][0].update(
        status="proposed", reviewer_decision="pending",
        decision_reason=None, reviewed_at=None,
    )
    v3 = migrate_v2_to_v3(CurationArtifact.from_dict(raw))
    v4 = migrate_v3_to_v4(v3, sources=sources, catalog=production_role_catalog())
    v5 = migrate_curation_v4_to_v5(
        v4, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    validate_role_direction_context(
        assignments=assignments, live_jobs=jobs_v4, curation=v5,
        sources=sources, catalog=production_role_catalog(),
        capture_contents=contents,
    )
    return source_dict, sources, contents, evidence, assignments, jobs_v4, v5


def reclassified(ctx=None):
    ctx = context() if ctx is None else ctx
    source, sources, contents, evidence, assignments, jobs, curation = ctx
    result = reclassify_job_role(
        assignments=assignments,
        live_jobs=jobs,
        curation=curation,
        sources=sources,
        catalog=production_role_catalog(),
        capture_contents=contents,
        current_assignment_id=assignments.current_for_job(source["canonical_job_id"]).assignment_id,
        new_role_id="machine_learning_engineer",
        new_specialization_id=None,
        evidence_references=(evidence,),
        reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATER,
        decision_reason="Fixture duties primarily concern ML engineering.",
    )
    return ctx, result


def test_initial_confirmation_has_deterministic_assignment_and_review_ids(tmp_path) -> None:
    first = context()
    second = context()
    assert first[4] == second[4]
    assignment = first[4].assignments[0]
    review = first[4].review_records[0]
    assert assignment.assignment_id == generate_role_assignment_id(
        canonical_job_id=assignment.canonical_job_id,
        source_id=assignment.source_id,
        role_id=assignment.role_id,
        specialization_id=assignment.specialization_id,
        previous_assignment_reference=None,
        reviewed_at=review.reviewed_at,
        evidence_references=assignment.evidence_references,
    )
    assert review.review_id == generate_role_assignment_review_id(
        assignment_id=assignment.assignment_id,
        action=review.action,
        previous_assignment_reference=None,
        old_role_id=None,
        old_specialization_id=None,
        new_role_id=assignment.role_id,
        new_specialization_id=assignment.specialization_id,
        evidence_references=review.evidence_references,
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        decision_reason=review.decision_reason,
    )
    alternate_text = "Use Python."
    alternate_start = normalize_jd_content(FAKE_JD).index(alternate_text)
    alternate = replace(
        review.evidence_references[0], start_offset=alternate_start,
        end_offset=alternate_start + len(alternate_text),
        exact_value_snapshot=alternate_text,
    )
    assert review.review_id != generate_role_assignment_review_id(
        assignment_id=assignment.assignment_id,
        action=review.action,
        previous_assignment_reference=None,
        old_role_id=None,
        old_specialization_id=None,
        new_role_id=assignment.role_id,
        new_specialization_id=assignment.specialization_id,
        evidence_references=(alternate,),
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        decision_reason=review.decision_reason,
    )
    path = save_role_assignment_artifact(
        first[4], tmp_path / "assignments.json", sources=first[1],
        catalog=production_role_catalog(), capture_contents=first[2],
    )
    assert load_role_assignment_artifact(
        path, sources=first[1], catalog=production_role_catalog(),
        capture_contents=first[2],
    ) == first[4]


@pytest.mark.parametrize("field", ["reviewer_reference", "reviewed_at", "decision_reason"])
def test_initial_confirmation_rejects_missing_human_review_fields(field) -> None:
    source, sources, contents, evidence, _, _, _ = context()
    empty = RoleAssignmentArtifact(
        "fixture_other_assignments", CatalogType.TEST_FIXTURE,
        sources.collection_id, "1.0.0", NOW, (), (),
    )
    kwargs = {
        "reviewer_reference": "fixture.reviewer",
        "reviewed_at": NOW,
        "decision_reason": "Fixture reason.",
    }
    kwargs[field] = None
    with pytest.raises(Phase2ValidationError):
        confirm_initial_role_assignment(
            empty, canonical_job_id=source["canonical_job_id"],
            source_id=source["source_id"], role_id="applied_ai_engineer",
            specialization_id="agentic_ai", evidence_references=(evidence,),
            sources=sources, catalog=production_role_catalog(),
            capture_contents=contents, **kwargs,
        )


def test_evidence_and_specialization_are_strictly_validated() -> None:
    source, sources, contents, evidence, assignments, _, _ = context()
    bad_snapshot = replace(evidence, exact_value_snapshot="Unsupported words")
    with pytest.raises(Phase2ValidationError, match="snapshot"):
        bad_snapshot.validate(
            canonical_job_id=source["canonical_job_id"], sources=sources,
            capture_contents=contents,
        )
    raw = assignments.to_dict()
    raw["assignments"][0]["specialization_id"] = "unknown_specialization"
    with pytest.raises(Phase2ValidationError, match="specialization"):
        RoleAssignmentArtifact.from_dict(raw).validate(
            sources, production_role_catalog(), contents
        )


def test_empty_evidence_multiple_current_and_superseded_leaf_are_rejected() -> None:
    source, sources, contents, evidence, assignments, _, _ = context()
    empty = replace(assignments, assignments=(), review_records=())
    with pytest.raises(Phase2ValidationError, match="evidence_references"):
        confirm_initial_role_assignment(
            empty, canonical_job_id=source["canonical_job_id"], source_id=source["source_id"],
            role_id="applied_ai_engineer", specialization_id="agentic_ai",
            evidence_references=(), reviewer_reference="fixture.role_reviewer",
            reviewed_at=NOW, decision_reason="Fixture review.", sources=sources,
            catalog=production_role_catalog(), capture_contents=contents,
        )
    second = confirm_initial_role_assignment(
        empty, canonical_job_id=source["canonical_job_id"], source_id=source["source_id"],
        role_id="machine_learning_engineer", specialization_id=None,
        evidence_references=(evidence,), reviewer_reference="fixture.other_reviewer",
        reviewed_at=LATER, decision_reason="Independent conflicting review.", sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    conflict = replace(
        assignments,
        assignments=assignments.assignments + second.assignments,
        review_records=assignments.review_records + second.review_records,
    )
    with pytest.raises(Phase2ValidationError, match="multiple current"):
        conflict.validate(sources, production_role_catalog(), contents)
    with pytest.raises(Phase2ValidationError, match="status contradicts"):
        replace(
            assignments,
            assignments=(replace(assignments.assignments[0], status=RoleAssignmentStatus.SUPERSEDED),),
        ).validate(sources, production_role_catalog(), contents)


def test_missing_previous_assignment_and_direct_cycle_tampering_are_rejected() -> None:
    ctx, result = reclassified()
    current = result.assignments.current_for_job(ctx[0]["canonical_job_id"])
    review = next(item for item in result.assignments.review_records if item.assignment_id == current.assignment_id)
    missing = "assignment_missing"
    changed_id = generate_role_assignment_id(
        canonical_job_id=current.canonical_job_id, source_id=current.source_id,
        role_id=current.role_id, specialization_id=current.specialization_id,
        previous_assignment_reference=missing, reviewed_at=review.reviewed_at,
        evidence_references=current.evidence_references,
    )
    changed_review_id = generate_role_assignment_review_id(
        assignment_id=changed_id, action=review.action,
        previous_assignment_reference=missing, old_role_id=review.old_role_id,
        old_specialization_id=review.old_specialization_id,
        new_role_id=review.new_role_id, new_specialization_id=review.new_specialization_id,
        evidence_references=review.evidence_references,
        reviewer_reference=review.reviewer_reference, reviewed_at=review.reviewed_at,
        decision_reason=review.decision_reason,
    )
    changed = replace(
        current, assignment_id=changed_id, previous_assignment_reference=missing,
        review_reference=changed_review_id,
    )
    changed_review = replace(
        review, review_id=changed_review_id, assignment_id=changed_id,
        previous_assignment_reference=missing,
    )
    artifact = replace(
        result.assignments,
        assignments=tuple(changed if item.assignment_id == current.assignment_id else item for item in result.assignments.assignments),
        review_records=tuple(changed_review if item.assignment_id == current.assignment_id else item for item in result.assignments.review_records),
    )
    with pytest.raises(Phase2ValidationError, match="missing previous"):
        artifact.validate(ctx[1], production_role_catalog(), ctx[2])
    raw = result.assignments.to_dict()
    raw["assignments"][0]["previous_assignment_reference"] = raw["assignments"][1]["assignment_id"]
    with pytest.raises(Phase2ValidationError):
        RoleAssignmentArtifact.from_dict(raw).validate(ctx[1], production_role_catalog(), ctx[2])


def test_reclassification_a_to_b_to_a_has_linear_auditable_identity() -> None:
    ctx, first = reclassified()
    assert first.succeeded
    second_assignments = add_reclassified_assignment(
        first.assignments,
        current_assignment_id=first.assignments.current_for_job(ctx[0]["canonical_job_id"]).assignment_id,
        role_id="applied_ai_engineer",
        specialization_id="agentic_ai",
        evidence_references=(ctx[3],),
        reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATEST,
        decision_reason="Fixture reviewer restored the earlier classification.",
        sources=ctx[1], catalog=production_role_catalog(),
        capture_contents=ctx[2],
    )
    assert len({item.assignment_id for item in second_assignments.assignments}) == 3
    assert [item.status for item in second_assignments.assignments].count(RoleAssignmentStatus.CURRENT) == 1


def test_provider_cannot_inject_human_or_terminal_fields() -> None:
    _, _, _, evidence, _, _, _ = context()
    payload = {
        "role_id": "applied_ai_engineer",
        "specialization_id": "agentic_ai",
        "evidence_references": [evidence.to_dict()],
        "reviewer_reference": "provider",
    }
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        RoleAssignmentProposal.from_provider(payload)


def test_joint_validation_rejects_independent_projection_tampering() -> None:
    _, sources, contents, _, assignments, jobs, curation = context()
    bad_job = replace(jobs.jobs[0], mapped_role_id="machine_learning_engineer", mapped_specialization_id=None)
    with pytest.raises(Phase2ValidationError, match="projection"):
        replace(jobs, jobs=(bad_job,)).validate(production_role_catalog(), sources, assignments)
    bad_candidate = replace(curation.candidates[0], mapped_role_id="machine_learning_engineer", mapped_specialization_id=None)
    with pytest.raises(Phase2ValidationError, match="projection"):
        replace(curation, candidates=(bad_candidate,)).validate(sources, production_role_catalog(), assignments)
    bad_cluster = replace(curation.clusters[0], role_id="machine_learning_engineer", specialization_id=None)
    with pytest.raises(Phase2ValidationError, match="generated|mixes"):
        replace(curation, clusters=(bad_cluster,)).validate(sources, production_role_catalog(), assignments)


def test_typed_load_rejects_live_job_json_role_tampering(tmp_path) -> None:
    _, sources, contents, _, assignments, jobs, curation = context()
    raw = jobs.to_dict()
    raw["jobs"][0]["mapped_role_id"] = "machine_learning_engineer"
    raw["jobs"][0]["mapped_specialization_id"] = None
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="projection"):
        load_live_job_collection(
            path, sources=sources, catalog=production_role_catalog(),
            assignments=assignments, capture_contents=contents,
        )

    curation_raw = curation.to_dict()
    curation_raw["candidates"][0]["mapped_role_id"] = "machine_learning_engineer"
    curation_raw["candidates"][0]["mapped_specialization_id"] = None
    curation_path = tmp_path / "curation.json"
    curation_path.write_text(json.dumps(curation_raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="projection"):
        load_curation_artifact(
            curation_path, sources=sources, catalog=production_role_catalog(),
            assignments=assignments, capture_contents=contents,
        )


def test_schema_v4_and_v5_typed_round_trip_requires_assignment_context(tmp_path) -> None:
    _, sources, contents, _, assignments, jobs, curation = context()
    job_path = save_live_job_collection(
        jobs, tmp_path / "jobs.json", sources=sources,
        catalog=production_role_catalog(), assignments=assignments,
        capture_contents=contents,
    )
    curation_path = save_curation_artifact(
        curation, tmp_path / "curation.json", sources=sources,
        catalog=production_role_catalog(), assignments=assignments,
        capture_contents=contents,
    )
    assert load_live_job_collection(
        job_path, sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    ) == jobs
    assert load_curation_artifact(
        curation_path, sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    ) == curation
    with pytest.raises(Phase2ValidationError, match="schema 4 requires"):
        load_live_job_collection(job_path, sources=sources, catalog=production_role_catalog())
    with pytest.raises(Phase2ValidationError, match="schema 5 requires"):
        load_curation_artifact(curation_path, sources=sources, catalog=production_role_catalog())


def test_review_pending_reclassification_is_atomic_in_memory() -> None:
    ctx, result = reclassified()
    assert result.succeeded
    assert result.blockers == ()
    assert result.invalidated_derived_references == (
        "clause_coverage_audit", "concept_matrix", "role_sample_counts"
    )
    current = result.assignments.current_for_job(ctx[0]["canonical_job_id"])
    assert current.role_id == "machine_learning_engineer"
    assert result.live_jobs.jobs[0].mapped_role_id == current.role_id
    assert result.curation.candidates[0].mapped_role_id == current.role_id
    assert result.curation.candidates[0].status == ctx[6].candidates[0].status
    assert result.curation.candidates[0].evidence == ctx[6].candidates[0].evidence
    assert result.curation.clusters[0].role_id == current.role_id
    before = ctx[6].candidates[0].to_dict()
    after = result.curation.candidates[0].to_dict()
    for field in (
        "candidate_id", "source_id", "capture_id", "source_content_hash",
        "evidence", "proposed_name", "proposed_description", "proposed_category",
        "proposed_importance", "extraction", "status", "reviewer_decision",
        "decision_reason", "reviewed_at",
    ):
        assert after[field] == before[field]


@pytest.mark.parametrize(
    ("candidate_status", "action", "decision"),
    [
        (CandidateLifecycleStatus.APPROVED, CandidateReviewAction.APPROVE, ReviewerDecision.APPROVE),
        (CandidateLifecycleStatus.REJECTED, CandidateReviewAction.REJECT, ReviewerDecision.REJECT),
    ],
)
def test_terminal_candidate_blocks_reclassification(candidate_status, action, decision) -> None:
    ctx = context()
    candidate = replace(
        ctx[6].candidates[0], status=candidate_status,
        reviewer_decision=decision, decision_reason="Fixture terminal review.",
        reviewed_at=NOW,
    )
    review = CandidateReviewRecord.create(
        parent_candidate_id=candidate.candidate_id, action=action,
        successor_candidate_ids=(), reviewer_reference="fixture.candidate_reviewer",
        reviewed_at=NOW, decision_reason="Fixture terminal review.",
    )
    curation = replace(ctx[6], candidates=(candidate,), review_records=(review,))
    curation.validate(ctx[1], production_role_catalog(), ctx[4])
    result = reclassify_job_role(
        assignments=ctx[4], live_jobs=ctx[5], curation=curation,
        sources=ctx[1], catalog=production_role_catalog(), capture_contents=ctx[2],
        current_assignment_id=ctx[4].assignments[0].assignment_id,
        new_role_id="machine_learning_engineer", new_specialization_id=None,
        evidence_references=(ctx[3],), reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATER, decision_reason="Fixture reclassification.",
    )
    assert not result.succeeded
    assert RoleReclassificationBlockerCode.TERMINAL_CANDIDATE in {item.code for item in result.blockers}


@pytest.mark.parametrize(
    ("status", "decision", "expected"),
    [
        (ClusterLifecycleStatus.CONFIRMED, ReviewerDecision.APPROVE, RoleReclassificationBlockerCode.CONFIRMED_CLUSTER),
        (ClusterLifecycleStatus.REJECTED, ReviewerDecision.REJECT, RoleReclassificationBlockerCode.NON_PROPOSED_CLUSTER),
    ],
)
def test_non_proposed_cluster_blocks_reclassification(status, decision, expected) -> None:
    ctx = context()
    confirmed = replace(
        ctx[6].clusters[0], status=status,
        reviewer_decision=decision, decision_reason="Fixture cluster review.",
        reviewed_at=NOW,
    )
    curation = replace(ctx[6], clusters=(confirmed,))
    curation.validate(ctx[1], production_role_catalog(), ctx[4])
    result = reclassify_job_role(
        assignments=ctx[4], live_jobs=ctx[5], curation=curation,
        sources=ctx[1], catalog=production_role_catalog(), capture_contents=ctx[2],
        current_assignment_id=ctx[4].assignments[0].assignment_id,
        new_role_id="machine_learning_engineer", new_specialization_id=None,
        evidence_references=(ctx[3],), reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATER, decision_reason="Fixture reclassification.",
    )
    assert expected in {item.code for item in result.blockers}


def test_existing_candidate_lineage_blocks_reclassification() -> None:
    ctx = context()
    parent = ctx[6].candidates[0]
    name = parent.proposed_name + " revised"
    child = replace(
        parent,
        candidate_id=generate_candidate_v4_id(
            parent.source_id, parent.capture_id, parent.evidence, name
        ),
        proposed_name=name,
        status=CandidateLifecycleStatus.APPROVED,
        cluster_id=None,
        reviewer_decision=ReviewerDecision.APPROVE,
        decision_reason="Fixture revision.",
        reviewed_at=NOW,
    )
    parent = replace(parent, status=CandidateLifecycleStatus.SUPERSEDED)
    review = CandidateReviewRecord.create(
        parent_candidate_id=parent.candidate_id,
        action=CandidateReviewAction.REVISE,
        successor_candidate_ids=(child.candidate_id,),
        reviewer_reference="fixture.candidate_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture revision.",
    )
    curation = replace(
        ctx[6], candidates=(parent, child), review_records=(review,)
    )
    curation.validate(ctx[1], production_role_catalog(), ctx[4])
    result = reclassify_job_role(
        assignments=ctx[4], live_jobs=ctx[5], curation=curation,
        sources=ctx[1], catalog=production_role_catalog(), capture_contents=ctx[2],
        current_assignment_id=ctx[4].assignments[0].assignment_id,
        new_role_id="machine_learning_engineer", new_specialization_id=None,
        evidence_references=(ctx[3],), reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATER, decision_reason="Fixture reclassification.",
    )
    assert RoleReclassificationBlockerCode.CANDIDATE_LINEAGE_EXISTS in {item.code for item in result.blockers}


def test_schema_v5_review_api_preserves_assignment_provenance() -> None:
    ctx = context()
    parent = ctx[6].candidates[0]
    revised = revise_candidate(
        ctx[6], parent.candidate_id,
        CandidateRevision(
            proposed_name="Python engineering",
            proposed_description="Use Python in reliable engineering work.",
            proposed_category=parent.proposed_category,
            proposed_importance=parent.proposed_importance,
        ),
        reviewer_reference="fixture.candidate_reviewer", reviewed_at=LATER,
        decision_reason="Fixture reviewer clarified the requirement.",
        sources=ctx[1], catalog=production_role_catalog(), assignments=ctx[4],
        capture_contents=ctx[2],
    )
    successor_id = revised.review_records[-1].successor_candidate_ids[0]
    successor = next(item for item in revised.candidates if item.candidate_id == successor_id)
    assert successor.role_assignment_reference == parent.role_assignment_reference
    assert isinstance(revised, CurationArtifactV5)


def test_mixed_job_cluster_blocks_reclassification() -> None:
    data = [source_data(1), source_data(2)]
    legacy_sources = JDSourceCollection.from_dict(source_collection_data(data))
    sources = migrate_source_v2_to_v3(
        legacy_sources,
        scope_by_source_id={item["source_id"]: "full_job_description" for item in data},
        content_length_by_source_id={item["source_id"]: len(normalize_jd_content(FAKE_JD)) for item in data},
    )
    assignments, contents = confirmed_assignments(sources)
    jobs_raw = live_v2_data(data[0])
    jobs_raw["jobs"].append(live_v2_data(data[1])["jobs"][0])
    jobs_v3 = migrate_live_jobs_v2_to_v3(
        LiveJobCollectionV2.from_dict(jobs_raw), legacy_sources=legacy_sources,
        sources=sources, catalog=production_role_catalog(),
    )
    jobs = migrate_live_jobs_v3_to_v4(
        jobs_v3, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    raw = curation_data(data)
    for candidate in raw["candidates"]:
        candidate.update(status="review_pending", reviewer_decision="pending", decision_reason=None, reviewed_at=None)
    raw["clusters"][0].update(status="proposed", reviewer_decision="pending", decision_reason=None, reviewed_at=None)
    v4 = migrate_v3_to_v4(
        migrate_v2_to_v3(CurationArtifact.from_dict(raw)),
        sources=sources, catalog=production_role_catalog(),
    )
    curation = migrate_curation_v4_to_v5(
        v4, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    first = assignments.current_for_job(data[0]["canonical_job_id"])
    evidence = first.evidence_references
    result = reclassify_job_role(
        assignments=assignments, live_jobs=jobs, curation=curation,
        sources=sources, catalog=production_role_catalog(), capture_contents=contents,
        current_assignment_id=first.assignment_id,
        new_role_id="machine_learning_engineer", new_specialization_id=None,
        evidence_references=evidence, reviewer_reference="fixture.role_reviewer",
        reviewed_at=LATER, decision_reason="Fixture reclassification.",
    )
    assert RoleReclassificationBlockerCode.MIXED_JOB_CLUSTER in {item.code for item in result.blockers}


def test_builder_rejects_forged_role_and_duplicate_canonical_job() -> None:
    ctx = context()
    curation = migrate_curation_v5_to_v6(
        ctx[6], sources=ctx[1], catalog=production_role_catalog(),
        assignments=ctx[4], capture_contents=ctx[2],
    )
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
        sources=ctx[1],
        catalog=production_role_catalog(),
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    for candidate in tuple(curation.candidates):
        curation = approve_candidate(
            curation,
            candidate.candidate_id,
            reviewer_reference="fixture.candidate_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture reviewer approved this capability.",
            sources=ctx[1],
            catalog=production_role_catalog(),
            assignments=ctx[4],
            capture_contents=ctx[2],
        )
    for cluster in tuple(curation.clusters):
        curation = confirm_cluster(
            curation,
            cluster.cluster_id,
            reviewer_reference="fixture.cluster_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture reviewer confirmed this semantic Cluster.",
            sources=ctx[1],
            catalog=production_role_catalog(),
            assignments=ctx[4],
            capture_contents=ctx[2],
        )
    wrong = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=(RoleSample(ctx[0]["source_id"], "machine_learning_engineer"),),
        sources=ctx[1], curation=curation, catalog=production_role_catalog(),
        assignments=ctx[4], capture_contents=ctx[2],
    )
    assert BuildBlockerCode.ROLE_SAMPLE_MISMATCH in {item.code for item in wrong.blockers}
    duplicate = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=(
            RoleSample(ctx[0]["source_id"], "applied_ai_engineer"),
            RoleSample(ctx[0]["source_id"], "machine_learning_engineer"),
        ),
        sources=ctx[1], curation=curation, catalog=production_role_catalog(),
        assignments=ctx[4], capture_contents=ctx[2],
    )
    assert BuildBlockerCode.DUPLICATE_CANONICAL_JOB in {item.code for item in duplicate.blockers}


def test_role_direction_context_accepts_valid_curation_schema7() -> None:
    ctx = context()
    curation = migrate_curation_v5_to_v6(
        ctx[6],
        sources=ctx[1],
        catalog=production_role_catalog(),
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    curation = migrate_curation_v6_to_v7(
        curation,
        sources=ctx[1],
        catalog=production_role_catalog(),
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    validate_role_direction_context(
        assignments=ctx[4],
        live_jobs=ctx[5],
        curation=curation,
        sources=ctx[1],
        catalog=production_role_catalog(),
        capture_contents=ctx[2],
    )


def test_validation_failure_changes_no_transaction_target(tmp_path) -> None:
    ctx, result = reclassified()
    bad_jobs = replace(
        result.live_jobs,
        jobs=(replace(result.live_jobs.jobs[0], mapped_role_id="backend_engineer"),),
    )
    bad_result = replace(result, live_jobs=bad_jobs)
    paths = [tmp_path / name for name in ("a.json", "j.json", "c.json")]
    with pytest.raises(Phase2ValidationError, match="projection"):
        save_role_reclassification(
            bad_result, assignment_path=paths[0], live_jobs_path=paths[1],
            curation_path=paths[2], sources=ctx[1],
            catalog=production_role_catalog(), capture_contents=ctx[2],
        )
    assert not any(path.exists() for path in paths)


def test_multi_artifact_save_round_trip_and_failure_preservation(tmp_path, monkeypatch) -> None:
    ctx, result = reclassified()
    paths = [tmp_path / name for name in ("assignments.json", "jobs.json", "curation.json")]
    save_role_reclassification(
        result, assignment_path=paths[0], live_jobs_path=paths[1],
        curation_path=paths[2], sources=ctx[1], catalog=production_role_catalog(),
        capture_contents=ctx[2],
    )
    loaded_assignments = load_role_assignment_artifact(
        paths[0], sources=ctx[1], catalog=production_role_catalog(),
        capture_contents=ctx[2],
    )
    loaded_jobs = load_live_job_collection(
        paths[1], sources=ctx[1], catalog=production_role_catalog(),
        assignments=loaded_assignments, capture_contents=ctx[2],
    )
    loaded_curation = load_curation_artifact(
        paths[2], sources=ctx[1], catalog=production_role_catalog(),
        assignments=loaded_assignments, capture_contents=ctx[2],
    )
    assert (loaded_assignments, loaded_jobs, loaded_curation) == (
        result.assignments, result.live_jobs, result.curation
    )
    originals = [path.read_bytes() for path in paths]
    calls = 0
    from aarvia import phase2_storage
    real_replace = phase2_storage.os.replace

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture replacement failure")
        return real_replace(source, target)

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_second)
    with pytest.raises(OSError, match="replacement failure"):
        save_role_reclassification(
            result, assignment_path=paths[0], live_jobs_path=paths[1],
            curation_path=paths[2], sources=ctx[1], catalog=production_role_catalog(),
            capture_contents=ctx[2],
        )
    assert [path.read_bytes() for path in paths] == originals
