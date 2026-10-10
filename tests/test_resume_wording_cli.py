from __future__ import annotations

import pytest

from aarvia.cli import main
from aarvia.resume_material import build_resume_material, save_resume_material
from aarvia.resume_wording import load_resume_wording_review
from test_resume_material_cli import _resume_context


NOW = "2026-10-08T00:00:00+00:00"


def _context(tmp_path):
    context = _resume_context(tmp_path)
    material_context = dict(
        profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
        evidence_bank=context["bank_object"],
        evidence_enrichment=context["enrichment_object"],
        evidence_reviews=context["review_object"],
    )
    material = build_resume_material(**material_context, created_at=NOW)
    context["material_object"] = material
    context["material"] = save_resume_material(
        material, tmp_path / "resume-material.json", **material_context
    )
    context["wording_context"] = dict(
        resume_material=material, **material_context,
        superseded_resume_material=None,
    )
    return context


def _args(context, output, *extra):
    return [
        "review-resume-wording",
        "--profile", str(context["profile"]),
        "--mapping", str(context["mapping"]),
        "--review-artifact", str(context["review"]),
        "--recommendation", str(context["recommendation"]),
        "--decision", str(context["decision"]),
        "--gap-analysis", str(context["gap"]),
        "--evidence-bank", str(context["bank"]),
        "--evidence-enrichment", str(context["enrichment"]),
        "--resume-material", str(context["material"]),
        "--output", str(output), *extra,
    ]


def _run(context, output, answers, messages=None):
    iterator = iter(answers)
    return main(
        _args(context, output), input_fn=lambda _: next(iterator),
        output_fn=(lambda _: None) if messages is None else messages.append,
    )


def _load(path, context, previous=None):
    return load_resume_wording_review(
        path, superseded_wording_review=previous, **context["wording_context"]
    )


def test_help_lists_complete_chain_and_revision_flags(capsys):
    with pytest.raises(SystemExit) as error:
        main(["review-resume-wording", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--review-artifact",
        "--allocation-review-artifact", "--recommendation", "--decision",
        "--gap-analysis", "--evidence-bank", "--evidence-enrichment",
        "--resume-material", "--supersede", "--output",
    ):
        assert flag in output


def test_cli_adds_confirms_and_saves_user_wording(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    messages: list[str] = []
    assert _run(
        context, output,
        ["a", "u", "1", "My exact wording", "c", *(["n"] * 5), "s"],
        messages,
    ) == 0
    artifact = _load(output, context)
    assert artifact.summary.confirmed_count == 1
    assert artifact.candidates[0].wording_text == "My exact wording"
    assert any("not a complete resume" in item for item in messages)


def test_material_number_spacing_variants(tmp_path):
    context = _context(tmp_path)
    # The synthetic source has at least two compatible materials.
    output = tmp_path / "wording.json"
    assert _run(
        context, output, ["a", "u", "1 , 2", "Combined wording", "c", *(["n"] * 5), "s"]
    ) == 0
    assert len(_load(output, context).candidates[0].material_ids) == 2


def test_invalid_material_input_reprompts_same_field(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    messages: list[str] = []
    assert _run(
        context, output,
        ["a", "u", "1,,2", "1, 2", "Recovered wording", "c", *(["n"] * 5), "s"],
        messages,
    ) == 0
    assert any("empty entries" in item for item in messages)


def test_field_back_navigation_preserves_session(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    answers = [
        "a", "u", "1", "Initial", "b",  # review -> wording
        "Edited", "e", "m", "b",        # edit to material, back to type
        "u", "1", "Final", "c", *(["n"] * 5), "s",
    ]
    assert _run(context, output, answers) == 0
    assert _load(output, context).candidates[0].wording_text == "Final"


def test_source_edit_replaces_candidate_and_old_ids_do_not_remain(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    answers = [
        "a", "u", "1", "Old wording", "c",
        "e", "1", "u", "1", "New wording", "c",
        *(["n"] * 5), "s",
    ]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    assert [item.wording_text for item in artifact.candidates] == ["New wording"]
    assert len(artifact.reviews) == 1


def test_source_delete_requires_confirmation(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    answers = [
        "a", "u", "1", "Remove me", "c",
        "d", "1", "n", "d", "1", "y", *(["n"] * 5), "s",
    ]
    assert _run(context, output, answers) == 0
    assert _load(output, context).summary.candidate_count == 0


def test_final_summary_edit_can_change_earlier_candidate(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    answers = [
        "a", "u", "1", "Before", "c", *(["n"] * 5),
        "e", "1", "e", "1", "u", "1", "After", "c", "n", "s",
    ]
    assert _run(context, output, answers) == 0
    assert _load(output, context).candidates[0].wording_text == "After"


@pytest.mark.parametrize(
    ("input_fn", "code"),
    [
        (lambda _: "q", 130),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_cancel_eof_interrupt_write_nothing(tmp_path, input_fn, code):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=input_fn, output_fn=messages.append
    ) == code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_existing_output_is_never_overwritten(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    output.write_bytes(b"existing")
    assert main(
        _args(context, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("must not prompt")),
        output_fn=lambda _: None,
    ) == 1
    assert output.read_bytes() == b"existing"


def test_atomic_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"
    monkeypatch.setattr(
        "aarvia.phase2_storage.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replacement failed")),
    )
    messages: list[str] = []
    assert _run(
        context, output, [*(["n"] * 5), "s"], messages
    ) == 1
    assert not output.exists()
    assert any("replacement failed" in item for item in messages)


def test_provider_is_never_called(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "wording.json"

    class NoProvider:
        def map(self, *_args, **_kwargs):
            raise AssertionError("Resume wording must not call Provider")

    answers = iter([*(["n"] * 5), "s"])
    assert main(
        _args(context, output), input_fn=lambda _: next(answers),
        output_fn=lambda _: None, mapper=NoProvider(),
    ) == 0


def test_revision_uses_new_output_and_lineage(tmp_path):
    context = _context(tmp_path)
    first_path = tmp_path / "first.json"
    assert _run(context, first_path, ["a", "u", "1", "First", "c", *(["n"] * 5), "s"]) == 0
    first = _load(first_path, context)
    second_path = tmp_path / "second.json"
    args = _args(context, second_path, "--supersede", str(first_path))
    answers = iter(["e", "1", "u", "1", "Second", "c", *(["n"] * 5), "s"])
    assert main(args, input_fn=lambda _: next(answers), output_fn=lambda _: None) == 0
    second = _load(second_path, context, first)
    assert second.supersedes_wording_review_id == first.wording_review_id
    assert second.candidates[0].wording_text == "Second"
    assert first_path.exists()
