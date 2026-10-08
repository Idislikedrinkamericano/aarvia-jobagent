from __future__ import annotations

import pytest

from aarvia.cli import main
from aarvia.evidence_bank import build_evidence_bank, save_evidence_bank
from aarvia.evidence_enrichment import load_evidence_enrichment
from test_evidence_bank_cli import _cli_context


NOW = "2026-10-07T00:00:00+00:00"


def _context(tmp_path):
    context = _cli_context(tmp_path)
    bank = build_evidence_bank(
        profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_reviews=context["review_object"], created_at=NOW,
    )
    context["bank_object"] = bank
    context["bank"] = save_evidence_bank(
        bank, tmp_path / "bank.json", profile=context["profile_object"],
        rubric=context["rubric"], catalog=context["catalog"],
        mapping=context["mapping_object"], recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_reviews=context["review_object"],
    )
    return context


def _args(context, output, *extra):
    return [
        "enrich-evidence",
        "--profile", str(context["profile"]),
        "--mapping", str(context["mapping"]),
        "--review-artifact", str(context["review"]),
        "--recommendation", str(context["recommendation"]),
        "--decision", str(context["decision"]),
        "--gap-analysis", str(context["gap"]),
        "--evidence-bank", str(context["bank"]),
        "--output", str(output),
        *extra,
    ]


def _load(output, context, *, superseded=None):
    return load_evidence_enrichment(
        output, profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_bank=context["bank_object"], evidence_reviews=context["review_object"],
        superseded_enrichment=superseded,
    )


def test_help_lists_complete_context_and_revision_flags(capsys):
    with pytest.raises(SystemExit) as error:
        main(["enrich-evidence", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--allocation-review-artifact",
        "--review-artifact", "--recommendation", "--decision", "--gap-analysis",
        "--evidence-bank", "--supersede", "--output",
    ):
        assert flag in output


def test_cli_saves_exact_user_statement_without_provider(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "y", "action", "I implemented the workflow exactly as stated.", "1",
        "supplements", "completed", "personal_contribution", "confirmed", "n",
        *("n" for _ in context["bank_object"].sources[1:]),
        "y",
    ])
    messages: list[str] = []

    class NoProvider:
        def map(self, *_):
            raise AssertionError("Evidence Enrichment must not call a Provider")

    assert main(
        _args(context, output), input_fn=lambda _: next(answers),
        output_fn=messages.append, mapper=NoProvider(),
    ) == 0
    artifact = _load(output, context)
    assert artifact.claims[0].user_supplied_statement == "I implemented the workflow exactly as stated."
    assert artifact.summary.confirmed_count == 1
    assert any("Statement: I implemented" in item for item in messages)


@pytest.mark.parametrize(
    ("input_fn", "code"),
    [
        (lambda _: "q", 130),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_cancel_eof_and_interrupt_write_nothing(tmp_path, input_fn, code):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=input_fn, output_fn=messages.append
    ) == code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_final_rejection_writes_nothing(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([*("n" for _ in context["bank_object"].sources), "n"])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 0
    assert not output.exists()


def test_final_rejection_after_collecting_claim_writes_nothing(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "y", "action", "I completed this synthetic test action.", "1",
        "supplements", "completed", "personal_contribution", "confirmed", "n",
        *("n" for _ in context["bank_object"].sources[1:]),
        "n",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 0
    assert not output.exists()


def test_cli_supersede_writes_new_revision_and_preserves_old_file(tmp_path):
    context = _context(tmp_path)
    first_path = tmp_path / "enrichment-v1.json"
    first_answers = iter([
        "y", "context", "Synthetic confirmed context.", "1", "clarifies",
        "completed", "project_context", "confirmed", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "y",
    ])
    assert main(
        _args(context, first_path), input_fn=lambda _: next(first_answers),
        output_fn=lambda _: None,
    ) == 0
    first = _load(first_path, context)
    first_bytes = first_path.read_bytes()

    second_path = tmp_path / "enrichment-v2.json"
    second_answers = iter([
        "deferred",
        *("n" for _ in context["bank_object"].sources),
        "y",
    ])
    assert main(
        _args(context, second_path, "--supersede", str(first_path)),
        input_fn=lambda _: next(second_answers), output_fn=lambda _: None,
    ) == 0
    second = _load(second_path, context, superseded=first)
    assert second.supersedes_enrichment_artifact_id == first.enrichment_artifact_id
    assert second.claims == first.claims
    assert second.claim_reviews[0].decision.value == "deferred"
    assert first_path.read_bytes() == first_bytes


def test_existing_output_is_preserved_without_prompt(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    output.write_bytes(b"existing")
    assert main(
        _args(context, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt")),
        output_fn=lambda _: None,
    ) == 1
    assert output.read_bytes() == b"existing"


def test_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([*("n" for _ in context["bank_object"].sources), "y"])
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("simulated replacement failure")),
    )
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 1
    assert not output.exists()
