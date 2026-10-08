from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.career_gap_analysis import GapClassification
from aarvia.career_gap_analysis import build_career_gap_analysis
from aarvia.career_direction import DecisionStatus, SelectedRole, create_user_role_decision
from aarvia.capability_rubric import EvidenceClass
from aarvia.evidence_bank import (
    CapabilitySupportState,
    EvidenceBank,
    EvidenceNeedType,
    ResumeEvidenceUse,
    _summary,
    _source_record,
    build_evidence_bank,
    generate_capability_link_id,
    generate_evidence_bank_id,
    load_evidence_bank,
    save_evidence_bank,
    select_resume_evidence_items,
)
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
)
from aarvia.evidence_group_allocation_review import (
    AllocationReviewDecision,
    AllocationReviewerType,
    create_evidence_group_allocation_review_artifact,
)
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.role_catalog import Phase2ValidationError
from aarvia.role_recommendation import build_role_recommendation
from test_career_gap_analysis_v2 import _context
from test_recommendation_ranking_v7 import _profile_with_project_evidence
from test_evidence_group_allocation_review import unresolved_mapping


NOW = "2026-10-06T00:00:00+00:00"
LATER = "2026-10-06T01:00:00+00:00"


def _bank_context():
    context = _context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap = context
    bank = build_evidence_bank(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap,
        evidence_reviews=reviews,
        created_at=NOW,
    )
    return (*context, bank)


def _validate(bank, context, *, superseded_bank=None):
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, _ = context
    bank.validate(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap,
        evidence_reviews=reviews,
        superseded_bank=superseded_bank,
    )


def _reidentify(bank: EvidenceBank, **changes) -> EvidenceBank:
    provisional = replace(bank, **changes, evidence_bank_id="evidence_bank_pending")
    return replace(provisional, evidence_bank_id=generate_evidence_bank_id(provisional))


def test_schema_one_round_trip_and_complete_provenance():
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank = context
    assert bank.schema == "aarvia.evidence_bank"
    assert bank.schema_version == 1
    assert bank.profile_fingerprint == recommendation.profile_fingerprint
    assert bank.mapping_artifact_id == reviews.mapping_artifact_id
    assert bank.binding_review_artifact_id == reviews.review_artifact_id
    assert bank.allocation_review_artifact_id is None
    assert bank.recommendation_set_id == recommendation.recommendation_set_id
    assert bank.recommendation_schema_version == 7
    assert bank.decision_id == decision.decision_id
    assert bank.decision_schema_version == 2
    assert bank.gap_analysis_id == gap.analysis_id
    assert bank.gap_analysis_schema_version == 2
    assert bank.rubric_version == rubric.rubric_version
    assert bank.catalog_version == catalog.catalog_version
    assert EvidenceBank.from_dict(bank.to_dict()) == bank
    _validate(bank, context)


def test_unknown_creates_needs_only_and_explicit_gap_is_distinct():
    *_, gap, bank = _bank_context()
    unknown_dimension_ids = {
        item.dimension_id
        for role in (gap.primary_role_analysis, *gap.secondary_role_analyses)
        for item in role.dimensions
        if item.gap_classification == GapClassification.UNKNOWN_EVIDENCE
    }
    assert unknown_dimension_ids
    assert all(link.dimension_id not in unknown_dimension_ids for link in bank.links)
    assert unknown_dimension_ids <= {
        need.dimension_id
        for need in bank.needs
        if need.need_type == EvidenceNeedType.CONFIRM_UNKNOWN_EVIDENCE
    }
    assert not any(
        need.need_type == EvidenceNeedType.DEVELOP_CAPABILITY for need in bank.needs
    )
    assert bank.summary.explicit_development_need_count == 0


def test_items_are_real_profile_facts_and_never_gap_actions_or_future_plans():
    profile, *_, gap, bank = _bank_context()
    profile_data = profile.to_dict()
    forbidden_text = {
        follow_up.prompt for follow_up in gap.follow_ups
    } | {
        item.next_action
        for role in (gap.primary_role_analysis, *gap.secondary_role_analyses)
        for item in role.dimensions
    }
    for item in bank.items:
        value = profile_data
        for component in item.canonical_profile_path.replace("]", "").replace("[", ".").split("."):
            value = value[int(component)] if component.isdigit() else value[component]
        assert value[item.start_offset:item.end_offset] == item.exact_excerpt
        assert item.exact_excerpt not in forbidden_text
    assert all(not need.is_profile_fact and not need.resume_eligible for need in bank.needs)


def test_structural_and_provisional_evidence_cannot_become_confirmed_capability():
    *_, bank = _bank_context()
    for link in bank.links:
        if link.trust_level.value == "structural_only":
            assert link.support_state == CapabilitySupportState.STRUCTURAL_FACT_ONLY
        if link.review_state.value != "confirmed":
            assert link.support_state != CapabilitySupportState.CONFIRMED_CAPABILITY_SUPPORT


def test_one_item_per_evidence_fingerprint_and_links_reuse_items():
    *_, bank = _bank_context()
    assert len(bank.items) == len({item.evidence_fingerprint for item in bank.items})
    assert len(bank.items) == len({item.evidence_item_id for item in bank.items})
    assert {link.evidence_item_id for link in bank.links} <= {
        item.evidence_item_id for item in bank.items
    }
    raw = bank.to_dict()
    raw["items"].append(raw["items"][0])
    with pytest.raises(Phase2ValidationError):
        EvidenceBank.from_dict(raw)


def test_nonoverlapping_atomic_evidence_shares_one_parent_source_record():
    profile = _profile_with_project_evidence()
    data = profile.to_dict()
    data["experience_overview"][0]["short_factual_summary"] = (
        "Implemented a deterministic feature pipeline. "
        "Evaluated model behavior with repeatable tests."
    )
    profile = CareerProfile.from_dict(data)
    _, rubric, catalog, *_ = _context()
    spans = tuple(
        span for span in canonical_evidence_span_inventory(profile)
        if span.path == "experience_overview[0].short_factual_summary"
    )
    assert len(spans) == 2
    dimensions = tuple(
        item for item in rubric.role_dimensions("applied_ai_engineer")
        if EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    )[:2]
    mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        {
            "mappings": [
                {
                    "role_id": dimension.role_id,
                    "dimension_id": dimension.dimension_id,
                    "criterion_id": dimension.criterion_ids[0],
                    "span_id": span.span_id,
                    "proposed_binding_type": "direct",
                    "provider_confidence": "high",
                }
                for dimension, span in zip(dimensions, spans, strict=True)
            ],
            "directional_signals": [], "constraints": [], "conflict_warnings": [],
        },
        profile=profile, rubric=rubric, catalog=catalog,
        provider_name="fixture", provider_model="fixture-model",
        attempt_number=1, response_hash_reference="sha256:" + "6" * 64,
        evidence_spans=canonical_evidence_span_inventory(profile),
        mapping_schema_version=5,
    )
    assert len(mapping.mappings) == 2
    records = tuple(_source_record(profile, binding) for binding in mapping.mappings)
    assert records[0].source_record_id == records[1].source_record_id
    assert records[0].canonical_profile_path == "experience_overview[0]"
    first, second = sorted(
        (item.atomic_evidence for item in mapping.mappings),
        key=lambda item: item.start_offset,
    )
    assert first.end_offset <= second.start_offset


def test_same_locator_across_roles_is_one_item_with_multiple_links():
    profile, rubric, catalog, *_ = _context()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path.endswith("short_factual_summary")
    )
    dimensions = tuple(
        next(
            item for item in rubric.role_dimensions(role_id)
            if EvidenceClass.PROJECT_SUMMARY
            in item.evidence_support_policy.allowed_evidence_classes
        )
        for role_id in ("machine_learning_engineer", "applied_ai_engineer")
    )
    mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        {
            "mappings": [
                {
                    "role_id": dimension.role_id,
                    "dimension_id": dimension.dimension_id,
                    "criterion_id": dimension.criterion_ids[0],
                    "span_id": span.span_id,
                    "proposed_binding_type": "direct",
                    "provider_confidence": "high",
                }
                for dimension in dimensions
            ],
            "directional_signals": [], "constraints": [], "conflict_warnings": [],
        },
        profile=profile, rubric=rubric, catalog=catalog,
        provider_name="fixture", provider_model="fixture-model", attempt_number=1,
        response_hash_reference="sha256:" + "5" * 64,
        evidence_spans=canonical_evidence_span_inventory(profile),
        mapping_schema_version=5,
    )
    reviews = create_evidence_binding_review_artifact(
        profile=profile, rubric=rubric, mapping=mapping, catalog=catalog,
        decisions={
            item.binding_id: (
                BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                NOW,
            )
            for item in mapping.mappings
        },
    )
    recommendation = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric,
        catalog=catalog, evidence_reviews=reviews, created_at=NOW,
    )
    decision = create_user_role_decision(
        status=DecisionStatus.CONFIRMED, catalog=catalog, rubric=rubric,
        profile=profile, recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        secondary_roles=(SelectedRole("applied_ai_engineer"),),
        created_at=NOW, updated_at=NOW,
    )
    gap = build_career_gap_analysis(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        evidence_reviews=reviews, created_at=NOW,
    )
    bank = build_evidence_bank(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_reviews=reviews, created_at=NOW,
    )
    matching_links = tuple(
        item for item in bank.links
        if item.binding_id in {binding.binding_id for binding in mapping.mappings}
    )
    assert len(matching_links) == 2
    assert len({item.evidence_item_id for item in matching_links}) == 1
    assert sum(
        item.evidence_item_id == matching_links[0].evidence_item_id
        for item in bank.items
    ) == 1


@pytest.mark.parametrize(
    ("decision_value", "expected_review_state"),
    [
        (BindingReviewDecision.REJECTED, None),
        (BindingReviewDecision.DEFERRED, "deferred"),
    ],
)
def test_rejected_is_excluded_and_deferred_remains_nonconfirmed(
    decision_value, expected_review_state
):
    profile, rubric, catalog, mapping, _, _, _, _ = _context()
    target = mapping.mappings[0]
    reviews = create_evidence_binding_review_artifact(
        profile=profile, rubric=rubric, mapping=mapping, catalog=catalog,
        decisions={
            item.binding_id: (
                decision_value if item.binding_id == target.binding_id
                else BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                NOW,
            )
            for item in mapping.mappings
        },
    )
    recommendation = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric,
        catalog=catalog, created_at=NOW, evidence_reviews=reviews,
    )
    direction = create_user_role_decision(
        status=DecisionStatus.CONFIRMED, catalog=catalog, rubric=rubric,
        profile=profile, recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        secondary_roles=(SelectedRole("applied_ai_engineer"),),
        created_at=NOW, updated_at=NOW,
    )
    gap = build_career_gap_analysis(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=direction,
        evidence_reviews=reviews, created_at=NOW,
    )
    bank = build_evidence_bank(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=direction, gap_analysis=gap,
        evidence_reviews=reviews, created_at=NOW,
    )
    links = tuple(link for link in bank.links if link.binding_id == target.binding_id)
    if expected_review_state is None:
        assert links == ()
    else:
        assert links and links[0].review_state.value == expected_review_state
        assert links[0].support_state != CapabilitySupportState.CONFIRMED_CAPABILITY_SUPPORT
        item = next(value for value in bank.items if value.evidence_item_id == links[0].evidence_item_id)
        assert item.resume_use == ResumeEvidenceUse.NOT_ELIGIBLE


def test_output_order_ids_serialization_and_summary_are_deterministic():
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank = context
    second = build_evidence_bank(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap,
        evidence_reviews=reviews,
        created_at=NOW,
    )
    assert bank == second
    assert json.dumps(bank.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )
    for values, key in (
        (bank.sources, lambda item: item.source_record_id),
        (bank.items, lambda item: item.evidence_item_id),
        (bank.links, lambda item: item.link_id),
        (bank.needs, lambda item: item.need_id),
    ):
        assert list(map(key, values)) == sorted(map(key, values))


@pytest.mark.parametrize("field", ["exact_excerpt", "start_offset", "end_offset"])
def test_typed_load_rejects_locator_tampering(tmp_path, field):
    context = _bank_context()
    *_, bank = context
    item = bank.items[0]
    changes = {
        field: (
            "Invented result 9000"
            if field == "exact_excerpt"
            else getattr(item, field) + 1
        )
    }
    forged_item = replace(item, **changes)
    items = tuple(forged_item if value == item else value for value in bank.items)
    forged = _reidentify(
        bank,
        items=items,
        summary=_summary(items, bank.links, bank.needs, bank.sources),
    )
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(forged.to_dict()), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="deterministic source"):
        load_evidence_bank(
            path,
            profile=context[0], rubric=context[1], catalog=context[2],
            mapping=context[3], evidence_reviews=context[4],
            recommendation=context[5], decision=context[6], gap_analysis=context[7],
        )


def test_typed_load_rejects_eligibility_support_priority_and_reference_tampering(tmp_path):
    context = _bank_context()
    *_, bank = context
    cases = []

    item = bank.items[0]
    forged_item = replace(
        item,
        resume_eligible=False,
        resume_use=ResumeEvidenceUse.NOT_ELIGIBLE,
    )
    items = tuple(forged_item if value == item else value for value in bank.items)
    cases.append(_reidentify(bank, items=items, summary=_summary(items, bank.links, bank.needs, bank.sources)))

    link = bank.links[0]
    changed_link = replace(link, support_state=CapabilitySupportState.PROVISIONAL_SUPPORT)
    changed_link = replace(changed_link, link_id=generate_capability_link_id(changed_link))
    links = tuple(changed_link if value == link else value for value in bank.links)
    cases.append(_reidentify(bank, links=links, summary=_summary(bank.items, links, bank.needs, bank.sources)))

    need = bank.needs[0]
    needs = tuple(replace(need, priority=type(need.priority).LOW) if value == need else value for value in bank.needs)
    cases.append(_reidentify(bank, needs=needs, summary=_summary(bank.items, bank.links, needs, bank.sources)))

    if len(bank.sources) > 1:
        wrong_source = bank.sources[1].source_record_id
        ref_item = replace(item, source_record_id=wrong_source)
        ref_items = tuple(ref_item if value == item else value for value in bank.items)
        cases.append(_reidentify(bank, items=ref_items, summary=_summary(ref_items, bank.links, bank.needs, bank.sources)))

    for index, forged in enumerate(cases):
        path = tmp_path / f"forged-{index}.json"
        path.write_text(json.dumps(forged.to_dict()), encoding="utf-8")
        with pytest.raises(Phase2ValidationError):
            load_evidence_bank(
                path,
                profile=context[0], rubric=context[1], catalog=context[2],
                mapping=context[3], evidence_reviews=context[4],
                recommendation=context[5], decision=context[6], gap_analysis=context[7],
            )


def test_unknown_fields_and_provenance_id_tampering_are_rejected():
    *_, bank = _bank_context()
    raw = bank.to_dict()
    raw["evidence_plan"] = {"future_project": "invented"}
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        EvidenceBank.from_dict(raw)
    for field, value in (
        ("evidence_bank_id", "evidence_bank_forged"),
        ("mapping_artifact_id", "mapping_forged"),
        ("recommendation_set_id", "recommendations_forged"),
        ("decision_id", "decision_forged"),
        ("gap_analysis_id", "gap_analysis_forged"),
    ):
        forged = bank.to_dict()
        forged[field] = value
        with pytest.raises(Phase2ValidationError):
            EvidenceBank.from_dict(forged)


def test_each_upstream_context_is_revalidated_not_only_compared_by_id():
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank = context
    profile_data = profile.to_dict()
    profile_data["basic_profile"]["current_status"] = "Changed after Bank creation"
    stale_profile = CareerProfile.from_dict(profile_data)
    cases = (
        {"profile": stale_profile},
        {"rubric": replace(rubric, rubric_version="9.9.9")},
        {"catalog": replace(catalog, catalog_version="9.9.9")},
        {"mapping": replace(mapping, provider_model="changed-model")},
        {"evidence_reviews": replace(reviews, profile_fingerprint="sha256:" + "0" * 64)},
        {"recommendation": replace(recommendation, provider_model="changed-model")},
        {"decision": replace(decision, user_reason="changed after Bank creation")},
        {"gap_analysis": replace(gap, created_at=LATER)},
    )
    baseline = {
        "profile": profile, "rubric": rubric, "catalog": catalog,
        "mapping": mapping, "evidence_reviews": reviews,
        "recommendation": recommendation, "decision": decision,
        "gap_analysis": gap,
    }
    for change in cases:
        with pytest.raises(Phase2ValidationError):
            bank.validate(**{**baseline, **change})


def test_allocation_review_provenance_is_bound_and_stale_change_is_rejected():
    profile, rubric, mapping = unresolved_mapping()
    _, _, catalog, *_ = _context()
    group = mapping.unresolved_evidence_groups[0]
    primary, secondary = group.allowed_primary_dimension_ids
    allocation = create_evidence_group_allocation_review_artifact(
        profile=profile, rubric=rubric, mapping=mapping, catalog=catalog,
        decisions={
            group.evidence_group_id: (
                AllocationReviewDecision.RESOLVED,
                primary,
                secondary,
                AllocationReviewerType.PROFILE_OWNER,
                NOW,
            )
        },
    )
    recommendation = build_role_recommendation(
        profile=profile, mapping_candidates=mapping, rubric=rubric,
        catalog=catalog, allocation_reviews=allocation, created_at=NOW,
    )
    decision = create_user_role_decision(
        status=DecisionStatus.CONFIRMED, catalog=catalog, rubric=rubric,
        profile=profile, recommendation_set=recommendation,
        primary_role=SelectedRole("applied_ai_engineer"),
        created_at=NOW, updated_at=NOW,
    )
    gap = build_career_gap_analysis(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision,
        allocation_reviews=allocation, created_at=NOW,
    )
    bank = build_evidence_bank(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        allocation_reviews=allocation, created_at=NOW,
    )
    assert bank.allocation_review_artifact_id == allocation.review_artifact_id
    stale = replace(allocation, mapping_artifact_id="mapping_forged")
    with pytest.raises(Phase2ValidationError):
        bank.validate(
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, gap_analysis=gap,
            allocation_reviews=stale,
        )


def test_revision_is_immutable_and_requires_complete_lineage(tmp_path):
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank = context
    revision = build_evidence_bank(
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        mapping=mapping,
        recommendation=recommendation,
        decision=decision,
        gap_analysis=gap,
        evidence_reviews=reviews,
        superseded_bank=bank,
        created_at=LATER,
    )
    assert revision.supersedes_evidence_bank_id == bank.evidence_bank_id
    assert revision.created_at == bank.created_at
    assert revision.updated_at == LATER
    _validate(revision, context, superseded_bank=bank)
    with pytest.raises(Phase2ValidationError):
        _validate(revision, context)

    path = tmp_path / "bank.json"
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        save_evidence_bank(
            bank, path,
            profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
            recommendation=recommendation, decision=decision, gap_analysis=gap,
            evidence_reviews=reviews,
        )
    assert path.read_bytes() == b"existing"


def test_atomic_replacement_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    context = _bank_context()
    *_, bank = context
    target = tmp_path / "bank.json"

    def fail_replace(*_):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        save_evidence_bank(
            bank, target,
            profile=context[0], rubric=context[1], catalog=context[2],
            mapping=context[3], evidence_reviews=context[4],
            recommendation=context[5], decision=context[6], gap_analysis=context[7],
        )
    assert not target.exists()
    assert not list(tmp_path.iterdir())


def test_future_resume_selector_excludes_needs_transferable_and_provisional_inference():
    *_, bank = _bank_context()
    all_selected = select_resume_evidence_items(bank)
    achievements = select_resume_evidence_items(bank, achievements_only=True)
    assert all(item.resume_eligible for item in all_selected)
    assert all(
        item.resume_use == ResumeEvidenceUse.CONFIRMED_CAPABILITY_EVIDENCE
        for item in achievements
    )
    assert not ({need.need_id for need in bank.needs} & {item.evidence_item_id for item in all_selected})
    transferable_item_ids = {
        link.evidence_item_id
        for link in bank.links
        if link.support_state == CapabilitySupportState.TRANSFERABLE_FOUNDATION_SUPPORT
    }
    assert not (transferable_item_ids & {item.evidence_item_id for item in achievements})
