from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.base_resume import BaseResumeItemType, build_base_resume
from aarvia.evidence_enrichment import (
    ClaimReviewDecision,
    EnrichmentClaimType,
    EnrichmentRelationship,
    EnrichmentScope,
    EnrichmentTemporality,
    build_evidence_enrichment,
    create_claim_review,
    create_enrichment_claim,
)
from aarvia.jd_matching import build_jd_match
from aarvia.resume_context import ValidatedResumeContext
from aarvia.resume_material import ResumeSectionType, build_resume_material
from aarvia.resume_profile_supplement import build_resume_profile_supplement
from aarvia.resume_wording import (
    WordingCandidateType,
    WordingOrigin,
    WordingReviewDecision,
    build_resume_wording_review,
    create_wording_candidate,
    create_wording_review,
)
from aarvia.role_catalog import Phase2ValidationError
from aarvia.tailored_resume import (
    TailoredResumeArtifact,
    _apply_forward_moves,
    build_tailored_resume,
    load_tailored_resume,
    materialize_tailored_resume,
    render_tailored_resume_markdown,
    render_tailoring_change_log,
    save_tailored_resume,
)
from aarvia.tailoring_proposal import (
    TailoringAction,
    TailoringReviewDecision,
    build_tailoring_proposal,
    create_tailoring_proposal_review,
)
from test_resume_material_cli import _resume_context


NOW = "2026-10-22T00:00:00+00:00"
JD = "Build alpha workflows. Build beta systems."


def _claim(bank, source, item, statement):
    return create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=source.source_record_id,
        evidence_item_ids=[item.evidence_item_id],
        claim_type=EnrichmentClaimType.ACTION,
        relationship=EnrichmentRelationship.SUPPLEMENTS,
        user_supplied_statement=statement,
        temporality=EnrichmentTemporality.COMPLETED,
        scope=EnrichmentScope.PERSONAL_CONTRIBUTION,
    )


def _fixture(tmp_path):
    source = _resume_context(tmp_path)
    bank = source["bank_object"]
    first_source = bank.sources[0]
    second_source = next(item for item in bank.sources if item.source_record_id != first_source.source_record_id)
    first_item = next(item for item in bank.items if item.source_record_id == first_source.source_record_id)
    second_item = next(item for item in bank.items if item.source_record_id == second_source.source_record_id)
    claims = (
        _claim(bank, first_source, first_item, "Implemented synthetic alpha workflows."),
        _claim(bank, first_source, first_item, "Built synthetic beta systems."),
        _claim(bank, first_source, first_item, "Documented unrelated synthetic context."),
        _claim(bank, second_source, second_item, "Built an unrelated synthetic project."),
    )
    claim_reviews = tuple(
        create_claim_review(item, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
        for item in claims
    )
    shared = dict(
        profile=source["profile_object"],
        rubric=source["rubric"],
        catalog=source["catalog"],
        mapping=source["mapping_object"],
        recommendation=source["recommendation_object"],
        decision=source["decision_object"],
        gap_analysis=source["gap_object"],
        evidence_bank=bank,
        evidence_reviews=source["review_object"],
    )
    enrichment = build_evidence_enrichment(
        **shared,
        claims=claims,
        claim_reviews=claim_reviews,
        created_at=NOW,
    )
    material_context = dict(shared, evidence_enrichment=enrichment)
    material = build_resume_material(**material_context, created_at=NOW)
    claim_materials = {
        item.enrichment_claim_ids[0]: item
        for item in material.materials if item.enrichment_claim_ids
    }
    wording_candidates = tuple(
        create_wording_candidate(
            section_type=ResumeSectionType.PROJECTS,
            candidate_type=WordingCandidateType.BULLET,
            source_record_id=claim.source_record_id,
            material_ids=[claim_materials[claim.claim_id].material_id],
            wording_text=claim.user_supplied_statement,
            origin=WordingOrigin.USER_AUTHORED,
            created_at=NOW,
        )
        for claim in claims
    )
    wording_reviews = tuple(
        create_wording_review(item, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
        for item in wording_candidates
    )
    wording = build_resume_wording_review(
        **material_context,
        resume_material=material,
        candidates=wording_candidates,
        reviews=wording_reviews,
        created_at=NOW,
    )
    supplement_context = dict(
        material_context,
        resume_material=material,
        resume_wording_review=wording,
    )
    supplement = build_resume_profile_supplement(
        **supplement_context,
        contact_fields=(),
        structural_selections=(),
        inclusion_reviews=(),
        created_at=NOW,
    )
    base_context = dict(supplement_context, resume_profile_supplement=supplement)
    initial = build_base_resume(**base_context, created_at=NOW)
    entries = [item for item in initial.entries if item.item_type == BaseResumeItemType.PROJECT]
    first_entry = next(item for item in entries if item.source_record_id == first_source.source_record_id)
    second_entry = next(item for item in entries if item.source_record_id == second_source.source_record_id)
    bullet_by_text = {item.text: item.bullet_id for item in initial.bullets}
    first_bullet_order = (
        bullet_by_text[claims[2].user_supplied_statement],
        bullet_by_text[claims[0].user_supplied_statement],
        bullet_by_text[claims[1].user_supplied_statement],
    )
    base = build_base_resume(
        **base_context,
        created_at=NOW,
        entry_order={ResumeSectionType.PROJECTS: (second_entry.entry_id, first_entry.entry_id)},
        bullet_order={first_entry.entry_id: first_bullet_order},
    )
    context = ValidatedResumeContext(
        **material_context,
        resume_material=material,
        resume_wording_review=wording,
        resume_profile_supplement=supplement,
        base_resume=base,
    )
    facts = {item.enrichment_claim_ids[0]: item.fact_id for item in context.fact_index().facts if item.enrichment_claim_ids}
    payload = {"requirements": [
        {
            "requirement_type": "capability",
            "normalized_text": "Alpha workflows",
            "exact_quote": "Build alpha workflows.",
            "condition_clauses": [{"normalized_text": "Alpha workflows", "exact_quote": "Build alpha workflows."}],
            "evaluation_mode": "evidence_match",
            "logic_operator": "atomic",
            "support_scope": "complete",
            "fact_ids": [facts[claims[0].claim_id]],
            "reasoning": "Synthetic reasoning.",
        },
        {
            "requirement_type": "capability",
            "normalized_text": "Beta systems",
            "exact_quote": "Build beta systems.",
            "condition_clauses": [{"normalized_text": "Beta systems", "exact_quote": "Build beta systems."}],
            "evaluation_mode": "evidence_match",
            "logic_operator": "atomic",
            "support_scope": "complete",
            "fact_ids": [facts[claims[1].claim_id]],
            "reasoning": "Synthetic reasoning.",
        },
    ]}
    analysis, match_report = build_jd_match(
        jd_content=JD,
        source_name="fictional.example.test.txt",
        provider_payload=payload,
        provider_name="fixture",
        provider_model="fixture-model",
        context=context,
        analysis_date="2026-10-22",
        created_at=NOW,
    )
    proposal = build_tailoring_proposal(
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
        created_at=NOW,
    )
    return context, analysis, match_report, proposal


def _review(context, analysis, match_report, proposal, decisions):
    return create_tailoring_proposal_review(
        proposal=proposal,
        decisions=decisions,
        reviewed_at=NOW,
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
    )


def _validation(context, analysis, match_report, proposal, review):
    return dict(
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
        proposal=proposal,
        proposal_review=review,
    )


def test_mixed_review_applies_only_accepted_and_preserves_content(tmp_path):
    context, analysis, match_report, proposal = _fixture(tmp_path)
    entry_move = next(item for item in proposal.suggestions if item.action == TailoringAction.PRIORITIZE_ENTRY)
    retain = [item for item in proposal.suggestions if item.action == TailoringAction.RETAIN_BULLET]
    assert retain
    decisions = {item.suggestion_id: TailoringReviewDecision.REJECTED for item in proposal.suggestions}
    decisions[entry_move.suggestion_id] = TailoringReviewDecision.ACCEPTED
    decisions[retain[0].suggestion_id] = TailoringReviewDecision.ACCEPTED
    if len(retain) > 1:
        decisions[retain[1].suggestion_id] = TailoringReviewDecision.DEFERRED
    review = _review(context, analysis, match_report, proposal, decisions)
    result = build_tailored_resume(
        **_validation(context, analysis, match_report, proposal, review),
        created_at=NOW,
    )
    materialized = materialize_tailored_resume(
        result,
        **_validation(context, analysis, match_report, proposal, review),
    )
    assert result.summary.accepted_suggestion_count == 2
    assert {item.suggestion_id for item in result.changes} == {
        entry_move.suggestion_id,
        retain[0].suggestion_id,
    }
    assert {item.bullet_id: item.text for item in materialized.bullets} == {
        item.bullet_id: item.text for item in context.base_resume.bullets
    }
    assert {item.entry_id: item.fields for item in materialized.entries} == {
        item.entry_id: item.fields for item in context.base_resume.entries
    }
    assert set(item.bullet_id for item in materialized.bullets) == set(
        item.bullet_id for item in context.base_resume.bullets
    )


def test_multiple_moves_are_stable_and_keep_unadjusted_relative_order():
    assert _apply_forward_moves(("a", "b", "c", "d", "e"), {"d": 1, "e": 2}) == (
        "d", "e", "a", "b", "c"
    )
    assert _apply_forward_moves(("a", "b", "c", "d"), {"c": 1, "d": 1}) == (
        "c", "d", "a", "b"
    )


def test_no_accepted_suggestions_preserves_layout(tmp_path):
    context, analysis, match_report, proposal = _fixture(tmp_path)
    decisions = {item.suggestion_id: TailoringReviewDecision.DEFERRED for item in proposal.suggestions}
    review = _review(context, analysis, match_report, proposal, decisions)
    result = build_tailored_resume(
        **_validation(context, analysis, match_report, proposal, review),
        created_at=NOW,
    )
    materialized = materialize_tailored_resume(
        result,
        **_validation(context, analysis, match_report, proposal, review),
    )
    assert result.changes == ()
    assert tuple(item.to_dict() for item in result.section_layouts) == tuple(
        {"section_type": item.section_type.value, "entry_ids": list(item.entry_ids)}
        for item in context.base_resume.sections
    )
    assert [item.entry_id for item in materialized.entries] == [item.entry_id for item in context.base_resume.entries]


def test_typed_load_render_and_stale_rejection(tmp_path):
    context, analysis, match_report, proposal = _fixture(tmp_path)
    decisions = {item.suggestion_id: TailoringReviewDecision.ACCEPTED for item in proposal.suggestions}
    review = _review(context, analysis, match_report, proposal, decisions)
    validation = _validation(context, analysis, match_report, proposal, review)
    result = build_tailored_resume(**validation, created_at=NOW)
    assert TailoredResumeArtifact.from_dict(result.to_dict()) == result
    path = tmp_path / "tailored.json"
    save_tailored_resume(result, path, **validation)
    assert load_tailored_resume(path, **validation) == result
    rendered = render_tailored_resume_markdown(result, **validation)
    assert rendered
    assert "Ordering-only tailoring" in render_tailoring_change_log(result, **validation)
    with pytest.raises(Phase2ValidationError):
        replace(result, base_resume_id="base_resume_forged").validate(**validation)
    tampered = result.to_dict()
    tampered["section_layouts"][0]["entry_ids"] = ["base_resume_entry_unknown"]
    with pytest.raises(Phase2ValidationError):
        TailoredResumeArtifact.from_dict(tampered).validate(**validation)
