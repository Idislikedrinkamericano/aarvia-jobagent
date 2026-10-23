from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aarvia.cli import _run_apply_tailoring, main
from aarvia.tailoring_proposal import TailoringReviewDecision
from test_tailored_resume import NOW, _fixture, _review


def _application(tmp_path):
    context, analysis, match_report, proposal = _fixture(tmp_path / "fixture")
    review = _review(
        context,
        analysis,
        match_report,
        proposal,
        {item.suggestion_id: TailoringReviewDecision.ACCEPTED for item in proposal.suggestions},
    )
    return context, analysis, match_report, proposal, review


def _arguments(tmp_path):
    values = {
        name: tmp_path / f"{name}.json"
        for name in (
            "profile", "mapping", "recommendation", "decision", "gap_analysis",
            "evidence_bank", "evidence_enrichment", "resume_material",
            "resume_wording_review", "resume_profile_supplement", "base_resume",
            "jd_analysis", "jd_match", "proposal", "proposal_review",
        )
    }
    values.update({
        "review_artifact": None,
        "allocation_review_artifact": None,
        "superseded_decision": None,
        "superseded_enrichment": None,
        "superseded_resume_material": None,
        "superseded_wording_review": None,
        "superseded_supplement": None,
        "superseded_base_resume": None,
        "output": tmp_path / "tailored.json",
        "markdown_output": tmp_path / "tailored.md",
        "change_log_output": tmp_path / "changes.md",
    })
    return SimpleNamespace(**values)


def test_apply_cli_writes_three_new_outputs_without_provider(tmp_path, monkeypatch):
    loaded = _application(tmp_path)
    monkeypatch.setattr("aarvia.cli._load_tailoring_application_inputs", lambda arguments: loaded)
    arguments = _arguments(tmp_path)
    messages = []
    assert _run_apply_tailoring(
        arguments,
        output_fn=messages.append,
        now_fn=lambda: NOW,
    ) == 0
    assert arguments.output.exists()
    assert arguments.markdown_output.exists()
    assert arguments.change_log_output.exists()
    assert "aarvia.tailored_resume" in arguments.output.read_text(encoding="utf-8")
    assert "Ordering-only tailoring" in arguments.change_log_output.read_text(encoding="utf-8")
    assert any("No wording" in item for item in messages)


def test_apply_cli_rolls_back_all_outputs_on_late_failure(tmp_path, monkeypatch):
    loaded = _application(tmp_path)
    monkeypatch.setattr("aarvia.cli._load_tailoring_application_inputs", lambda arguments: loaded)
    arguments = _arguments(tmp_path)
    from aarvia import cli

    original = cli._save_new_text
    calls = 0

    def fail_second(path: Path, content: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic change-log failure")
        return original(path, content)

    monkeypatch.setattr("aarvia.cli._save_new_text", fail_second)
    try:
        _run_apply_tailoring(arguments, output_fn=lambda _: None, now_fn=lambda: NOW)
    except OSError:
        pass
    else:
        raise AssertionError("synthetic output failure was not propagated")
    assert not arguments.output.exists()
    assert not arguments.markdown_output.exists()
    assert not arguments.change_log_output.exists()


def test_apply_tailoring_help_explains_all_outputs(capsys):
    try:
        main(["apply-tailoring", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    for flag in (
        "--proposal-review", "--output", "--markdown-output", "--change-log-output",
    ):
        assert flag in output
