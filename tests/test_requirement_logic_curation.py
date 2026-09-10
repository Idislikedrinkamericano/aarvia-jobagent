from dataclasses import replace
import json

import pytest

from aarvia.catalog_build import BuildBlockerCode, RoleSample, build_catalog_draft
from aarvia.jd_curation import (
    CandidateLifecycleStatus,
    CandidateLogicGroup,
    CandidateLogicGroupReviewAction,
    CandidateLogicGroupReviewRecord,
    CandidateLogicGroupStatus,
    CandidateRevision,
    CurationArtifactV5,
    CurationArtifactV6,
    EvidenceLocator,
    ReviewerDecision,
    confirm_candidate_logic_group,
    generate_candidate_logic_group_id,
    generate_candidate_logic_group_review_id,
    generate_candidate_v4_id,
    load_curation_artifact,
    migrate_curation_v4_to_v5,
    migrate_curation_v5_to_v6,
    reject_candidate_logic_group,
    reject_candidate,
    revise_candidate,
    save_curation_artifact,
)
from aarvia.requirement_logic import RequirementLogicOperator, RequirementModality
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from phase2b_fixtures import FAKE_HASH, NOW, confirmed_assignments
from test_catalog_build import contexts
from test_role_assignments import context


def logic_context(operator=RequirementLogicOperator.ANY_OF):
    ctx = context()
    source, sources, contents, _, assignments, _, v5 = ctx
    base = migrate_curation_v5_to_v6(
        v5, sources=sources, catalog=production_role_catalog(),
        assignments=assignments, capture_contents=contents,
    )
    first = base.candidates[0]
    second_name = "Machine learning tooling"
    second = replace(
        first,
        candidate_id=generate_candidate_v4_id(
            first.source_id, first.capture_id, first.evidence, second_name
        ),
        proposed_name=second_name,
        proposed_description="Fixture machine learning tooling capability.",
        status=CandidateLifecycleStatus.NORMALIZED,
        cluster_id=None,
        reviewer_decision=ReviewerDecision.PENDING,
        decision_reason=None,
        reviewed_at=None,
    )
    excerpt = "Build reliable machine learning applications."
    evidence = EvidenceLocator(
        source_content_hash=FAKE_HASH,
        section="Qualifications",
        start_offset=0,
        end_offset=len(excerpt),
        minimal_excerpt=excerpt,
    )
    group = CandidateLogicGroup.create_proposed(
        operator=operator,
        member_candidate_references=(second.candidate_id, first.candidate_id),
        source_reference=first.source_id,
        capture_reference=first.capture_id,
        source_content_hash=first.source_content_hash,
        role_assignment_reference=first.role_assignment_reference,
        mapped_role_id=first.mapped_role_id,
        mapped_specialization_id=first.mapped_specialization_id,
        evidence=evidence,
        modality=RequirementModality.REQUIRED,
    )
    artifact = replace(
        base,
        candidates=(*base.candidates, second),
        logic_groups=(group,),
    )
    artifact.validate(sources, production_role_catalog(), assignments, contents)
    return ctx, artifact, group


def review_kwargs(ctx):
    return {
        "reviewer_reference": "fixture.logic_reviewer",
        "reviewed_at": NOW,
        "decision_reason": "Fixture reviewer confirmed the source logic.",
        "sources": ctx[1],
        "catalog": production_role_catalog(),
        "assignments": ctx[4],
        "capture_contents": ctx[2],
    }


@pytest.mark.parametrize("operator", list(RequirementLogicOperator))
def test_valid_logic_groups_and_stable_id_semantics(operator) -> None:
    _, artifact, group = logic_context(operator)
    members = group.member_candidate_references
    assert group.logic_group_id == generate_candidate_logic_group_id(
        operator=operator,
        member_candidate_references=tuple(reversed(members)),
        source_reference=group.source_reference,
        capture_reference=group.capture_reference,
        source_content_hash=group.source_content_hash,
        evidence_start=group.evidence.start_offset,
        evidence_end=group.evidence.end_offset,
        role_assignment_reference=group.role_assignment_reference,
        modality=group.modality,
    )
    other = RequirementLogicOperator.ALL_OF if operator == RequirementLogicOperator.ANY_OF else RequirementLogicOperator.ANY_OF
    assert group.logic_group_id != generate_candidate_logic_group_id(
        operator=other,
        member_candidate_references=members,
        source_reference=group.source_reference,
        capture_reference=group.capture_reference,
        source_content_hash=group.source_content_hash,
        evidence_start=group.evidence.start_offset,
        evidence_end=group.evidence.end_offset,
        role_assignment_reference=group.role_assignment_reference,
        modality=group.modality,
    )
    assert group.logic_group_id != generate_candidate_logic_group_id(
        operator=operator,
        member_candidate_references=members,
        source_reference=group.source_reference,
        capture_reference=group.capture_reference,
        source_content_hash=group.source_content_hash,
        evidence_start=group.evidence.start_offset,
        evidence_end=group.evidence.end_offset,
        role_assignment_reference=group.role_assignment_reference,
        modality=RequirementModality.PREFERRED,
    )
    assert CurationArtifactV6.from_dict(artifact.to_dict()) == artifact


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("member_candidate_references", [], "at least two"),
        ("member_candidate_references", ["candidate_one"], "at least two"),
        ("member_candidate_references", ["candidate_one", "candidate_one"], "duplicate"),
        ("operator", "neither", "operator"),
    ],
)
def test_logic_group_wire_shape_is_strict(field, value, match) -> None:
    _, _, group = logic_context()
    raw = group.to_dict()
    raw[field] = value
    with pytest.raises(Phase2ValidationError, match=match):
        CandidateLogicGroup.from_dict(raw)
    raw = group.to_dict()
    raw["unexpected"] = True
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        CandidateLogicGroup.from_dict(raw)


def test_evidence_source_capture_role_and_member_integrity() -> None:
    ctx, artifact, group = logic_context()
    kwargs = (ctx[1], production_role_catalog(), ctx[4], ctx[2])
    changed_capture = replace(group, capture_reference="capture_missing")
    changed_capture = replace(
        changed_capture,
        logic_group_id=generate_candidate_logic_group_id(
            operator=changed_capture.operator,
            member_candidate_references=changed_capture.member_candidate_references,
            source_reference=changed_capture.source_reference,
            capture_reference=changed_capture.capture_reference,
            source_content_hash=changed_capture.source_content_hash,
            evidence_start=changed_capture.evidence.start_offset,
            evidence_end=changed_capture.evidence.end_offset,
            role_assignment_reference=changed_capture.role_assignment_reference,
            modality=changed_capture.modality,
        ),
    )
    with pytest.raises(Phase2ValidationError, match="unknown JD capture"):
        replace(artifact, logic_groups=(changed_capture,)).validate(*kwargs)

    raw = group.to_dict()
    raw["evidence"]["end_offset"] = 999
    raw["logic_group_id"] = generate_candidate_logic_group_id(
        operator=group.operator,
        member_candidate_references=group.member_candidate_references,
        source_reference=group.source_reference,
        capture_reference=group.capture_reference,
        source_content_hash=group.source_content_hash,
        evidence_start=group.evidence.start_offset,
        evidence_end=999,
        role_assignment_reference=group.role_assignment_reference,
        modality=group.modality,
    )
    with pytest.raises(Phase2ValidationError, match="outside capture"):
        replace(artifact, logic_groups=(CandidateLogicGroup.from_dict(raw),)).validate(*kwargs)

    with pytest.raises(Phase2ValidationError, match="minimal excerpt"):
        replace(
            artifact,
            logic_groups=(replace(group, evidence=replace(group.evidence, minimal_excerpt="Wrong excerpt")),),
        ).validate(*kwargs)
    with pytest.raises(Phase2ValidationError, match="Role projection"):
        replace(
            artifact,
            logic_groups=(replace(group, mapped_role_id="machine_learning_engineer"),),
        ).validate(*kwargs)
    with pytest.raises(Phase2ValidationError, match="current Role Assignment"):
        changed = replace(group, role_assignment_reference="assignment_other")
        changed = replace(
            changed,
            logic_group_id=generate_candidate_logic_group_id(
                operator=changed.operator,
                member_candidate_references=changed.member_candidate_references,
                source_reference=changed.source_reference,
                capture_reference=changed.capture_reference,
                source_content_hash=changed.source_content_hash,
                evidence_start=changed.evidence.start_offset,
                evidence_end=changed.evidence.end_offset,
                role_assignment_reference=changed.role_assignment_reference,
                modality=changed.modality,
            ),
        )
        replace(artifact, logic_groups=(changed,)).validate(*kwargs)


def test_inactive_missing_and_cross_provenance_members_are_rejected() -> None:
    ctx, artifact, group = logic_context()
    kwargs = (ctx[1], production_role_catalog(), ctx[4], ctx[2])
    with pytest.raises(Phase2ValidationError, match="unknown Candidate"):
        changed = replace(group, member_candidate_references=(group.member_candidate_references[0], "candidate_missing"))
        changed = replace(changed, logic_group_id=generate_candidate_logic_group_id(
            operator=changed.operator, member_candidate_references=changed.member_candidate_references,
            source_reference=changed.source_reference, capture_reference=changed.capture_reference,
            source_content_hash=changed.source_content_hash, evidence_start=changed.evidence.start_offset,
            evidence_end=changed.evidence.end_offset, role_assignment_reference=changed.role_assignment_reference,
            modality=changed.modality,
        ))
        replace(artifact, logic_groups=(changed,)).validate(*kwargs)
    without_group = replace(artifact, logic_groups=())
    rejected = reject_candidate(
        without_group,
        artifact.candidates[-1].candidate_id,
        reviewer_reference="fixture.candidate_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture reviewer rejected this Candidate.",
        sources=ctx[1],
        catalog=production_role_catalog(),
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    with pytest.raises(Phase2ValidationError, match="inactive Candidate"):
        replace(rejected, logic_groups=(group,)).validate(*kwargs)
    cross_source = replace(artifact.candidates[-1], source_id="source_other")
    with pytest.raises(Phase2ValidationError):
        replace(artifact, candidates=(*artifact.candidates[:-1], cross_source)).validate(*kwargs)


def test_logic_group_cannot_join_candidates_from_different_canonical_jobs() -> None:
    _, sources, v4, _ = contexts(2)
    assignments, contents = confirmed_assignments(sources)
    v5 = migrate_curation_v4_to_v5(
        v4, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    v6 = migrate_curation_v5_to_v6(
        v5, assignments=assignments, sources=sources,
        catalog=production_role_catalog(), capture_contents=contents,
    )
    first, second = v6.candidates[:2]
    exact = contents[first.capture_id][:10]
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
            first.source_content_hash, "Qualifications", 0, 10, exact
        ),
        modality=RequirementModality.REQUIRED,
    )
    with pytest.raises(Phase2ValidationError, match="share source"):
        replace(v6, logic_groups=(group,)).validate(
            sources, production_role_catalog(), assignments, contents
        )


def test_candidate_revision_cannot_leave_stale_logic_member_reference() -> None:
    ctx, artifact, group = logic_context()
    parent = next(
        item for item in artifact.candidates
        if item.candidate_id == group.member_candidate_references[0]
    )
    with pytest.raises(Phase2ValidationError, match="inactive Candidate"):
        revise_candidate(
            artifact,
            parent.candidate_id,
            CandidateRevision(
                proposed_name="Revised fixture capability",
                proposed_description="A revised fixture capability.",
                proposed_category=parent.proposed_category,
                proposed_importance=parent.proposed_importance,
            ),
            reviewer_reference="fixture.candidate_reviewer",
            reviewed_at=NOW,
            decision_reason="Fixture revision must update logic references atomically.",
            sources=ctx[1],
            catalog=production_role_catalog(),
            assignments=ctx[4],
            capture_contents=ctx[2],
        )


def test_provider_boundary_rejects_identity_and_human_fields() -> None:
    _, _, group = logic_context()
    base = {
        "operator": group.operator.value,
        "evidence": group.evidence.to_dict(),
        "modality": group.modality.value,
    }
    context_fields = {
        "member_candidate_references": group.member_candidate_references,
        "source_reference": group.source_reference,
        "capture_reference": group.capture_reference,
        "source_content_hash": group.source_content_hash,
        "role_assignment_reference": group.role_assignment_reference,
        "mapped_role_id": group.mapped_role_id,
        "mapped_specialization_id": group.mapped_specialization_id,
    }
    assert CandidateLogicGroup.from_provider_proposal(base, **context_fields).status == CandidateLogicGroupStatus.PROPOSED
    for forbidden in (
        "logic_group_id", "candidate_id", "role_assignment_reference", "review_id",
        "status", "reviewer", "reviewed_at", "action", "prevalence",
        "catalog_requirement", "gap_status",
    ):
        with pytest.raises(Phase2ValidationError, match="unknown fields"):
            CandidateLogicGroup.from_provider_proposal(
                {**base, forbidden: "provider_value"}, **context_fields
            )


@pytest.mark.parametrize(
    ("operation", "status", "action"),
    [
        (confirm_candidate_logic_group, CandidateLogicGroupStatus.CONFIRMED, CandidateLogicGroupReviewAction.CONFIRM),
        (reject_candidate_logic_group, CandidateLogicGroupStatus.REJECTED, CandidateLogicGroupReviewAction.REJECT),
    ],
)
def test_controlled_logic_group_review(operation, status, action) -> None:
    ctx, artifact, group = logic_context()
    result = operation(artifact, group.logic_group_id, **review_kwargs(ctx))
    assert result.logic_groups[0].status == status
    review = result.logic_group_review_records[0]
    assert review.action == action
    assert review.review_id == generate_candidate_logic_group_review_id(
        logic_group_reference=group.logic_group_id,
        action=action,
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        decision_reason=review.decision_reason,
    )
    with pytest.raises(Phase2ValidationError, match="terminal review"):
        operation(result, group.logic_group_id, **review_kwargs(ctx))


def test_terminal_review_provenance_cannot_be_bypassed() -> None:
    ctx, artifact, group = logic_context()
    kwargs = (ctx[1], production_role_catalog(), ctx[4], ctx[2])
    with pytest.raises(Phase2ValidationError, match="exactly one review"):
        replace(
            artifact,
            logic_groups=(replace(group, status=CandidateLogicGroupStatus.CONFIRMED),),
        ).validate(*kwargs)
    missing_group_review = CandidateLogicGroupReviewRecord.create(
        logic_group_reference="logic_group_missing",
        action=CandidateLogicGroupReviewAction.REJECT,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture review references no real group.",
    )
    with pytest.raises(Phase2ValidationError, match="unknown group"):
        replace(
            artifact,
            logic_group_review_records=(missing_group_review,),
        ).validate(*kwargs)
    review = CandidateLogicGroupReviewRecord.create(
        logic_group_reference=group.logic_group_id,
        action=CandidateLogicGroupReviewAction.REJECT,
        reviewer_reference="fixture.logic_reviewer",
        reviewed_at=NOW,
        decision_reason="Fixture rejected the source logic.",
    )
    with pytest.raises(Phase2ValidationError, match="contradicts"):
        replace(
            artifact,
            logic_groups=(replace(group, status=CandidateLogicGroupStatus.CONFIRMED),),
            logic_group_review_records=(review,),
        ).validate(*kwargs)
    second_review = CandidateLogicGroupReviewRecord.create(
        logic_group_reference=group.logic_group_id,
        action=CandidateLogicGroupReviewAction.REJECT,
        reviewer_reference="fixture.second_logic_reviewer",
        reviewed_at="2026-09-02T12:00:00+00:00",
        decision_reason="A second terminal fixture review is forbidden.",
    )
    with pytest.raises(Phase2ValidationError, match="multiple terminal"):
        replace(
            artifact,
            logic_groups=(replace(group, status=CandidateLogicGroupStatus.REJECTED),),
            logic_group_review_records=(review, second_review),
        ).validate(*kwargs)


def test_schema5_migration_and_schema6_storage_round_trip(tmp_path, monkeypatch) -> None:
    ctx, artifact, _ = logic_context()
    v5 = ctx[6]
    migrated = migrate_curation_v5_to_v6(
        v5, sources=ctx[1], catalog=production_role_catalog(),
        assignments=ctx[4], capture_contents=ctx[2],
    )
    assert migrated.candidates == v5.candidates
    assert migrated.review_records == v5.review_records
    assert migrated.clusters == v5.clusters
    assert migrated.logic_groups == ()
    assert migrated.logic_group_review_records == ()
    assert CurationArtifactV5.from_dict(v5.to_dict()) == v5
    with pytest.raises(Phase2ValidationError, match="schema version"):
        CurationArtifactV6.from_dict(v5.to_dict())

    path = save_curation_artifact(
        artifact, tmp_path / "curation-v6.json", sources=ctx[1],
        catalog=production_role_catalog(), assignments=ctx[4],
        capture_contents=ctx[2],
    )
    copy = save_curation_artifact(
        artifact, tmp_path / "copy.json", sources=ctx[1],
        catalog=production_role_catalog(), assignments=ctx[4],
        capture_contents=ctx[2],
    )
    assert path.read_bytes() == copy.read_bytes()
    assert load_curation_artifact(
        path, sources=ctx[1], catalog=production_role_catalog(),
        assignments=ctx[4], capture_contents=ctx[2],
    ) == artifact
    original = path.read_bytes()
    invalid = replace(
        artifact,
        logic_groups=(replace(artifact.logic_groups[0], status="confirmed"),),
    )
    with pytest.raises(Phase2ValidationError, match="status"):
        save_curation_artifact(
            invalid, path, sources=ctx[1], catalog=production_role_catalog(),
            assignments=ctx[4], capture_contents=ctx[2],
        )
    assert path.read_bytes() == original
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        save_curation_artifact(
            artifact, path, sources=ctx[1], catalog=production_role_catalog(),
            assignments=ctx[4], capture_contents=ctx[2],
        )
    assert path.read_bytes() == original

    tampered = json.loads(original)
    tampered["logic_groups"][0]["source_content_hash"] = "sha256:" + "0" * 64
    group_data = tampered["logic_groups"][0]
    group_data["logic_group_id"] = generate_candidate_logic_group_id(
        operator=RequirementLogicOperator(group_data["operator"]),
        member_candidate_references=group_data["member_candidate_references"],
        source_reference=group_data["source_reference"],
        capture_reference=group_data["capture_reference"],
        source_content_hash=group_data["source_content_hash"],
        evidence_start=group_data["evidence"]["start_offset"],
        evidence_end=group_data["evidence"]["end_offset"],
        role_assignment_reference=group_data["role_assignment_reference"],
        modality=RequirementModality(group_data["modality"]),
    )
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="hash"):
        load_curation_artifact(
            path, sources=ctx[1], catalog=production_role_catalog(),
            assignments=ctx[4], capture_contents=ctx[2],
        )


def test_builder_blocks_schema5_and_logic_flattening() -> None:
    ctx, proposed, group = logic_context()
    samples = (RoleSample(ctx[0]["source_id"], "applied_ai_engineer"),)
    old = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=ctx[1], curation=ctx[6],
        catalog=production_role_catalog(), assignments=ctx[4],
        capture_contents=ctx[2],
    )
    assert old.blockers[0].code == BuildBlockerCode.CURATION_SCHEMA_UPGRADE_REQUIRED
    confirmed = confirm_candidate_logic_group(
        proposed, group.logic_group_id, **review_kwargs(ctx)
    )
    blocked = build_catalog_draft(
        draft_id="fixture_draft", target_catalog_version="1.1.0", created_at=NOW,
        samples=samples, sources=ctx[1], curation=confirmed,
        catalog=production_role_catalog(), assignments=ctx[4],
        capture_contents=ctx[2],
    )
    assert blocked.draft is None
    assert BuildBlockerCode.PRODUCTION_REQUIREMENT_LOGIC_CONTRACT_REQUIRED in {
        item.code for item in blocked.blockers
    }
    assert all(item.code != BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP for item in blocked.blockers)
