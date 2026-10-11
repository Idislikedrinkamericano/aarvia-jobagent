from __future__ import annotations

import pytest

from aarvia.cli import main
from aarvia.resume_profile_supplement import load_resume_profile_supplement
from aarvia.resume_wording import build_resume_wording_review, save_resume_wording_review
from test_resume_wording_cli import _context as _wording_context


NOW = "2026-10-10T00:00:00+00:00"


def _context(tmp_path):
    context = _wording_context(tmp_path)
    wording = build_resume_wording_review(
        candidates=[], reviews=[], created_at=NOW, **context["wording_context"],
    )
    context["wording_object"] = wording
    context["wording"] = save_resume_wording_review(
        wording, tmp_path / "wording.json", **context["wording_context"],
    )
    context["supplement_context"] = dict(
        context["wording_context"], profile=context["profile_object"],
        resume_material=context["material_object"],
        resume_wording_review=wording,
    )
    return context


def _args(context, output, *extra):
    return [
        "supplement-resume-profile",
        "--profile", str(context["profile"]), "--mapping", str(context["mapping"]),
        "--review-artifact", str(context["review"]),
        "--recommendation", str(context["recommendation"]),
        "--decision", str(context["decision"]), "--gap-analysis", str(context["gap"]),
        "--evidence-bank", str(context["bank"]),
        "--evidence-enrichment", str(context["enrichment"]),
        "--resume-material", str(context["material"]),
        "--resume-wording-review", str(context["wording"]),
        "--output", str(output), *extra,
    ]


def _run(context, output, answers, messages=None):
    iterator = iter(answers)
    return main(
        _args(context, output), input_fn=lambda _: next(iterator),
        output_fn=(lambda _: None) if messages is None else messages.append,
    )


def _load(path, context, previous=None):
    return load_resume_profile_supplement(
        path, superseded_supplement=previous, **context["supplement_context"],
    )


def test_help_lists_complete_chain_and_revision_flags(capsys):
    with pytest.raises(SystemExit) as error:
        main(["supplement-resume-profile", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--review-artifact", "--recommendation",
        "--decision", "--gap-analysis", "--evidence-bank", "--evidence-enrichment",
        "--resume-material", "--resume-wording-review", "--supersede", "--output",
    ):
        assert flag in output


def test_cli_adds_private_contact_masks_final_summary_and_saves(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "supplement.json"
    messages = []
    answers = ["a", "2", "owner@example.test", "c", "n", "n", "n", "n", "s"]
    assert _run(context, output, answers, messages) == 0
    artifact = _load(output, context)
    assert artifact.summary.contact_field_count == 1
    assert artifact.contact_fields[0].value == "owner@example.test"
    summary_index = messages.index("Resume Profile Supplement final summary")
    assert "owner@example.test" not in "\n".join(messages[summary_index:])
    assert "o***@example.test" in "\n".join(messages[summary_index:])


def test_invalid_contact_reprompts_current_field(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"; messages = []
    answers = ["a", "2", "bad", "owner@example.test", "c", "n", "n", "n", "n", "s"]
    assert _run(context, output, answers, messages) == 0
    assert any("Invalid input" in item for item in messages)
    assert _load(output, context).contact_fields[0].value == "owner@example.test"


def test_cli_adds_education_selection_as_structural_only(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    answers = ["n", "a", "1", "1, 2, 3", "c", "n", "n", "n", "s"]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    assert artifact.structural_selections[0].structural_use.value == "education_entry"
    assert artifact.structural_selections[0].profile_reference.path.startswith("education[")


def test_delete_contact_changes_only_memory_and_final_edit_can_restore(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    answers = [
        "a", "1", "Owner", "c", "d", "1", "y", "n",  # Contact ends empty
        "n", "n", "n", "e", "1",                       # Final edit Contact
        "a", "1", "New Owner", "c", "n", "s",
    ]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    assert artifact.contact_fields[0].value == "New Owner"


@pytest.mark.parametrize("failure", ["q", "eof", "interrupt"])
def test_cancel_eof_and_keyboard_interrupt_write_nothing(tmp_path, failure):
    context = _context(tmp_path / failure); output = tmp_path / f"{failure}.json"
    if failure == "q": input_fn = lambda _: "q"
    elif failure == "eof": input_fn = lambda _: (_ for _ in ()).throw(EOFError())
    else: input_fn = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    messages = []
    assert main(_args(context, output), input_fn=input_fn, output_fn=messages.append) == 130
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_existing_output_is_preserved(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    output.write_text("existing", encoding="utf-8")
    messages = []
    assert _run(context, output, [], messages) == 1
    assert output.read_text(encoding="utf-8") == "existing"


def test_revision_uses_new_path_and_preserves_old(tmp_path):
    context = _context(tmp_path); first = tmp_path / "first.json"
    assert _run(context, first, ["n", "n", "n", "n", "s"]) == 0
    previous = _load(first, context)
    second = tmp_path / "second.json"
    args = _args(context, second, "--supersede", str(first))
    answers = iter(["n", "n", "n", "n", "s"])
    assert main(args, input_fn=lambda _: next(answers), output_fn=lambda _: None) == 0
    revised = _load(second, context, previous)
    assert revised.supersedes_supplement_id == previous.supplement_id
    assert first.exists() and second.exists()
