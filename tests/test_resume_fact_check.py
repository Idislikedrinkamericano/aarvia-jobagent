from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.phase2_storage import load_phase2_json
from aarvia.resume_fact_check import (
    FinalResumeFactCheckArtifact,
    FinalResumeKind,
    ResumeSemanticReviewStatus,
    build_final_resume_fact_check,
    generate_final_resume_bullet_check_id,
    generate_final_resume_bullet_review_id,
    generate_final_resume_fact_check_id,
    load_final_resume_fact_check,
    prepare_final_resume_bullet_checks,
    render_final_resume_fact_check_report,
    save_final_resume_fact_check,
)
from aarvia.resume_wording import WordingReviewDecision
from aarvia.role_catalog import Phase2ValidationError
from aarvia.tailored_wording import (
    build_wording_tailored_resume,
    create_tailored_wording_review,
    generate_tailored_wording_proposal,
)
from test_tailored_resume import NOW
from test_tailored_wording import JD, MockProvider, _context


@pytest.fixture(scope="module")
def final_context(tmp_path_factory):
    validation = _context(tmp_path_factory.mktemp("resume_fact_check"))
    proposal = generate_tailored_wording_proposal(
        provider=MockProvider(count=1), jd_content=JD, created_at=NOW, **validation,
    )
    review = create_tailored_wording_review(
        proposal=proposal,
        decisions={proposal.candidates[0].wording_candidate_id: WordingReviewDecision.CONFIRMED},
        reviewed_at=NOW,
        **validation,
    )
    final = build_wording_tailored_resume(
        proposal=proposal, review=review, created_at=NOW, **validation,
    )
    return dict(
        validation,
        wording_proposal=proposal,
        wording_review=review,
        wording_tailored_resume=final,
    )


def _decisions(checks, statuses=None):
    statuses = statuses or [ResumeSemanticReviewStatus.PASSED] * len(checks)
    return {item.bullet_check_id: status for item, status in zip(checks, statuses)}


def test_ordering_only_mixed_review_round_trip_and_contact_exclusion(final_context, tmp_path):
    context = dict(final_context, wording_proposal=None, wording_review=None, wording_tailored_resume=None)
    checks, _, _, kind = prepare_final_resume_bullet_checks(**context)
    assert kind == FinalResumeKind.ORDERING_ONLY
    assert all(not item.wording_changed for item in checks)
    statuses = [
        ResumeSemanticReviewStatus.PASSED,
        ResumeSemanticReviewStatus.ISSUE_FOUND,
        ResumeSemanticReviewStatus.NEEDS_CONFIRMATION,
        *([ResumeSemanticReviewStatus.PASSED] * max(0, len(checks) - 3)),
    ]
    reasons = {
        checks[1].bullet_check_id: "Ownership needs correction.",
        checks[2].bullet_check_id: "Technology use needs confirmation.",
    }
    artifact = build_final_resume_fact_check(
        decisions=_decisions(checks, statuses), reasons=reasons,
        created_at=NOW, **context,
    )
    assert artifact.summary.issue_count == 1
    assert artifact.summary.needs_confirmation_count == 1
    report = render_final_resume_fact_check_report(artifact, **context)
    assert "Deterministic provenance and reference validation passed" in report
    assert "issue_found" in report and "needs_confirmation" in report
    contact_values = {
        value for entry in context["context"].base_resume.entries
        if entry.item_type.value == "contact" for _, value in entry.fields
    }
    assert not any(value in report for value in contact_values)
    path = tmp_path / "fact-check.json"
    save_final_resume_fact_check(artifact, path, **context)
    assert load_final_resume_fact_check(path, **context) == artifact


def test_wording_overlay_is_bound_to_accepted_change(final_context):
    checks, _, _, kind = prepare_final_resume_bullet_checks(**final_context)
    assert kind == FinalResumeKind.WORDING_OVERLAY
    changed = [item for item in checks if item.wording_changed]
    assert len(changed) == 1
    assert changed[0].applied_wording_candidate_id is not None
    artifact = build_final_resume_fact_check(
        decisions=_decisions(checks), reasons={}, created_at=NOW, **final_context,
    )
    artifact.validate(**final_context)
    assert artifact.summary.changed_bullet_count == 1

    proposal = final_context["wording_proposal"]
    rejected = create_tailored_wording_review(
        proposal=proposal,
        decisions={proposal.candidates[0].wording_candidate_id: WordingReviewDecision.REJECTED},
        reviewed_at=NOW,
        **{key: value for key, value in final_context.items() if not key.startswith("wording_")},
    )
    with pytest.raises(Phase2ValidationError):
        prepare_final_resume_bullet_checks(
            **dict(final_context, wording_review=rejected),
        )


def test_reference_source_and_stale_tampering_are_rejected(final_context):
    checks, _, _, _ = prepare_final_resume_bullet_checks(**final_context)
    artifact = build_final_resume_fact_check(
        decisions=_decisions(checks), reasons={}, created_at=NOW, **final_context,
    )
    first = replace(checks[0], source_record_id="evidence_source_wrong")
    first = replace(first, bullet_check_id=generate_final_resume_bullet_check_id(first))
    old_review = artifact.reviews[0]
    first_review = replace(old_review, bullet_check_id=first.bullet_check_id)
    first_review = replace(
        first_review, review_id=generate_final_resume_bullet_review_id(first_review),
    )
    tampered = replace(
        artifact,
        bullet_checks=(first, *artifact.bullet_checks[1:]),
        reviews=(first_review, *artifact.reviews[1:]),
    )
    tampered = replace(tampered, fact_check_id=generate_final_resume_fact_check_id(tampered))
    loaded = FinalResumeFactCheckArtifact.from_dict(tampered.to_dict())
    with pytest.raises(Phase2ValidationError):
        loaded.validate(**final_context)

    raw = artifact.to_dict()
    raw["final_resume_content_hash"] = "sha256:" + "0" * 64
    raw["fact_check_id"] = generate_final_resume_fact_check_id(
        replace(artifact, final_resume_content_hash=raw["final_resume_content_hash"])
    )
    forged = FinalResumeFactCheckArtifact.from_dict(raw)
    with pytest.raises(Phase2ValidationError):
        forged.validate(**final_context)


def test_unknown_fields_and_incomplete_decisions_are_rejected(final_context):
    checks, _, _, _ = prepare_final_resume_bullet_checks(**final_context)
    with pytest.raises(Phase2ValidationError, match="cover every bullet"):
        build_final_resume_fact_check(
            decisions={checks[0].bullet_check_id: ResumeSemanticReviewStatus.PASSED},
            reasons={}, created_at=NOW, **final_context,
        )
    artifact = build_final_resume_fact_check(
        decisions=_decisions(checks), reasons={}, created_at=NOW, **final_context,
    )
    raw = artifact.to_dict()
    raw["provider_decision"] = "passed"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        FinalResumeFactCheckArtifact.from_dict(raw)
