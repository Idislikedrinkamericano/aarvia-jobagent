import json
from dataclasses import replace
from copy import deepcopy

import pytest

from aarvia.catalog_build import BuildBlockerCode, RoleSample, build_catalog_draft
from aarvia.curation_workflow import (
    CURATION_SCHEMA_V7_VERSION,
    CandidateLogicGroupV7,
    ClusterReviewAction,
    ClusterReviewRecord,
    CurationArtifactV7,
    LogicGroupResolutionAction,
    LogicGroupRevision,
    LogicGroupResolutionRecord,
    LogicGroupStatusV7,
    ProposedClusterSpec,
    assign_candidates_to_cluster,
    confirm_candidate_logic_group_v7,
    confirm_cluster,
    create_proposed_cluster,
    effective_logic_group_bindings,
    generate_cluster_review_id,
    mark_candidate_normalized,
    mark_candidate_review_pending,
    merge_proposed_clusters,
    migrate_curation_v6_to_v7,
    quarantine_candidate_logic_group,
    reject_cluster,
    reject_logic_group_and_members,
    release_logic_group_members,
    remove_candidates_from_cluster,
    revise_candidate_logic_group,
    split_candidate_logic_group,
    split_proposed_cluster,
)
from aarvia.jd_curation import (
    CandidateLifecycleStatus,
    CandidateLogicGroup,
    CandidateRevision,
    EvidenceLocator,
    ReviewerDecision,
    approve_candidate,
    confirm_candidate_logic_group,
    generate_candidate_v4_id,
    load_curation_artifact,
    reject_candidate,
    reject_candidate_logic_group,
    revise_candidate,
    save_curation_artifact,
)
from aarvia.requirement_logic import RequirementLogicOperator, RequirementModality
from aarvia.role_catalog import (
    Phase2ValidationError,
    RequirementCategory,
    RequirementImportance,
    production_role_catalog,
)
from phase2b_fixtures import NOW
from test_requirement_logic_curation import logic_context


LATER = "2026-09-02T12:00:00+00:00"
REVIEW = {
    "reviewer_reference": "fixture.workflow_reviewer",
    "reviewed_at": NOW,
    "decision_reason": "Fixture human reviewed this workflow decision.",
}


def context_v7():
    ctx, v6, group = logic_context()
    catalog = production_role_catalog()
    v7 = migrate_curation_v6_to_v7(
        v6,
        sources=ctx[1],
        catalog=catalog,
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    common = {
        "sources": ctx[1],
        "catalog": catalog,
        "assignments": ctx[4],
        "capture_contents": ctx[2],
    }
    return ctx, v7, v7.logic_groups[0], common


def approve_all(artifact, common):
    result = artifact
    for candidate in tuple(result.candidates):
        if candidate.status not in {
            CandidateLifecycleStatus.APPROVED,
            CandidateLifecycleStatus.REJECTED,
            CandidateLifecycleStatus.SUPERSEDED,
        }:
            result = approve_candidate(result, candidate.candidate_id, **REVIEW, **common)
    return result


def spec_for(candidate, *candidate_ids, name="Fixture capability"):
    return ProposedClusterSpec(
        normalized_name=name,
        normalized_description=f"Reviewed semantic cluster for {name}.",
        role_id=candidate.mapped_role_id,
        specialization_id=candidate.mapped_specialization_id,
        category=candidate.proposed_category,
        importance=RequirementImportance.CORE,
        candidate_ids=tuple(candidate_ids),
    )


def test_v6_to_v7_migration_is_explicit_and_preserves_proposed_facts() -> None:
    ctx, v6, _ = logic_context()
    v7 = migrate_curation_v6_to_v7(
        v6,
        sources=ctx[1],
        catalog=production_role_catalog(),
        assignments=ctx[4],
        capture_contents=ctx[2],
    )
    assert v7.schema_version == CURATION_SCHEMA_V7_VERSION
    assert v7.candidates == v6.candidates
    assert v7.clusters == v6.clusters
    assert v7.review_records == v6.review_records
    assert v7.cluster_review_records == ()
    assert all(item.status == LogicGroupStatusV7.PROPOSED for item in v7.logic_groups)
    with pytest.raises(Phase2ValidationError):
        CurationArtifactV7.from_dict({**v7.to_dict(), "schema_version": 6})


def test_candidate_review_is_independent_from_cluster() -> None:
    _, artifact, _, common = context_v7()
    unclustered = next(item for item in artifact.candidates if item.cluster_id is None)
    approved = approve_candidate(artifact, unclustered.candidate_id, **REVIEW, **common)
    assert next(item for item in approved.candidates if item.candidate_id == unclustered.candidate_id).status == CandidateLifecycleStatus.APPROVED
    extra_name = "Independent fixture capability"
    extra = replace(
        unclustered,
        candidate_id=generate_candidate_v4_id(
            unclustered.source_id, unclustered.capture_id,
            unclustered.evidence, extra_name,
        ),
        proposed_name=extra_name,
        proposed_description=extra_name,
    )
    with_extra = replace(artifact, candidates=(*artifact.candidates, extra))
    with_extra.validate(**common)
    rejected = reject_candidate(with_extra, extra.candidate_id, **REVIEW, **common)
    assert next(item for item in rejected.candidates if item.candidate_id == extra.candidate_id).status == CandidateLifecycleStatus.REJECTED


def test_create_assign_remove_cluster_synchronizes_both_directions() -> None:
    _, artifact, _, common = context_v7()
    candidates = [item for item in artifact.candidates if item.cluster_id is None]
    candidate = candidates[0]
    created = create_proposed_cluster(artifact, spec_for(candidate, candidate.candidate_id), **common)
    cluster = next(item for item in created.clusters if candidate.candidate_id in item.candidate_ids)
    assert next(item for item in created.candidates if item.candidate_id == candidate.candidate_id).cluster_id == cluster.cluster_id
    removed = remove_candidates_from_cluster(created, cluster.cluster_id, (candidate.candidate_id,), **common)
    assert all(candidate.candidate_id not in item.candidate_ids for item in removed.clusters)
    assert next(item for item in removed.candidates if item.candidate_id == candidate.candidate_id).cluster_id is None


def test_assign_rebuilds_deterministic_cluster_id_for_new_assignment() -> None:
    _, artifact, _, common = context_v7()
    unclustered = next(item for item in artifact.candidates if item.cluster_id is None)
    original = artifact.clusters[0]
    assigned = assign_candidates_to_cluster(artifact, original.cluster_id, (unclustered.candidate_id,), **common)
    rebuilt = next(item for item in assigned.clusters if unclustered.candidate_id in item.candidate_ids)
    assert rebuilt.candidate_ids == tuple(sorted((artifact.candidates[0].candidate_id, unclustered.candidate_id)))
    assert all(next(item for item in assigned.candidates if item.candidate_id == cid).cluster_id == rebuilt.cluster_id for cid in rebuilt.candidate_ids)


def test_merge_and_split_proposed_clusters_are_atomic() -> None:
    _, artifact, _, common = context_v7()
    unclustered = next(item for item in artifact.candidates if item.cluster_id is None)
    second = create_proposed_cluster(artifact, spec_for(unclustered, unclustered.candidate_id, name="Second capability"), **common)
    ids = tuple(item.cluster_id for item in second.clusters)
    all_ids = tuple(item.candidate_id for item in second.candidates)
    merged = merge_proposed_clusters(second, ids, spec_for(second.candidates[0], *all_ids, name="Merged capability"), **common)
    merged_cluster = merged.clusters[0]
    parts = tuple(spec_for(next(item for item in merged.candidates if item.candidate_id == cid), cid, name=f"Part {index}") for index, cid in enumerate(merged_cluster.candidate_ids, 1))
    split = split_proposed_cluster(merged, merged_cluster.cluster_id, parts, **common)
    assert len(split.clusters) == 2
    assert {cid for item in split.clusters for cid in item.candidate_ids} == set(all_ids)


def test_confirm_cluster_requires_approved_leaves_and_review_snapshot() -> None:
    _, artifact, _, common = context_v7()
    cluster = artifact.clusters[0]
    with pytest.raises(Phase2ValidationError, match="approved leaf"):
        confirm_cluster(artifact, cluster.cluster_id, **REVIEW, **common)
    approved = approve_all(artifact, common)
    confirmed = confirm_cluster(approved, cluster.cluster_id, **REVIEW, **common)
    reviewed_cluster = next(item for item in confirmed.clusters if item.cluster_id == cluster.cluster_id)
    assert reviewed_cluster.status.value == "confirmed"
    assert len(confirmed.cluster_review_records) == 1
    tampered = deepcopy(confirmed.to_dict())
    tampered["clusters"][0]["normalized_name"] = "Tampered"
    with pytest.raises(Phase2ValidationError):
        CurationArtifactV7.from_dict(tampered).validate(**common)


def test_rejected_cluster_does_not_prevent_candidate_fact_review() -> None:
    _, artifact, _, common = context_v7()
    cluster = artifact.clusters[0]
    rejected = reject_cluster(artifact, cluster.cluster_id, **REVIEW, **common)
    candidate = next(item for item in rejected.candidates if item.candidate_id in cluster.candidate_ids)
    approved = approve_candidate(rejected, candidate.candidate_id, **REVIEW, **common)
    assert next(item for item in approved.candidates if item.candidate_id == candidate.candidate_id).status == CandidateLifecycleStatus.APPROVED


def test_confirm_and_quarantine_logic_group_have_distinct_bindings() -> None:
    _, artifact, group, common = context_v7()
    confirmed = confirm_candidate_logic_group_v7(artifact, group.logic_group_id, **REVIEW, **common)
    assert effective_logic_group_bindings(confirmed)[0].status == LogicGroupStatusV7.CONFIRMED
    quarantined = quarantine_candidate_logic_group(artifact, group.logic_group_id, **REVIEW, **common)
    assert effective_logic_group_bindings(quarantined)[0].status == LogicGroupStatusV7.QUARANTINED


def test_release_members_removes_current_binding() -> None:
    _, artifact, group, common = context_v7()
    released = release_logic_group_members(artifact, group.logic_group_id, **REVIEW, **common)
    assert effective_logic_group_bindings(released) == ()
    assert released.logic_groups[0].status == LogicGroupStatusV7.SUPERSEDED


def test_reject_members_is_all_or_nothing_and_creates_candidate_reviews() -> None:
    _, artifact, group, common = context_v7()
    rejected = reject_logic_group_and_members(artifact, group.logic_group_id, **REVIEW, **common)
    members = [next(item for item in rejected.candidates if item.candidate_id == cid) for cid in group.member_candidate_references]
    assert all(item.status == CandidateLifecycleStatus.REJECTED for item in members)
    assert len(rejected.review_records) == len(artifact.review_records) + len(members)
    assert rejected.logic_groups[0].status == LogicGroupStatusV7.REJECTED
    assert effective_logic_group_bindings(rejected) == ()


def test_reject_members_failure_preserves_original() -> None:
    _, artifact, group, common = context_v7()
    approved = approve_candidate(artifact, group.member_candidate_references[0], **REVIEW, **common)
    with pytest.raises(Phase2ValidationError, match="not independently reviewable"):
        reject_logic_group_and_members(approved, group.logic_group_id, **REVIEW, **common)
    assert artifact.logic_groups[0].status == LogicGroupStatusV7.PROPOSED


def test_revise_logic_group_preserves_lineage_and_successor_binding() -> None:
    _, artifact, group, common = context_v7()
    revision = LogicGroupRevision(RequirementLogicOperator.ALL_OF, group.member_candidate_references, group.evidence, RequirementModality.PREFERRED)
    revised = revise_candidate_logic_group(artifact, group.logic_group_id, revision, **REVIEW, **common)
    assert revised.logic_groups[0].status == LogicGroupStatusV7.SUPERSEDED
    assert len(effective_logic_group_bindings(revised)) == 1
    assert effective_logic_group_bindings(revised)[0].operator == RequirementLogicOperator.ALL_OF


def test_split_logic_group_requires_complete_nonoverlapping_members() -> None:
    _, artifact, group, common = context_v7()
    first = artifact.candidates[0]
    extras = []
    for index in (3, 4):
        name = f"Fixture capability {index}"
        extras.append(replace(first, candidate_id=generate_candidate_v4_id(first.source_id, first.capture_id, first.evidence, name), proposed_name=name, proposed_description=name, status=CandidateLifecycleStatus.NORMALIZED, cluster_id=None, reviewer_decision=ReviewerDecision.PENDING, decision_reason=None, reviewed_at=None))
    expanded_group = CandidateLogicGroupV7.from_dict({**group.to_dict(), "logic_group_id": CandidateLogicGroup.create_proposed(operator=group.operator, member_candidate_references=(*group.member_candidate_references, *(item.candidate_id for item in extras)), source_reference=group.source_reference, capture_reference=group.capture_reference, source_content_hash=group.source_content_hash, role_assignment_reference=group.role_assignment_reference, mapped_role_id=group.mapped_role_id, mapped_specialization_id=group.mapped_specialization_id, evidence=group.evidence, modality=group.modality).logic_group_id, "member_candidate_references": sorted((*group.member_candidate_references, *(item.candidate_id for item in extras)))})
    expanded = replace(artifact, candidates=(*artifact.candidates, *extras), logic_groups=(expanded_group,))
    expanded.validate(**common)
    members = expanded_group.member_candidate_references
    revisions = (
        LogicGroupRevision(RequirementLogicOperator.ANY_OF, members[:2], expanded_group.evidence, expanded_group.modality),
        LogicGroupRevision(RequirementLogicOperator.ALL_OF, members[2:], expanded_group.evidence, expanded_group.modality),
    )
    result = split_candidate_logic_group(expanded, expanded_group.logic_group_id, revisions, **REVIEW, **common)
    assert len(effective_logic_group_bindings(result)) == 2
    with pytest.raises(Phase2ValidationError, match="duplicate successors|partial or overlapping"):
        split_candidate_logic_group(expanded, expanded_group.logic_group_id, (revisions[0], revisions[0]), **REVIEW, **common)


def test_storage_round_trip_and_tampering(tmp_path) -> None:
    _, artifact, _, common = context_v7()
    path = save_curation_artifact(artifact, tmp_path / "v7.json", **common)
    assert load_curation_artifact(path, **common) == artifact
    raw = deepcopy(artifact.to_dict())
    raw["cluster_review_records"] = [{"injected": True}]
    path.write_text(__import__("json").dumps(raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError):
        load_curation_artifact(path, **common)


def test_terminal_v6_cluster_migration_is_rejected() -> None:
    ctx, v6, _ = logic_context()
    cluster = replace(v6.clusters[0], status=v6.clusters[0].status.CONFIRMED, reviewer_decision=ReviewerDecision.APPROVE, decision_reason="Legacy fields only.", reviewed_at=NOW)
    terminal = replace(v6, clusters=(cluster,))
    terminal.validate(ctx[1], production_role_catalog(), ctx[4], ctx[2])
    with pytest.raises(Phase2ValidationError, match="trusted Cluster Review"):
        migrate_curation_v6_to_v7(terminal, sources=ctx[1], catalog=production_role_catalog(), assignments=ctx[4], capture_contents=ctx[2])


def test_candidate_lifecycle_transitions_have_controlled_meaning() -> None:
    _, artifact, _, common = context_v7()
    candidate = next(item for item in artifact.candidates if item.cluster_id is None)
    artifact = replace(
        artifact,
        candidates=tuple(
            replace(item, status=CandidateLifecycleStatus.CANDIDATE_EXTRACTED)
            if item.candidate_id == candidate.candidate_id
            else item
            for item in artifact.candidates
        ),
    )
    artifact.validate(**common)
    normalized = mark_candidate_normalized(
        artifact, candidate.candidate_id, **common
    )
    assert next(
        item for item in normalized.candidates
        if item.candidate_id == candidate.candidate_id
    ).status == CandidateLifecycleStatus.NORMALIZED
    clustered = create_proposed_cluster(
        normalized,
        spec_for(candidate, candidate.candidate_id, name="Lifecycle capability"),
        **common,
    )
    clustered_candidate = next(
        item for item in clustered.candidates
        if item.candidate_id == candidate.candidate_id
    )
    assert clustered_candidate.status == CandidateLifecycleStatus.CLUSTERED
    pending = mark_candidate_review_pending(
        clustered, candidate.candidate_id, **common
    )
    assert next(
        item for item in pending.candidates
        if item.candidate_id == candidate.candidate_id
    ).status == CandidateLifecycleStatus.REVIEW_PENDING


def test_revise_detaches_parent_from_proposed_cluster() -> None:
    _, artifact, group, common = context_v7()
    released = release_logic_group_members(
        artifact, group.logic_group_id, **REVIEW, **common
    )
    parent = next(item for item in released.candidates if item.cluster_id is not None)
    revision = CandidateRevision(
        proposed_name="Revised fixture capability",
        proposed_description="Revised fixture capability.",
        proposed_category=parent.proposed_category,
        proposed_importance=parent.proposed_importance,
        evidence=parent.evidence,
    )
    revised = revise_candidate(
        released, parent.candidate_id, revision, **REVIEW, **common
    )
    old = next(
        item for item in revised.candidates if item.candidate_id == parent.candidate_id
    )
    successor = revised.candidates[-1]
    assert old.status == CandidateLifecycleStatus.SUPERSEDED
    assert old.cluster_id is None
    assert successor.status == CandidateLifecycleStatus.APPROVED
    assert successor.cluster_id is None
    assert all(parent.candidate_id not in item.candidate_ids for item in revised.clusters)


def test_rejected_cluster_member_can_be_reassigned_without_changing_review(
    tmp_path,
) -> None:
    _, artifact, _, common = context_v7()
    cluster = artifact.clusters[0]
    rejected = reject_cluster(artifact, cluster.cluster_id, **REVIEW, **common)
    candidate = next(
        item for item in rejected.candidates if item.candidate_id in cluster.candidate_ids
    )
    reassigned = create_proposed_cluster(
        rejected,
        spec_for(candidate, candidate.candidate_id, name="Replacement capability"),
        **common,
    )
    reassigned_candidate = next(
        item for item in reassigned.candidates
        if item.candidate_id == candidate.candidate_id
    )
    assert reassigned_candidate.cluster_id != cluster.cluster_id
    approved = approve_candidate(
        reassigned, candidate.candidate_id, **REVIEW, **common
    )
    confirmed = confirm_cluster(
        approved, reassigned_candidate.cluster_id, **REVIEW, **common
    )
    confirmed.validate(**common)
    assert len(confirmed.cluster_review_records) == 2
    assert next(
        item for item in confirmed.candidates
        if item.candidate_id == candidate.candidate_id
    ).cluster_id == reassigned_candidate.cluster_id
    assert candidate.candidate_id in next(
        item for item in confirmed.clusters
        if item.cluster_id == cluster.cluster_id
    ).candidate_ids

    path = tmp_path / "reassigned-curation.json"
    save_curation_artifact(confirmed, path, **common)
    assert load_curation_artifact(path, **common) == confirmed

    tampered = confirmed.to_dict()
    candidate_data = next(
        item for item in tampered["candidates"]
        if item["candidate_id"] == candidate.candidate_id
    )
    candidate_data["cluster_id"] = cluster.cluster_id
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="inconsistent Candidate reference"):
        load_curation_artifact(path, **common)


def test_cluster_review_id_and_snapshot_are_deterministic() -> None:
    _, artifact, _, common = context_v7()
    approved = approve_all(artifact, common)
    cluster = approved.clusters[0]
    confirmed = confirm_cluster(approved, cluster.cluster_id, **REVIEW, **common)
    review = confirmed.cluster_review_records[0]
    assert review.review_id == generate_cluster_review_id(
        cluster_reference=review.cluster_reference,
        action=review.action,
        cluster_snapshot_hash=review.cluster_snapshot_hash,
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        decision_reason=review.decision_reason,
    )
    forged = replace(
        review, cluster_snapshot_hash="sha256:" + "0" * 64
    )
    with pytest.raises(Phase2ValidationError, match="generated by Aarvia"):
        ClusterReviewRecord.from_dict(forged.to_dict())


def test_logic_successor_requires_machine_readable_predecessor() -> None:
    _, artifact, group, common = context_v7()
    revised = revise_candidate_logic_group(
        artifact,
        group.logic_group_id,
        LogicGroupRevision(
            RequirementLogicOperator.ALL_OF,
            group.member_candidate_references,
            group.evidence,
            group.modality,
        ),
        **REVIEW,
        **common,
    )
    successor = effective_logic_group_bindings(revised)[0]
    assert successor.predecessor_group_reference == group.logic_group_id
    orphan = replace(successor, predecessor_group_reference=None)
    tampered = replace(
        revised,
        logic_groups=tuple(
            orphan if item.logic_group_id == successor.logic_group_id else item
            for item in revised.logic_groups
        ),
    )
    with pytest.raises(Phase2ValidationError, match="predecessor lineage"):
        tampered.validate(**common)


def test_quarantine_can_be_followed_by_later_release_only() -> None:
    _, artifact, group, common = context_v7()
    quarantined = quarantine_candidate_logic_group(
        artifact, group.logic_group_id, **REVIEW, **common
    )
    released = release_logic_group_members(
        quarantined,
        group.logic_group_id,
        reviewer_reference="fixture.workflow_reviewer",
        reviewed_at=LATER,
        decision_reason="Fixture human released the atomic members.",
        **common,
    )
    assert effective_logic_group_bindings(released) == ()
    assert len(released.logic_group_review_records) == 2
    with pytest.raises(Phase2ValidationError, match="invalid resolution history"):
        release_logic_group_members(
            quarantined,
            group.logic_group_id,
            reviewer_reference="fixture.workflow_reviewer",
            reviewed_at="2026-08-31T12:00:00+00:00",
            decision_reason="Fixture invalid ordering.",
            **common,
        )


def test_storage_replacement_failure_preserves_existing_v7(
    tmp_path, monkeypatch
) -> None:
    _, artifact, _, common = context_v7()
    path = save_curation_artifact(artifact, tmp_path / "curation.json", **common)
    original = path.read_bytes()
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        save_curation_artifact(artifact, path, **common)
    assert path.read_bytes() == original


def test_direct_terminal_cluster_without_review_is_rejected() -> None:
    _, artifact, _, common = context_v7()
    approved = approve_all(artifact, common)
    cluster = approved.clusters[0]
    forged = replace(
        cluster,
        status=cluster.status.CONFIRMED,
        reviewer_decision=ReviewerDecision.APPROVE,
        decision_reason="Forged terminal state.",
        reviewed_at=NOW,
    )
    bypass = replace(approved, clusters=(forged, *approved.clusters[1:]))
    with pytest.raises(Phase2ValidationError, match="exactly one Cluster Review"):
        bypass.validate(**common)


def test_candidate_cannot_belong_to_two_current_clusters() -> None:
    _, artifact, _, common = context_v7()
    candidate = next(item for item in artifact.candidates if item.cluster_id is None)
    first = create_proposed_cluster(
        artifact,
        spec_for(candidate, candidate.candidate_id, name="First semantic cluster"),
        **common,
    )
    with pytest.raises(Phase2ValidationError, match="already belongs"):
        create_proposed_cluster(
            first,
            spec_for(candidate, candidate.candidate_id, name="Second semantic cluster"),
            **common,
        )


def test_v6_terminal_logic_migration_requires_unambiguous_resolution() -> None:
    ctx, v6, group = logic_context()
    common = {
        "sources": ctx[1],
        "catalog": production_role_catalog(),
        "assignments": ctx[4],
        "capture_contents": ctx[2],
    }
    confirmed_v6 = confirm_candidate_logic_group(
        v6, group.logic_group_id, **REVIEW, **common
    )
    confirmed_v7 = migrate_curation_v6_to_v7(confirmed_v6, **common)
    assert confirmed_v7.logic_groups[0].status == LogicGroupStatusV7.CONFIRMED

    rejected_v6 = reject_candidate_logic_group(
        v6, group.logic_group_id, **REVIEW, **common
    )
    with pytest.raises(Phase2ValidationError, match="explicit non-fabricated"):
        migrate_curation_v6_to_v7(rejected_v6, **common)
    quarantined = migrate_curation_v6_to_v7(
        rejected_v6,
        rejected_group_resolutions={
            group.logic_group_id: LogicGroupResolutionAction.QUARANTINE
        },
        **common,
    )
    assert quarantined.logic_groups[0].status == LogicGroupStatusV7.QUARANTINED


def test_logic_lineage_cycle_is_rejected() -> None:
    _, artifact, group, common = context_v7()
    revised = revise_candidate_logic_group(
        artifact,
        group.logic_group_id,
        LogicGroupRevision(
            RequirementLogicOperator.ALL_OF,
            group.member_candidate_references,
            group.evidence,
            group.modality,
        ),
        **REVIEW,
        **common,
    )
    child = effective_logic_group_bindings(revised)[0]
    child_review = LogicGroupResolutionRecord.create(
        logic_group_reference=child.logic_group_id,
        action=LogicGroupResolutionAction.REVISE,
        successor_group_references=(group.logic_group_id,),
        reviewer_reference="fixture.workflow_reviewer",
        reviewed_at=LATER,
        decision_reason="Fixture cyclic lineage.",
    )
    cyclic = replace(
        revised,
        logic_groups=tuple(
            replace(
                item,
                status=LogicGroupStatusV7.SUPERSEDED,
                predecessor_group_reference=child.logic_group_id,
            )
            if item.logic_group_id == group.logic_group_id
            else replace(item, status=LogicGroupStatusV7.SUPERSEDED)
            for item in revised.logic_groups
        ),
        logic_group_review_records=(
            *revised.logic_group_review_records,
            child_review,
        ),
    )
    with pytest.raises(Phase2ValidationError, match="cycle"):
        cyclic.validate(**common)


def test_builder_blocks_unclustered_approved_candidate() -> None:
    ctx, artifact, group, common = context_v7()
    candidate = next(item for item in artifact.candidates if item.cluster_id is None)
    approved = approve_candidate(
        artifact, candidate.candidate_id, **REVIEW, **common
    )
    result = build_catalog_draft(
        draft_id="fixture_workflow_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=(RoleSample(ctx[0]["source_id"], candidate.mapped_role_id),),
        sources=common["sources"],
        curation=approved,
        catalog=common["catalog"],
        assignments=common["assignments"],
        capture_contents=common["capture_contents"],
    )
    codes = {item.code for item in result.blockers}
    assert BuildBlockerCode.APPROVED_CANDIDATE_UNCLUSTERED in codes
    assert BuildBlockerCode.UNCONFIRMED_LOGIC_GROUP in codes


def test_rejected_cluster_is_history_not_a_publication_requirement() -> None:
    ctx, artifact, group, common = context_v7()
    released = release_logic_group_members(
        artifact, group.logic_group_id, **REVIEW, **common
    )
    cluster = released.clusters[0]
    rejected = reject_cluster(released, cluster.cluster_id, **REVIEW, **common)
    candidate = next(
        item for item in rejected.candidates if item.candidate_id in cluster.candidate_ids
    )
    approved = approve_candidate(
        rejected, candidate.candidate_id, **REVIEW, **common
    )
    result = build_catalog_draft(
        draft_id="fixture_workflow_draft",
        target_catalog_version="1.1.0",
        created_at=NOW,
        samples=(RoleSample(ctx[0]["source_id"], candidate.mapped_role_id),),
        sources=common["sources"],
        curation=approved,
        catalog=common["catalog"],
        assignments=common["assignments"],
        capture_contents=common["capture_contents"],
    )
    codes = {item.code for item in result.blockers}
    assert result.draft is None
    assert all(item.value != "rejected_cluster" for item in codes)
    assert BuildBlockerCode.APPROVED_CANDIDATE_UNCLUSTERED in codes
    assert BuildBlockerCode.NO_APPROVED_EVIDENCE in codes
