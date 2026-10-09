from __future__ import annotations

import pytest

from aarvia.cli import main
from aarvia.evidence_enrichment import (
    ClaimReviewDecision,
    EnrichmentClaimType,
    EnrichmentRelationship,
    EnrichmentScope,
    EnrichmentTemporality,
    build_evidence_enrichment,
    create_claim_review,
    create_enrichment_claim,
    save_evidence_enrichment,
)
from aarvia.resume_material import load_resume_material
from test_evidence_enrichment_cli import _context


NOW = "2026-10-08T00:00:00+00:00"


def _resume_context(tmp_path):
    context = _context(tmp_path)
    bank = context["bank_object"]
    source = bank.sources[0]
    item = next(item for item in bank.items if item.source_record_id == source.source_record_id)
    claim = create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=source.source_record_id,
        evidence_item_ids=[item.evidence_item_id],
        claim_type=EnrichmentClaimType.ACTION,
        relationship=EnrichmentRelationship.SUPPLEMENTS,
        user_supplied_statement="I implemented the validated workflow.",
        temporality=EnrichmentTemporality.COMPLETED,
        scope=EnrichmentScope.PERSONAL_CONTRIBUTION,
    )
    review = create_claim_review(
        claim, decision=ClaimReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    enrichment = build_evidence_enrichment(
        profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_bank=bank, evidence_reviews=context["review_object"],
        claims=[claim], claim_reviews=[review], created_at=NOW,
    )
    context["enrichment_object"] = enrichment
    context["enrichment"] = save_evidence_enrichment(
        enrichment, tmp_path / "enrichment.json",
        profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_bank=bank, evidence_reviews=context["review_object"],
    )
    return context


def _args(context, output, *extra):
    return [
        "prepare-resume-materials",
        "--profile", str(context["profile"]),
        "--mapping", str(context["mapping"]),
        "--review-artifact", str(context["review"]),
        "--recommendation", str(context["recommendation"]),
        "--decision", str(context["decision"]),
        "--gap-analysis", str(context["gap"]),
        "--evidence-bank", str(context["bank"]),
        "--evidence-enrichment", str(context["enrichment"]),
        "--output", str(output),
        *extra,
    ]


def _load(path, context, *, previous=None):
    return load_resume_material(
        path, profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_bank=context["bank_object"],
        evidence_enrichment=context["enrichment_object"],
        evidence_reviews=context["review_object"],
        superseded_resume_material=previous,
    )


def test_help_lists_complete_context_and_revision_flags(capsys):
    with pytest.raises(SystemExit) as error:
        main(["prepare-resume-materials", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--allocation-review-artifact",
        "--review-artifact", "--recommendation", "--decision", "--gap-analysis",
        "--evidence-bank", "--evidence-enrichment", "--supersede", "--output",
    ):
        assert flag in output


def test_cli_builds_without_provider_and_shows_material_and_coverage(tmp_path):
    context = _resume_context(tmp_path)
    output = tmp_path / "materials.json"
    messages: list[str] = []

    class NoProvider:
        def map(self, *_):
            raise AssertionError("Resume Material must not call a Provider")

    assert main(
        _args(context, output), input_fn=lambda _: "s",
        output_fn=messages.append, mapper=NoProvider(),
    ) == 0
    artifact = _load(output, context)
    assert artifact.coverage.partial_coverage
    assert artifact.summary.achievement_component_count == 1
    assert any("Coverage" in item for item in messages)
    assert any("Partial coverage: yes" in item for item in messages)


@pytest.mark.parametrize(
    ("input_fn", "expected_code"),
    [
        (lambda _: "q", 130),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_cancel_paths_write_nothing(tmp_path, input_fn, expected_code):
    context = _resume_context(tmp_path)
    output = tmp_path / "materials.json"
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=input_fn, output_fn=messages.append
    ) == expected_code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_invalid_confirmation_reprompts_without_writing_early(tmp_path):
    context = _resume_context(tmp_path)
    output = tmp_path / "materials.json"
    answers = iter(["", "yes", "s"])
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=lambda _: next(answers),
        output_fn=messages.append,
    ) == 0
    assert output.exists()
    assert messages.count("Please enter s or q.") == 2


def test_existing_output_is_preserved_without_prompt(tmp_path):
    context = _resume_context(tmp_path)
    output = tmp_path / "materials.json"
    output.write_bytes(b"existing")
    assert main(
        _args(context, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt")),
        output_fn=lambda _: None,
    ) == 1
    assert output.read_bytes() == b"existing"


def test_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    context = _resume_context(tmp_path)
    output = tmp_path / "materials.json"
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("simulated replacement failure")),
    )
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=lambda _: "s", output_fn=messages.append
    ) == 1
    assert not output.exists()
    assert any("simulated replacement failure" in item for item in messages)


def test_cli_revision_uses_new_file_and_explicit_parent(tmp_path):
    context = _resume_context(tmp_path)
    first_path = tmp_path / "materials-1.json"
    second_path = tmp_path / "materials-2.json"
    assert main(_args(context, first_path), input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    first = _load(first_path, context)
    assert main(
        _args(context, second_path, "--supersede", str(first_path)),
        input_fn=lambda _: "s", output_fn=lambda _: None,
    ) == 0
    second = _load(second_path, context, previous=first)
    assert second.supersedes_resume_material_id == first.resume_material_id
