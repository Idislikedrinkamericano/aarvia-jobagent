from __future__ import annotations

from pathlib import Path

import pytest

from aarvia.cli import _supplement_record_numbers, main
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
        "--resume-session",
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
    answers = ["n", "a", "1", "c", "n", "n", "n", "s"]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    assert artifact.structural_selections[0].structural_use.value == "education_entry"
    assert artifact.structural_selections[0].profile_reference.path.startswith("education[")


@pytest.mark.parametrize(("raw", "maximum", "expected"), [
    ("all", 5, (1, 2, 3, 4, 5)),
    ("1,2,5", 5, (1, 2, 5)),
    ("1-5", 5, (1, 2, 3, 4, 5)),
    ("1,3-6,9", 9, (1, 3, 4, 5, 6, 9)),
])
def test_bulk_record_parser(raw, maximum, expected):
    assert _supplement_record_numbers(raw, maximum) == expected


@pytest.mark.parametrize("raw", ["", "1,,2", "1,1", "1-3,3", "0", "-1", "x", "3-1", "1-9"])
def test_bulk_record_parser_rejects_invalid_input(raw):
    with pytest.raises(ValueError):
        _supplement_record_numbers(raw, 5)


@pytest.mark.parametrize(("choice", "decision"), [
    ("c", "confirmed"), ("r", "rejected"), ("d", "deferred"),
])
def test_bulk_skills_use_actual_default_fields_and_one_decision(tmp_path, choice, decision):
    context = _context(tmp_path / decision); output = tmp_path / f"{decision}.json"
    answers = ["n", "n", "a", "all", choice, "n", "n", "s"]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    skill_selections = [item for item in artifact.structural_selections if item.record_type.value == "skill"]
    assert skill_selections
    reviews = {item.subject_id: item.decision.value for item in artifact.inclusion_reviews}
    assert {reviews[item.selection_id] for item in skill_selections} == {decision}
    for item in skill_selections:
        snapshot = item.profile_reference.value_snapshot
        assert item.selected_fields == tuple(sorted(key for key, value in snapshot.items() if value is not None))


def test_bulk_then_single_edit_and_delete(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    answers = [
        "n", "n", "a", "all", "c",
        "e", "1", "1", "d",
        "d", "2", "y", "n",
        "n", "s",
    ]
    assert _run(context, output, answers) == 0
    artifact = _load(output, context)
    skill_items = [item for item in artifact.structural_selections if item.record_type.value == "skill"]
    uncovered = context["material_object"].coverage.profile_skill_count - context["material_object"].coverage.covered_skill_count
    assert len(skill_items) == uncovered - 1
    review_by_id = {item.subject_id: item.decision.value for item in artifact.inclusion_reviews}
    assert sum(review_by_id[item.selection_id] == "deferred" for item in skill_items) == 1


def test_invalid_bulk_input_reprompts_without_losing_section_state(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"; messages = []
    answers = ["n", "n", "a", "1,,2", "1-2", "c", "n", "n", "s"]
    assert _run(context, output, answers, messages) == 0
    artifact = _load(output, context)
    skill_items = [
        item for item in artifact.structural_selections
        if item.record_type.value == "skill"
    ]
    assert len(skill_items) == 2
    assert any("empty entries" in item for item in messages)


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


def test_explicit_pause_is_atomic_and_resume_returns_to_exact_section(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    session = Path(str(output) + ".session.json")
    messages = []
    assert _run(context, output, ["n", "n", "p"], messages) == 0
    assert not output.exists()
    assert session.exists()
    assert any("paused" in item for item in messages)

    resume_messages = []
    args = _args(context, output, "--resume-session", str(session))
    answers = iter(["a", "all", "c", "n", "n", "s"])
    assert main(args, input_fn=lambda _: next(answers), output_fn=resume_messages.append) == 0
    artifact = _load(output, context)
    assert any(item.record_type.value == "skill" for item in artifact.structural_selections)
    assert any("skills" in item for item in resume_messages if "Resuming" in item)
    assert session.exists()
    assert any("retained" in item for item in resume_messages)


def test_pause_preserves_private_contact_without_formal_output(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    session = tmp_path / "private-session.json"; messages = []
    args = _args(context, output, "--resume-session", str(session))
    answers = iter(["a", "2", "owner@example.test", "c", "p"])
    assert main(args, input_fn=lambda _: next(answers), output_fn=messages.append) == 0
    assert session.exists() and not output.exists()
    pause_messages = "\n".join(item for item in messages if "paused" in item or "formal" in item)
    assert "owner@example.test" not in pause_messages


def test_resume_session_rejects_different_target_output(tmp_path):
    context = _context(tmp_path); output = tmp_path / "first.json"
    session = tmp_path / "session.json"
    args = _args(context, output, "--resume-session", str(session))
    assert main(args, input_fn=lambda _: "p", output_fn=lambda _: None) == 0
    messages = []
    other = tmp_path / "other.json"
    assert main(
        _args(context, other, "--resume-session", str(session)),
        input_fn=lambda _: "q", output_fn=messages.append,
    ) == 1
    assert not other.exists()
    assert any("different output" in item for item in messages)


def test_default_session_is_not_silently_replaced(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"
    session = Path(str(output) + ".session.json")
    session.write_text("existing", encoding="utf-8")
    messages = []
    assert _run(context, output, [], messages) == 1
    assert session.read_text(encoding="utf-8") == "existing"
    assert any("--resume-session" in item for item in messages)


def test_session_path_cannot_replace_an_upstream_input(tmp_path):
    context = _context(tmp_path); output = tmp_path / "supplement.json"; messages = []
    assert main(
        _args(context, output, "--resume-session", str(context["profile"])),
        input_fn=lambda _: "p", output_fn=messages.append,
    ) == 1
    assert not output.exists()
    assert any("must not replace an input artifact" in item for item in messages)


@pytest.mark.parametrize("failure", ["q", "eof", "interrupt"])
def test_cancel_never_creates_default_session_draft(tmp_path, failure):
    context = _context(tmp_path / failure); output = tmp_path / f"{failure}.json"
    session = Path(str(output) + ".session.json")
    if failure == "q": input_fn = lambda _: "q"
    elif failure == "eof": input_fn = lambda _: (_ for _ in ()).throw(EOFError())
    else: input_fn = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    assert main(_args(context, output), input_fn=input_fn, output_fn=lambda _: None) == 130
    assert not output.exists() and not session.exists()
