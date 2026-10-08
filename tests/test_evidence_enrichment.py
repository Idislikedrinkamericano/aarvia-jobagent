from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.evidence_bank import ResumeEvidenceUse
from aarvia.evidence_enrichment import (
    ClaimReviewDecision,
    EnrichmentClaimType,
    EnrichmentRelationship,
    EnrichmentScope,
    EnrichmentTemporality,
    EvidenceEnrichmentArtifact,
    ResumeEnrichmentUse,
    build_evidence_enrichment,
    create_claim_review,
    create_enrichment_claim,
    generate_enrichment_artifact_id,
    _resume_materials,
    load_evidence_enrichment,
    save_evidence_enrichment,
    select_resume_enrichment_materials,
)
from aarvia.role_catalog import Phase2ValidationError
from test_evidence_bank import _bank_context


NOW = "2026-10-07T00:00:00+00:00"
LATER = "2026-10-07T01:00:00+00:00"


def _claim(bank, *, source=None, items=None, claim_type=EnrichmentClaimType.ACTION,
           statement="I implemented the validated workflow.", structured_value=None):
    source = source or bank.sources[0]
    if items is None:
        items = tuple(item for item in bank.items if item.source_record_id == source.source_record_id)[:1]
    return create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=source.source_record_id,
        evidence_item_ids=[item.evidence_item_id for item in items],
        claim_type=claim_type,
        relationship=EnrichmentRelationship.SUPPLEMENTS,
        user_supplied_statement=statement,
        temporality=EnrichmentTemporality.COMPLETED,
        scope=EnrichmentScope.PERSONAL_CONTRIBUTION,
        structured_value=structured_value,
    )


def _artifact_context(*, decision=ClaimReviewDecision.CONFIRMED, claim=None):
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, role_decision, gap, bank = context
    claim = claim or _claim(bank)
    review = create_claim_review(claim, decision=decision, reviewed_at=NOW)
    artifact = build_evidence_enrichment(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=role_decision, gap_analysis=gap,
        evidence_bank=bank, claims=[claim], claim_reviews=[review], created_at=NOW,
        evidence_reviews=reviews,
    )
    return (*context, claim, review, artifact)


def _validation_kwargs(context):
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank, *_ = context
    return dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_reviews=reviews,
    )


def _reidentify(artifact, **changes):
    provisional = replace(artifact, **changes, enrichment_artifact_id="evidence_enrichment_pending")
    return replace(provisional, enrichment_artifact_id=generate_enrichment_artifact_id(provisional))


def test_schema_one_round_trip_and_complete_provenance():
    context = _artifact_context()
    *_, bank, claim, review, artifact = context
    assert artifact.schema == "aarvia.evidence_enrichment"
    assert artifact.schema_version == 1
    assert artifact.evidence_bank_id == bank.evidence_bank_id
    assert artifact.recommendation_schema_version == 7
    assert artifact.claims == (claim,)
    assert artifact.claim_reviews == (review,)
    assert EvidenceEnrichmentArtifact.from_dict(artifact.to_dict()) == artifact
    artifact.validate(**_validation_kwargs(context))


def test_claim_id_changes_with_bank_statement_and_structured_value():
    *_, bank = _bank_context()
    first = _claim(bank, claim_type=EnrichmentClaimType.METRIC, structured_value="12 percent")
    second = _claim(bank, claim_type=EnrichmentClaimType.METRIC, structured_value="13 percent")
    third = _claim(
        bank, claim_type=EnrichmentClaimType.METRIC, structured_value="12 percent",
        statement="I improved the measured result.",
    )
    assert len({first.claim_id, second.claim_id, third.claim_id}) == 3
    raw = first.to_dict()
    raw["claim_id"] = create_enrichment_claim(
        evidence_bank_id="evidence_bank_different", source_record_id=first.source_record_id,
        evidence_item_ids=first.evidence_item_ids, claim_type=first.claim_type,
        relationship=first.relationship, user_supplied_statement=first.user_supplied_statement,
        temporality=first.temporality, scope=first.scope,
        structured_value=first.structured_value,
    ).claim_id
    artifact = _artifact_context()[ -1]
    tampered_claim = artifact.claims[0].from_dict(raw, "claim")
    tampered_review = create_claim_review(
        tampered_claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    tampered = _reidentify(
        artifact, claims=(tampered_claim,), claim_reviews=(tampered_review,)
    )
    with pytest.raises(Phase2ValidationError, match="deterministic for its Evidence Bank"):
        tampered.validate(**_validation_kwargs(_artifact_context()))


def test_structured_value_only_allowed_for_applicable_claim_types():
    *_, bank = _bank_context()
    with pytest.raises(Phase2ValidationError, match="structured_value"):
        _claim(bank, structured_value="not allowed")


@pytest.mark.parametrize("bad_temporality", ["planned", "intended", "future", "completed_unverified"])
def test_future_or_unverified_temporality_is_rejected(bad_temporality):
    *_, bank = _bank_context()
    raw = _claim(bank).to_dict()
    raw["temporality"] = bad_temporality
    with pytest.raises(Phase2ValidationError):
        type(_claim(bank)).from_dict(raw, "claim")


def test_cross_source_and_unknown_item_references_are_rejected():
    context = _artifact_context()
    *_, bank, claim, review, artifact = context
    other_source = next(source for source in bank.sources if source.source_record_id != claim.source_record_id)
    other_item = next(item for item in bank.items if item.source_record_id == other_source.source_record_id)
    crossed = create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id, source_record_id=claim.source_record_id,
        evidence_item_ids=[other_item.evidence_item_id], claim_type=claim.claim_type,
        relationship=claim.relationship, user_supplied_statement=claim.user_supplied_statement,
        temporality=claim.temporality, scope=claim.scope,
    )
    crossed_review = create_claim_review(crossed, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
    crossed_artifact = build_evidence_enrichment
    with pytest.raises(Phase2ValidationError, match="crosses"):
        crossed_artifact(
            **_validation_kwargs(context), claims=[crossed], claim_reviews=[crossed_review],
            created_at=NOW,
        )
    raw = claim.to_dict()
    raw["evidence_item_ids"] = ["evidence_item_unknown"]
    raw["claim_id"] = "enrichment_claim_" + "a" * 24
    bad_claim = type(claim).from_dict(raw, "claim")
    bad_review = create_claim_review(bad_claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
    provisional = replace(artifact, claims=(bad_claim,), claim_reviews=(bad_review,), enrichment_artifact_id="evidence_enrichment_pending")
    bad_artifact = replace(provisional, enrichment_artifact_id=generate_enrichment_artifact_id(provisional))
    with pytest.raises(Phase2ValidationError):
        bad_artifact.validate(**_validation_kwargs(context))


def test_source_level_context_is_stored_but_not_resume_selectable():
    *base, bank = _bank_context()
    claim = _claim(bank, items=(), claim_type=EnrichmentClaimType.CONTEXT)
    context = _artifact_context(claim=claim)
    artifact = context[-1]
    assert artifact.summary.source_level_context_count == 1
    assert artifact.summary.resume_selectable_count == 0
    assert select_resume_enrichment_materials(artifact, **_validation_kwargs(context)) == ()


def test_confirmed_nonstructural_claim_is_resume_material_with_three_references():
    context = _artifact_context()
    *_, claim, _, artifact = context
    materials = select_resume_enrichment_materials(artifact, **_validation_kwargs(context))
    assert len(materials) == 1
    material = materials[0]
    assert material.claim_id == claim.claim_id
    assert material.source_record_id == claim.source_record_id
    assert material.evidence_item_ids == claim.evidence_item_ids


@pytest.mark.parametrize("decision", [ClaimReviewDecision.REJECTED, ClaimReviewDecision.DEFERRED])
def test_rejected_and_deferred_claims_are_retained_but_not_selectable(decision):
    context = _artifact_context(decision=decision)
    artifact = context[-1]
    assert artifact.summary.claim_count == 1
    assert select_resume_enrichment_materials(artifact, **_validation_kwargs(context)) == ()


def test_structural_fact_cannot_become_achievement():
    *_, bank = _bank_context()
    structural = replace(
        bank.items[0], resume_use=ResumeEvidenceUse.STRUCTURAL_INFORMATION,
        resume_eligible=True,
    )
    bank = replace(bank, items=(structural, *bank.items[1:]))
    source = next(item for item in bank.sources if item.source_record_id == structural.source_record_id)
    claim = _claim(bank, source=source, items=(structural,), claim_type=EnrichmentClaimType.RESULT)
    review = create_claim_review(claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
    assert _resume_materials((claim,), (review,), bank) == ()


def test_structural_technology_is_information_not_achievement():
    *_, bank = _bank_context()
    structural = replace(
        bank.items[0], resume_use=ResumeEvidenceUse.STRUCTURAL_INFORMATION,
        resume_eligible=True,
    )
    bank = replace(bank, items=(structural, *bank.items[1:]))
    source = next(item for item in bank.sources if item.source_record_id == structural.source_record_id)
    claim = _claim(
        bank, source=source, items=(structural,), claim_type=EnrichmentClaimType.TECHNOLOGY,
        structured_value="Python",
    )
    review = create_claim_review(claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
    materials = _resume_materials((claim,), (review,), bank)
    assert materials[0].use == ResumeEnrichmentUse.STRUCTURAL_INFORMATION


def test_duplicate_claim_and_multiple_reviews_are_rejected():
    context = _artifact_context()
    artifact = context[-1]
    raw = artifact.to_dict()
    raw["claims"].append(raw["claims"][0])
    with pytest.raises(Phase2ValidationError):
        EvidenceEnrichmentArtifact.from_dict(raw)
    raw = artifact.to_dict()
    raw["claim_reviews"].append(raw["claim_reviews"][0])
    with pytest.raises(Phase2ValidationError):
        EvidenceEnrichmentArtifact.from_dict(raw)


def test_input_order_does_not_change_serialization_id_or_summary():
    context = _bank_context()
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap, bank = context
    first = _claim(bank)
    second = _claim(bank, claim_type=EnrichmentClaimType.CONTEXT, statement="The work supported a team delivery.")
    first_review = create_claim_review(first, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW)
    second_review = create_claim_review(second, decision=ClaimReviewDecision.DEFERRED, reviewed_at=NOW)
    kwargs = dict(
        profile=profile, rubric=rubric, catalog=catalog, mapping=mapping,
        recommendation=recommendation, decision=decision, gap_analysis=gap,
        evidence_bank=bank, evidence_reviews=reviews, created_at=NOW,
    )
    a = build_evidence_enrichment(claims=[first, second], claim_reviews=[first_review, second_review], **kwargs)
    b = build_evidence_enrichment(claims=[second, first], claim_reviews=[second_review, first_review], **kwargs)
    assert a == b
    assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)


def test_revision_preserves_claims_allows_review_change_and_forbids_overwrite(tmp_path):
    context = _artifact_context(decision=ClaimReviewDecision.DEFERRED)
    *_, claim, _, first = context
    confirmed = create_claim_review(claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=LATER)
    second = build_evidence_enrichment(
        **_validation_kwargs(context), claims=[claim], claim_reviews=[confirmed],
        created_at=LATER, superseded_enrichment=first,
    )
    assert second.supersedes_enrichment_artifact_id == first.enrichment_artifact_id
    assert second.created_at == first.created_at
    output = tmp_path / "enrichment.json"
    save_evidence_enrichment(second, output, **_validation_kwargs(context), superseded_enrichment=first)
    assert load_evidence_enrichment(output, **_validation_kwargs(context), superseded_enrichment=first) == second
    with pytest.raises(FileExistsError):
        save_evidence_enrichment(second, output, **_validation_kwargs(context), superseded_enrichment=first)
    removed = _reidentify(second, claims=(), claim_reviews=(), summary=replace(second.summary, claim_count=0, confirmed_count=0, resume_selectable_count=0))
    with pytest.raises(Phase2ValidationError, match="cannot remove"):
        removed.validate(**_validation_kwargs(context), superseded_enrichment=first)


def test_atomic_replacement_failure_preserves_absence(tmp_path, monkeypatch):
    context = _artifact_context()
    artifact = context[-1]
    output = tmp_path / "enrichment.json"
    monkeypatch.setattr("aarvia.phase2_storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(OSError, match="failure"):
        save_evidence_enrichment(artifact, output, **_validation_kwargs(context))
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_fingerprint", "sha256:" + "0" * 64),
        ("mapping_artifact_id", "mapping_tampered"),
        ("recommendation_set_id", "recommendation_tampered"),
        ("decision_id", "decision_tampered"),
        ("gap_analysis_id", "gap_tampered"),
        ("rubric_version", "99.0.0"),
        ("catalog_version", "99.0.0"),
    ],
)
def test_provenance_tampering_is_rejected(field, value):
    context = _artifact_context()
    artifact = _reidentify(context[-1], **{field: value})
    with pytest.raises(Phase2ValidationError, match="stale or inconsistent"):
        artifact.validate(**_validation_kwargs(context))


def test_unknown_fields_and_review_tampering_are_rejected():
    artifact = _artifact_context()[-1]
    raw = artifact.to_dict()
    raw["provider_text"] = "injected"
    with pytest.raises(Phase2ValidationError):
        EvidenceEnrichmentArtifact.from_dict(raw)
    raw = artifact.to_dict()
    raw["claim_reviews"][0]["decision"] = "confirmed"
    raw["claim_reviews"][0]["review_id"] = "enrichment_review_" + "0" * 24
    with pytest.raises(Phase2ValidationError, match="review_id"):
        EvidenceEnrichmentArtifact.from_dict(raw)
