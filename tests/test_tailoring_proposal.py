from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.base_resume import build_base_resume
from aarvia.jd_matching import build_jd_match
from aarvia.resume_context import ValidatedResumeContext
from aarvia.resume_material import ResumeSectionType
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
from aarvia.tailoring_proposal import (
    TailoringAction,
    TailoringProposalArtifact,
    TailoringProposalReviewArtifact,
    TailoringReviewDecision,
    TailoringTargetType,
    accepted_tailoring_suggestions,
    build_tailoring_proposal,
    create_tailoring_proposal_review,
    load_tailoring_proposal,
    load_tailoring_proposal_review,
    render_tailoring_proposal_report,
    save_tailoring_proposal,
    save_tailoring_proposal_review,
)
from test_resume_wording import _context as wording_context


NOW = "2026-10-21T00:00:00+00:00"
JD = "Build reliable Python workflows."


def _supports(fact, bullet) -> bool:
    return bool(
        fact.fact_id in bullet.evidence_item_ids
        or fact.fact_id in bullet.enrichment_claim_ids
        or set(fact.evidence_item_ids) & set(bullet.evidence_item_ids)
        or set(fact.enrichment_claim_ids) & set(bullet.enrichment_claim_ids)
    )


def _fixture(tmp_path):
    source, material_context, material = wording_context(tmp_path)
    selected = next(
        item for item in material.materials
        if item.eligibility.value == "achievement_component"
    )
    specifications = ((selected, "Existing JD-relevant wording"),)
    candidates = tuple(
        create_wording_candidate(
            section_type=ResumeSectionType.PROJECTS,
            candidate_type=WordingCandidateType.BULLET,
            source_record_id=item.source_record_id,
            material_ids=[item.material_id],
            wording_text=text,
            origin=WordingOrigin.USER_AUTHORED,
            created_at=NOW,
        )
        for item, text in specifications
    )
    reviews = tuple(
        create_wording_review(
            item,
            decision=WordingReviewDecision.CONFIRMED,
            reviewed_at=NOW,
        )
        for item in candidates
    )
    wording = build_resume_wording_review(
        resume_material=material,
        candidates=candidates,
        reviews=reviews,
        created_at=NOW,
        **material_context,
    )
    supplement_context = dict(
        material_context,
        resume_material=material,
        resume_wording_review=wording,
    )
    supplement = build_resume_profile_supplement(
        contact_fields=(),
        structural_selections=(),
        inclusion_reviews=(),
        created_at=NOW,
        **supplement_context,
    )
    base_context = dict(
        supplement_context,
        resume_profile_supplement=supplement,
    )
    initial = build_base_resume(created_at=NOW, **base_context)
    base = initial
    context = ValidatedResumeContext(
        profile=source["profile_object"],
        rubric=source["rubric"],
        catalog=source["catalog"],
        mapping=source["mapping_object"],
        recommendation=source["recommendation_object"],
        decision=source["decision_object"],
        gap_analysis=source["gap_object"],
        evidence_bank=source["bank_object"],
        evidence_enrichment=source["enrichment_object"],
        resume_material=material,
        resume_wording_review=wording,
        resume_profile_supplement=supplement,
        base_resume=base,
        evidence_reviews=source["review_object"],
    )
    target_bullet = next(item for item in base.bullets if item.text == "Existing JD-relevant wording")
    fact = next(item for item in context.fact_index().facts if _supports(item, target_bullet))
    payload = {"requirements": [{
        "requirement_type": "capability",
        "normalized_text": "Reliable Python workflows",
        "exact_quote": JD,
        "condition_clauses": [{
            "normalized_text": "Reliable Python workflows",
            "exact_quote": JD,
        }],
        "evaluation_mode": "evidence_match",
        "logic_operator": "atomic",
        "support_scope": "complete",
        "fact_ids": [fact.fact_id],
        "reasoning": "Provider reasoning is not a career fact.",
    }]}
    analysis, match_report = build_jd_match(
        jd_content=JD,
        source_name="fictional-jd.txt",
        provider_payload=payload,
        provider_name="fixture",
        provider_model="fixture-model",
        context=context,
        analysis_date="2026-10-21",
        created_at=NOW,
    )
    return context, analysis, match_report


def test_builder_prioritizes_existing_entry_and_reviewed_bullet(tmp_path):
    context, analysis, match_report = _fixture(tmp_path)
    proposal = build_tailoring_proposal(
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
        created_at=NOW,
    )
    assert TailoringProposalArtifact.from_dict(proposal.to_dict()) == proposal
    proposal.validate(context=context, jd_analysis=analysis, match_report=match_report)
    assert {item.action for item in proposal.suggestions} == {TailoringAction.RETAIN_BULLET}
    assert all(item.suggested_position == item.current_position for item in proposal.suggestions)
    assert {item.target_type for item in proposal.suggestions} == {TailoringTargetType.BULLET}
    assert all(item.fact_ids and item.requirement_ids for item in proposal.suggestions)
    assert not any("delete" in item.action.value for item in proposal.suggestions)
    assert not any(item.section_type in {ResumeSectionType.CONTACT, ResumeSectionType.EDUCATION} for item in proposal.suggestions)
    assert "Provider reasoning" not in " ".join(item.rationale for item in proposal.suggestions)


def test_report_is_review_only_and_uses_exact_jd_requirement(tmp_path):
    context, analysis, match_report = _fixture(tmp_path)
    proposal = build_tailoring_proposal(
        context=context, jd_analysis=analysis, match_report=match_report, created_at=NOW,
    )
    report = render_tailoring_proposal_report(
        proposal, context=context, jd_analysis=analysis, match_report=match_report,
    )
    assert JD in report
    assert "No resume content has been changed" in report
    assert "remains unreviewed" in report
    assert "Provider reasoning is not a career fact" not in report


def test_direct_and_loaded_tampering_is_rejected(tmp_path):
    context, analysis, match_report = _fixture(tmp_path)
    proposal = build_tailoring_proposal(
        context=context, jd_analysis=analysis, match_report=match_report, created_at=NOW,
    )
    bad = proposal.to_dict()
    bad["invented"] = True
    with pytest.raises(Phase2ValidationError):
        TailoringProposalArtifact.from_dict(bad)
    bad = proposal.to_dict()
    bad["suggestions"][0]["fact_ids"] = ["fact_forged"]
    with pytest.raises(Phase2ValidationError):
        TailoringProposalArtifact.from_dict(bad)
    with pytest.raises(Phase2ValidationError):
        replace(proposal, base_resume_id="base_resume_forged").validate(
            context=context, jd_analysis=analysis, match_report=match_report,
        )


def test_review_is_independent_complete_and_deterministic(tmp_path):
    context, analysis, match_report = _fixture(tmp_path)
    proposal = build_tailoring_proposal(
        context=context, jd_analysis=analysis, match_report=match_report, created_at=NOW,
    )
    ordered = proposal.suggestions
    decisions = {
        ordered[0].suggestion_id: TailoringReviewDecision.ACCEPTED,
    }
    review = create_tailoring_proposal_review(
        proposal=proposal,
        decisions=decisions,
        reviewed_at=NOW,
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
    )
    assert TailoringProposalReviewArtifact.from_dict(review.to_dict()) == review
    review.validate(
        proposal=proposal,
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
    )
    selected = accepted_tailoring_suggestions(
        proposal,
        review,
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
    )
    assert [item.suggestion_id for item in selected] == [ordered[0].suggestion_id]
    proposal_path = tmp_path / "proposal.json"
    review_path = tmp_path / "tailoring-review.json"
    validation = dict(context=context, jd_analysis=analysis, match_report=match_report)
    save_tailoring_proposal(proposal, proposal_path, **validation)
    assert load_tailoring_proposal(proposal_path, **validation) == proposal
    save_tailoring_proposal_review(
        review,
        review_path,
        proposal=proposal,
        **validation,
    )
    assert load_tailoring_proposal_review(
        review_path,
        proposal=proposal,
        **validation,
    ) == review
    with pytest.raises(FileExistsError):
        save_tailoring_proposal(proposal, proposal_path, **validation)
    with pytest.raises(Phase2ValidationError, match="must match current suggestions"):
        create_tailoring_proposal_review(
            proposal=proposal,
            decisions={},
            reviewed_at=NOW,
            context=context,
            jd_analysis=analysis,
            match_report=match_report,
        )
