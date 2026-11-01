from __future__ import annotations

from types import SimpleNamespace

import pytest

from aarvia.cli import (
    FinalResumeFactCheckCancelled,
    _run_fact_check_resume,
    main,
)
from aarvia.resume_fact_check import load_final_resume_fact_check, prepare_final_resume_bullet_checks
from test_resume_fact_check import NOW, final_context


def _arguments(tmp_path):
    return SimpleNamespace(
        output=tmp_path / "fact-check.json",
        report_output=tmp_path / "fact-check.md",
        wording_proposal=None,
        wording_review=None,
        wording_tailored_resume=None,
    )


def test_fact_check_cli_writes_typed_outputs_and_rolls_back_on_report_failure(
    final_context, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "aarvia.cli._load_final_resume_fact_check_context",
        lambda arguments: final_context,
    )
    checks, _, _, _ = prepare_final_resume_bullet_checks(**final_context)
    arguments = _arguments(tmp_path)
    answers = iter((*(["p"] * len(checks)), "s"))
    assert _run_fact_check_resume(
        arguments, input_fn=lambda _: next(answers), output_fn=lambda _: None,
        now_fn=lambda: NOW,
    ) == 0
    assert arguments.output.exists() and arguments.report_output.exists()
    assert load_final_resume_fact_check(arguments.output, **final_context).summary.passed_count == len(checks)

    failed = _arguments(tmp_path / "failed")
    failed.output.parent.mkdir(parents=True)
    answers = iter((*(["p"] * len(checks)), "s"))
    monkeypatch.setattr(
        "aarvia.cli._save_new_text",
        lambda path, content: (_ for _ in ()).throw(OSError("synthetic report failure")),
    )
    with pytest.raises(OSError, match="synthetic report failure"):
        _run_fact_check_resume(
            failed, input_fn=lambda _: next(answers), output_fn=lambda _: None,
            now_fn=lambda: NOW,
        )
    assert not failed.output.exists() and not failed.report_output.exists()


def test_fact_check_cli_cancel_writes_nothing(final_context, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "aarvia.cli._load_final_resume_fact_check_context",
        lambda arguments: final_context,
    )
    arguments = _arguments(tmp_path)
    with pytest.raises(FinalResumeFactCheckCancelled):
        _run_fact_check_resume(
            arguments, input_fn=lambda _: "q", output_fn=lambda _: None,
            now_fn=lambda: NOW,
        )
    assert not arguments.output.exists() and not arguments.report_output.exists()


def test_fact_check_resume_help(capsys):
    with pytest.raises(SystemExit):
        main(["fact-check-resume", "--help"])
    output = capsys.readouterr().out
    assert "--tailored-resume" in output
    assert "--wording-tailored-resume" in output
    assert "--report-output" in output
