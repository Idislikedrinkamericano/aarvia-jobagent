from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.resume_material import build_resume_material
from aarvia.evidence_enrichment import (
    ClaimReviewDecision, EnrichmentClaimType, EnrichmentRelationship,
    EnrichmentScope, EnrichmentTemporality, build_evidence_enrichment,
    create_claim_review, create_enrichment_claim,
)
from aarvia.resume_wording import (
    ResumeWordingReviewArtifact,
    WordingCandidateType,
    WordingOrigin,
    WordingReviewDecision,
    build_resume_wording_review,
    create_wording_candidate,
    create_wording_review,
    generate_resume_wording_artifact_id,
    load_resume_wording_review,
    save_resume_wording_review,
    select_confirmed_resume_wording,
)
from aarvia.role_catalog import Phase2ValidationError
from test_resume_material_cli import _resume_context


NOW = "2026-10-08T00:00:00+00:00"
LATER = "2026-10-08T01:00:00+00:00"


def _context(tmp_path):
    source = _resume_context(tmp_path)
    material_context = dict(
        profile=source["profile_object"], rubric=source["rubric"],
        catalog=source["catalog"], mapping=source["mapping_object"],
        recommendation=source["recommendation_object"],
        decision=source["decision_object"], gap_analysis=source["gap_object"],
        evidence_bank=source["bank_object"],
        evidence_enrichment=source["enrichment_object"],
        evidence_reviews=source["review_object"],
    )
    material = build_resume_material(**material_context, created_at=NOW)
    return source, material_context, material


def _context_with_claim(tmp_path, *, temporality, scope, statement):
    source = _resume_context(tmp_path)
    bank = source["bank_object"]
    record = bank.sources[0]
    item = next(item for item in bank.items if item.source_record_id == record.source_record_id)
    claim = create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=record.source_record_id,
        evidence_item_ids=[item.evidence_item_id],
        claim_type=EnrichmentClaimType.ACTION,
        relationship=EnrichmentRelationship.SUPPLEMENTS,
        user_supplied_statement=statement, temporality=temporality, scope=scope,
    )
    claim_review = create_claim_review(
        claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    enrichment = build_evidence_enrichment(
        profile=source["profile_object"], rubric=source["rubric"],
        catalog=source["catalog"], mapping=source["mapping_object"],
        recommendation=source["recommendation_object"],
        decision=source["decision_object"], gap_analysis=source["gap_object"],
        evidence_bank=bank, evidence_reviews=source["review_object"],
        claims=[claim], claim_reviews=[claim_review], created_at=NOW,
    )
    context = dict(
        profile=source["profile_object"], rubric=source["rubric"],
        catalog=source["catalog"], mapping=source["mapping_object"],
        recommendation=source["recommendation_object"],
        decision=source["decision_object"], gap_analysis=source["gap_object"],
        evidence_bank=bank, evidence_enrichment=enrichment,
        evidence_reviews=source["review_object"],
    )
    return context, build_resume_material(**context, created_at=NOW)


def _candidate(material, *, eligibility=None, candidate_type=None, text="User wording", created_at=NOW):
    selected = next(
        item for item in material.materials
        if eligibility is None or item.eligibility.value == eligibility
    )
    kind = candidate_type or (
        WordingCandidateType.STRUCTURAL_ENTRY
        if selected.eligibility.value == "structural_only"
        else WordingCandidateType.BULLET
    )
    wording = selected.exact_text if text == ".exact" else text
    origin = WordingOrigin.EXACT_MATERIAL if text == ".exact" else WordingOrigin.USER_AUTHORED
    return create_wording_candidate(
        section_type=selected.section, candidate_type=kind,
        source_record_id=selected.source_record_id,
        material_ids=[selected.material_id], wording_text=wording,
        origin=origin, created_at=created_at,
    )


def _artifact(tmp_path, *, decision=WordingReviewDecision.CONFIRMED):
    _, context, material = _context(tmp_path)
    candidate = _candidate(material, eligibility="achievement_component")
    review = create_wording_review(candidate, decision=decision, reviewed_at=NOW)
    artifact = build_resume_wording_review(
        resume_material=material, candidates=[candidate], reviews=[review],
        created_at=NOW, **context,
    )
    return context, material, candidate, review, artifact


def test_schema_round_trip_and_complete_provenance(tmp_path):
    _, material, _, _, artifact = _artifact(tmp_path)
    assert artifact.schema == "aarvia.resume_wording_review"
    assert artifact.schema_version == 1
    assert artifact.resume_material_artifact_id == material.resume_material_id
    assert ResumeWordingReviewArtifact.from_dict(artifact.to_dict()) == artifact


def test_same_source_materials_can_form_one_bullet(tmp_path):
    _, context, material = _context(tmp_path)
    groups = {}
    for item in material.materials:
        if item.eligibility.value != "structural_only":
            groups.setdefault(item.source_record_id, []).append(item)
    items = next(values for values in groups.values() if len(values) >= 2)
    candidate = create_wording_candidate(
        section_type=items[0].section, candidate_type=WordingCandidateType.BULLET,
        source_record_id=items[0].source_record_id,
        material_ids=[item.material_id for item in items[:2]],
        wording_text="Combined user-authored bullet.",
        origin=WordingOrigin.USER_AUTHORED, created_at=NOW,
    )
    review = create_wording_review(
        candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    result = build_resume_wording_review(
        resume_material=material, candidates=[candidate], reviews=[review],
        created_at=NOW, **context,
    )
    assert result.candidates[0].material_ids == tuple(sorted(item.material_id for item in items[:2]))


def test_cross_source_combination_is_rejected(tmp_path):
    _, context, material = _context(tmp_path)
    items = [item for item in material.materials if item.eligibility.value != "structural_only"]
    first = items[0]
    second = next(item for item in items if item.source_record_id != first.source_record_id)
    candidate = create_wording_candidate(
        section_type=first.section, candidate_type=WordingCandidateType.BULLET,
        source_record_id=first.source_record_id,
        material_ids=[first.material_id, second.material_id], wording_text="Invalid",
        origin=WordingOrigin.USER_AUTHORED, created_at=NOW,
    )
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="combine Source"):
        build_resume_wording_review(
            resume_material=material, candidates=[candidate], reviews=[review],
            created_at=NOW, **context,
        )


def test_structural_material_only_supports_structural_entry(tmp_path, monkeypatch):
    _, context, material = _context(tmp_path)
    from aarvia.resume_material import (
        ResumeMaterialEligibility, ResumeMaterialArtifact, ResumeSectionType,
        generate_resume_material_id,
    )
    original = material.materials[0]
    provisional = replace(
        original, material_id="resume_material_pending",
        eligibility=ResumeMaterialEligibility.STRUCTURAL_ONLY,
        section=ResumeSectionType.SKILLS,
    )
    structural = replace(provisional, material_id=generate_resume_material_id(provisional))
    material = replace(material, materials=(structural, *material.materials[1:]))
    monkeypatch.setattr(ResumeMaterialArtifact, "validate", lambda *_args, **_kwargs: None)
    candidate = create_wording_candidate(
        section_type=structural.section, candidate_type=WordingCandidateType.BULLET,
        source_record_id=structural.source_record_id, material_ids=[structural.material_id],
        wording_text="Built systems with Python", origin=WordingOrigin.USER_AUTHORED,
        created_at=NOW,
    )
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="structural material"):
        build_resume_wording_review(
            resume_material=material, candidates=[candidate], reviews=[review],
            created_at=NOW, **context,
        )


def test_structural_entry_rejects_achievement_material(tmp_path):
    _, context, material = _context(tmp_path)
    candidate = _candidate(
        material, eligibility="achievement_component",
        candidate_type=WordingCandidateType.STRUCTURAL_ENTRY,
    )
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="structural entry"):
        build_resume_wording_review(
            resume_material=material, candidates=[candidate], reviews=[review],
            created_at=NOW, **context,
        )


def test_confirmed_candidates_cannot_consume_material_twice(tmp_path):
    _, context, material = _context(tmp_path)
    first = _candidate(material, eligibility="achievement_component", text="First")
    second = _candidate(material, eligibility="achievement_component", text="Second")
    reviews = [
        create_wording_review(item, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
        for item in (first, second)
    ]
    with pytest.raises(Phase2ValidationError, match="consume material twice"):
        build_resume_wording_review(
            resume_material=material, candidates=[first, second], reviews=reviews,
            created_at=NOW, **context,
        )


@pytest.mark.parametrize("decision", [WordingReviewDecision.REJECTED, WordingReviewDecision.DEFERRED])
def test_rejected_and_deferred_are_not_selected(tmp_path, decision):
    context, material, _, _, artifact = _artifact(tmp_path, decision=decision)
    assert select_confirmed_resume_wording(
        artifact, resume_material=material, **context
    ) == ()


def test_confirmed_selector_returns_only_confirmed(tmp_path):
    context, material, candidate, _, artifact = _artifact(tmp_path)
    assert select_confirmed_resume_wording(
        artifact, resume_material=material, **context
    ) == (candidate,)


def test_exact_material_must_be_verbatim_and_single(tmp_path):
    _, context, material = _context(tmp_path)
    candidate = _candidate(material, eligibility="achievement_component", text=".exact")
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    build_resume_wording_review(
        resume_material=material, candidates=[candidate], reviews=[review],
        created_at=NOW, **context,
    )
    forged = replace(candidate, wording_text="changed", wording_candidate_id="wording_candidate_pending")
    forged = replace(forged, wording_candidate_id=__import__(
        "aarvia.resume_wording", fromlist=["generate_wording_candidate_id"]
    ).generate_wording_candidate_id(forged))
    forged_review = create_wording_review(forged, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="exact_material"):
        build_resume_wording_review(
            resume_material=material, candidates=[forged], reviews=[forged_review],
            created_at=NOW, **context,
        )


@pytest.mark.parametrize("field", ["wording_text", "material_ids", "source_record_id"])
def test_typed_load_rejects_candidate_tampering(tmp_path, field):
    context, material, _, _, artifact = _artifact(tmp_path)
    raw = artifact.to_dict()
    raw["candidates"][0][field] = (
        "Invented 900 percent result" if field == "wording_text"
        else (["resume_material_unknown"] if field == "material_ids" else "source_unknown")
    )
    path = tmp_path / f"tampered-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError):
        load_resume_wording_review(path, resume_material=material, **context)


def test_unknown_field_and_provider_origin_are_rejected(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path)
    raw = artifact.to_dict()
    raw["provider_payload"] = {}
    with pytest.raises(Phase2ValidationError, match="unknown"):
        ResumeWordingReviewArtifact.from_dict(raw)
    raw = artifact.to_dict()
    raw["candidates"][0]["origin"] = "provider_generated"
    with pytest.raises(Phase2ValidationError):
        ResumeWordingReviewArtifact.from_dict(raw)


def test_save_load_round_trip_and_existing_output_protection(tmp_path):
    context, material, _, _, artifact = _artifact(tmp_path)
    path = save_resume_wording_review(
        artifact, tmp_path / "wording.json", resume_material=material, **context
    )
    assert load_resume_wording_review(path, resume_material=material, **context) == artifact
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_resume_wording_review(artifact, path, resume_material=material, **context)
    assert path.read_bytes() == original


def test_revision_requires_new_path_and_preserves_lineage(tmp_path):
    context, material, candidate, review, first = _artifact(tmp_path)
    replacement = create_wording_candidate(
        section_type=candidate.section_type, candidate_type=candidate.candidate_type,
        source_record_id=candidate.source_record_id, material_ids=candidate.material_ids,
        wording_text="Edited wording", origin=WordingOrigin.USER_AUTHORED,
        created_at=LATER,
    )
    replacement_review = create_wording_review(
        replacement, decision=WordingReviewDecision.CONFIRMED, reviewed_at=LATER
    )
    revised = build_resume_wording_review(
        resume_material=material, candidates=[replacement], reviews=[replacement_review],
        created_at=LATER, superseded_wording_review=first, **context,
    )
    assert revised.supersedes_wording_review_id == first.wording_review_id
    assert revised.created_at == first.created_at
    assert candidate.wording_candidate_id not in {item.wording_candidate_id for item in revised.candidates}
    revised.validate(
        resume_material=material, superseded_wording_review=first, **context
    )


def test_input_order_does_not_change_artifact(tmp_path):
    _, context, material = _context(tmp_path)
    eligible = [item for item in material.materials if item.eligibility.value == "achievement_component"]
    candidates = [
        create_wording_candidate(
            section_type=item.section, candidate_type=WordingCandidateType.BULLET,
            source_record_id=item.source_record_id, material_ids=[item.material_id],
            wording_text=f"Wording {index}", origin=WordingOrigin.USER_AUTHORED,
            created_at=NOW,
        ) for index, item in enumerate(eligible[:2])
    ]
    reviews = [
        create_wording_review(item, decision=WordingReviewDecision.REJECTED, reviewed_at=NOW)
        for item in candidates
    ]
    first = build_resume_wording_review(
        resume_material=material, candidates=candidates, reviews=reviews,
        created_at=NOW, **context,
    )
    second = build_resume_wording_review(
        resume_material=material, candidates=list(reversed(candidates)),
        reviews=list(reversed(reviews)), created_at=NOW, **context,
    )
    assert first == second


def test_stale_resume_material_is_rejected(tmp_path):
    context, material, _, _, artifact = _artifact(tmp_path)
    stale = replace(material, resume_material_id="resume_material_stale")
    with pytest.raises(Phase2ValidationError):
        artifact.validate(resume_material=stale, **context)


def test_ongoing_claim_cannot_be_reworded_as_completed(tmp_path):
    context, material = _context_with_claim(
        tmp_path, temporality=EnrichmentTemporality.ONGOING,
        scope=EnrichmentScope.PERSONAL_CONTRIBUTION,
        statement="I continue developing the project.",
    )
    candidate = _candidate(
        material, eligibility="achievement_component",
        text="Completed and shipped the project.",
    )
    review = create_wording_review(candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW)
    with pytest.raises(Phase2ValidationError, match="ongoing"):
        build_resume_wording_review(
            resume_material=material, candidates=[candidate], reviews=[review],
            created_at=NOW, **context,
        )


def test_team_claim_must_preserve_contribution_boundary(tmp_path):
    context, material = _context_with_claim(
        tmp_path, temporality=EnrichmentTemporality.COMPLETED,
        scope=EnrichmentScope.TEAM_CONTEXT,
        statement="I contributed to model evaluation with the team.",
    )
    invalid = _candidate(
        material, eligibility="achievement_component",
        text="Owned model evaluation end to end.",
    )
    invalid_review = create_wording_review(
        invalid, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    with pytest.raises(Phase2ValidationError, match="contribution boundary"):
        build_resume_wording_review(
            resume_material=material, candidates=[invalid], reviews=[invalid_review],
            created_at=NOW, **context,
        )
    valid = _candidate(
        material, eligibility="achievement_component",
        text="Contributed to model evaluation with the team.",
    )
    valid_review = create_wording_review(
        valid, decision=WordingReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    build_resume_wording_review(
        resume_material=material, candidates=[valid], reviews=[valid_review],
        created_at=NOW, **context,
    )


def test_artifact_id_tampering_is_rejected(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path)
    raw = artifact.to_dict()
    raw["wording_review_id"] = "resume_wording_review_forged"
    with pytest.raises(Phase2ValidationError, match="not deterministic"):
        ResumeWordingReviewArtifact.from_dict(raw)


def test_review_provenance_id_and_schema_version_must_appear_together(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path)
    raw = artifact.to_dict()
    assert raw["binding_review_artifact_id"] is not None
    raw["binding_review_schema_version"] = None
    with pytest.raises(Phase2ValidationError, match="must appear together"):
        ResumeWordingReviewArtifact.from_dict(raw)


def test_review_timestamp_cannot_follow_artifact_update(tmp_path):
    _, _, candidate, _, artifact = _artifact(tmp_path)
    later_review = create_wording_review(
        candidate, decision=WordingReviewDecision.CONFIRMED, reviewed_at=LATER
    )
    provisional = replace(
        artifact, reviews=(later_review,), wording_review_id="resume_wording_review_pending"
    )
    raw = provisional.to_dict()
    raw["wording_review_id"] = generate_resume_wording_artifact_id(provisional)
    with pytest.raises(Phase2ValidationError, match="updated_at"):
        ResumeWordingReviewArtifact.from_dict(raw)


def test_reidentified_summary_tampering_is_rejected(tmp_path):
    _, _, _, _, artifact = _artifact(tmp_path)
    raw = artifact.to_dict()
    raw["summary"]["confirmed_count"] = 99
    provisional = replace(artifact, summary=replace(artifact.summary, confirmed_count=99))
    raw["wording_review_id"] = generate_resume_wording_artifact_id(provisional)
    with pytest.raises(Phase2ValidationError, match="summary"):
        ResumeWordingReviewArtifact.from_dict(raw)
