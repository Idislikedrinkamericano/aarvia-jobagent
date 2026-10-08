from __future__ import annotations

import pytest

from aarvia.cli import (
    _claim_draft,
    _fill_claim_draft,
    _parse_evidence_numbers,
    main,
)
from aarvia.evidence_bank import build_evidence_bank, save_evidence_bank
from aarvia.evidence_enrichment import (
    EnrichmentClaimType,
    EnrichmentRelationship,
    EnrichmentScope,
    EnrichmentTemporality,
    create_enrichment_claim,
    load_evidence_enrichment,
)
from aarvia.role_catalog import Phase2ValidationError
from test_evidence_bank_cli import _cli_context


NOW = "2026-10-07T00:00:00+00:00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,2", (1, 2)),
        ("1, 2", (1, 2)),
        ("1 , 2", (1, 2)),
        ("1", (1,)),
        ("", ()),
        ("   ", ()),
    ],
)
def test_evidence_number_parser_accepts_equivalent_formats(value, expected):
    assert _parse_evidence_numbers(value, item_count=2) == expected


@pytest.mark.parametrize(
    "value",
    ["one", "1,,2", "1,1", "0", "-1", "3"],
)
def test_evidence_number_parser_rejects_invalid_or_unsafe_values(value):
    with pytest.raises(Phase2ValidationError):
        _parse_evidence_numbers(value, item_count=2)


@pytest.mark.parametrize(
    "answers",
    [
        ["action", "b", "responsibility", "Statement", "1", "supplements", "completed", "personal_contribution"],
        ["action", "Statement", "b", "Revised statement", "1", "supplements", "completed", "personal_contribution"],
        ["action", "Statement", "1", "b", "", "supplements", "completed", "personal_contribution"],
        ["action", "Statement", "1", "supplements", "b", "clarifies", "completed", "personal_contribution"],
        ["action", "Statement", "1", "supplements", "completed", "b", "ongoing", "personal_contribution"],
        ["technology", "Statement", "1", "supplements", "completed", "project_context", "b", "team_context", "Python"],
    ],
)
def test_each_claim_field_can_return_to_the_previous_step(answers):
    draft = _claim_draft(source_items=(object(),))
    iterator = iter(answers)
    assert _fill_claim_draft(
        draft, source_items=(object(),), input_fn=lambda _: next(iterator),
        output_fn=lambda _: None,
    )
    assert draft["statement"]
    assert draft["claim_type"] in EnrichmentClaimType


def test_back_from_first_field_returns_to_source_menu():
    draft = _claim_draft(source_items=(object(),))
    assert not _fill_claim_draft(
        draft, source_items=(object(),), input_fn=lambda _: "b",
        output_fn=lambda _: None,
    )


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
        "a", "action", "I implemented the workflow exactly as stated.", "1",
        "supplements", "completed", "personal_contribution", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]),
        "s",
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


def test_invalid_evidence_number_reprompts_and_preserves_prior_claim(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "a", "action", "First confirmed statement.", "1", "supplements",
        "completed", "personal_contribution", "c",
        "a", "context", "Second confirmed statement.", "not-a-number", "1",
        "clarifies", "ongoing", "project_context", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=lambda _: next(answers),
        output_fn=messages.append,
    ) == 0
    artifact = _load(output, context)
    assert {claim.user_supplied_statement for claim in artifact.claims} == {
        "First confirmed statement.", "Second confirmed statement."
    }
    assert artifact.summary.confirmed_count == 2
    assert any("Invalid evidence numbers" in message for message in messages)


def test_review_edit_menu_updates_all_claim_fields_and_recomputes_ids(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "a", "action", "Original statement.", "1", "supplements", "completed",
        "personal_contribution",
        "e",
        "1", "responsibility",
        "2", "Updated statement.",
        "3", "",
        "4", "clarifies",
        "5", "ongoing",
        "6", "team_context",
        "b", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 0
    artifact = _load(output, context)
    claim = artifact.claims[0]
    assert claim.claim_type == EnrichmentClaimType.RESPONSIBILITY
    assert claim.user_supplied_statement == "Updated statement."
    assert claim.evidence_item_ids == ()
    assert claim.relationship == EnrichmentRelationship.CLARIFIES
    assert claim.temporality == EnrichmentTemporality.ONGOING
    assert claim.scope == EnrichmentScope.TEAM_CONTEXT


def test_source_menu_edits_confirmed_claim_and_removes_old_ids(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    bank = context["bank_object"]
    source = bank.sources[0]
    item = next(item for item in bank.items if item.source_record_id == source.source_record_id)
    old_claim = create_enrichment_claim(
        evidence_bank_id=bank.evidence_bank_id,
        source_record_id=source.source_record_id,
        evidence_item_ids=[item.evidence_item_id],
        claim_type=EnrichmentClaimType.ACTION,
        relationship=EnrichmentRelationship.SUPPLEMENTS,
        user_supplied_statement="Original statement.",
        temporality=EnrichmentTemporality.COMPLETED,
        scope=EnrichmentScope.PERSONAL_CONTRIBUTION,
    )
    answers = iter([
        "a", "action", "Original statement.", "1", "supplements", "completed",
        "personal_contribution", "c",
        "e", "1", "e", "2", "Edited statement.", "b", "d",
        "n", *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 0
    artifact = _load(output, context)
    assert len(artifact.claims) == len(artifact.claim_reviews) == 1
    assert artifact.claims[0].user_supplied_statement == "Edited statement."
    assert artifact.claim_reviews[0].decision.value == "deferred"
    assert old_claim.claim_id not in {claim.claim_id for claim in artifact.claims}
    assert old_claim.claim_id not in {review.claim_id for review in artifact.claim_reviews}
    assert "Original statement." not in output.read_text()


def test_source_menu_deletes_confirmed_claim_after_explicit_confirmation(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    messages: list[str] = []
    prompts: list[str] = []
    answers = iter([
        "a", "action", "Delete this statement.", "1", "supplements", "completed",
        "personal_contribution", "c",
        "d", "1", "", "y", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    def input_fn(prompt):
        prompts.append(prompt)
        return next(answers)

    assert main(
        _args(context, output), input_fn=input_fn,
        output_fn=messages.append,
    ) == 0
    artifact = _load(output, context)
    assert artifact.claims == ()
    assert artifact.claim_reviews == ()
    assert any("Please enter one of" in message for message in messages)
    assert "Delete this Claim? [y/n/q]: " in prompts
    assert not any("[y/N/q]" in prompt for prompt in prompts)


def test_final_summary_edits_earlier_source_without_changing_other_source(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "a", "action", "First source statement.", "1", "supplements", "completed",
        "personal_contribution", "c", "n",
        "a", "context", "Second source statement.", "1", "clarifies", "ongoing",
        "project_context", "c", "n",
        *("n" for _ in context["bank_object"].sources[2:]),
        "e", "1", "1", "e", "2", "First source edited at final review.", "b", "c",
        "s",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 0
    artifact = _load(output, context)
    statements = {claim.user_supplied_statement for claim in artifact.claims}
    assert statements == {
        "First source edited at final review.", "Second source statement."
    }


def test_invalid_field_and_empty_source_action_reprompt_only_current_field(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    messages: list[str] = []
    answers = iter([
        "", "a",
        "invalid", "action",
        "", "Statement after retry.",
        "1",
        "invalid", "supplements",
        "invalid", "completed",
        "invalid", "personal_contribution",
        "invalid", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers),
        output_fn=messages.append,
    ) == 0
    assert _load(output, context).claims[0].user_supplied_statement == "Statement after retry."
    assert any("Statement cannot be blank" in message for message in messages)
    assert any("Please enter one of" in message for message in messages)
    assert not any("[y/N/q]" in message for message in messages)


@pytest.mark.parametrize(
    "failure",
    [
        lambda: (_ for _ in ()).throw(EOFError),
        lambda: (_ for _ in ()).throw(KeyboardInterrupt),
    ],
)
def test_evidence_number_prompt_eof_or_interrupt_writes_nothing(tmp_path, failure):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter(["a", "action", "Unfinished statement."])
    calls = 0

    def input_fn(_):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return next(answers)
        return failure()

    assert main(
        _args(context, output), input_fn=input_fn, output_fn=lambda _: None
    ) == 130
    assert not output.exists()


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
    answers = iter([*("n" for _ in context["bank_object"].sources), "q"])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 130
    assert not output.exists()


def test_final_rejection_after_collecting_claim_writes_nothing(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "enrichment.json"
    answers = iter([
        "a", "action", "I completed this synthetic test action.", "1",
        "supplements", "completed", "personal_contribution", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]),
        "q",
    ])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 130
    assert not output.exists()


def test_cli_supersede_writes_new_revision_and_preserves_old_file(tmp_path):
    context = _context(tmp_path)
    first_path = tmp_path / "enrichment-v1.json"
    first_answers = iter([
        "a", "context", "Synthetic confirmed context.", "1", "clarifies",
        "completed", "project_context", "c", "n",
        *("n" for _ in context["bank_object"].sources[1:]), "s",
    ])
    assert main(
        _args(context, first_path), input_fn=lambda _: next(first_answers),
        output_fn=lambda _: None,
    ) == 0
    first = _load(first_path, context)
    first_bytes = first_path.read_bytes()

    second_path = tmp_path / "enrichment-v2.json"
    second_answers = iter([
        "e", "1", "d", "n",
        *("n" for _ in context["bank_object"].sources[1:]),
        "s",
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
    answers = iter([*("n" for _ in context["bank_object"].sources), "s"])
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("simulated replacement failure")),
    )
    assert main(
        _args(context, output), input_fn=lambda _: next(answers), output_fn=lambda _: None
    ) == 1
    assert not output.exists()
