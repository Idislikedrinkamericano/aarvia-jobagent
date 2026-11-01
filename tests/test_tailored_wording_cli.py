from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aarvia.cli import (
    _run_apply_tailored_wording,
    _run_propose_tailored_wording,
    _run_review_tailored_wording,
    main,
)
from test_tailored_wording import JD, NOW, MockProvider, _context


def _arguments(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    values = {
        name: tmp_path / f"{name}.json"
        for name in (
            "profile", "mapping", "recommendation", "decision", "gap_analysis",
            "evidence_bank", "evidence_enrichment", "resume_material",
            "resume_wording_review", "resume_profile_supplement", "base_resume",
            "jd_analysis", "jd_match", "proposal", "proposal_review", "tailored_resume",
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
        "jd": tmp_path / "fictional.example.test.txt",
        "wording_proposal": None,
        "wording_review": None,
        "output": tmp_path / "wording-proposal.json",
        "report_output": tmp_path / "wording-proposal.md",
        "markdown_output": tmp_path / "wording-tailored.md",
        "change_log_output": tmp_path / "wording-changes.md",
    })
    values["jd"].write_text(JD, encoding="utf-8")
    return SimpleNamespace(**values)


def test_cli_mock_loop_and_late_output_rollback(tmp_path, monkeypatch):
    validation = _context(tmp_path / "fixture")
    monkeypatch.setattr("aarvia.cli._wording_context", lambda arguments: validation)
    arguments = _arguments(tmp_path)
    provider = MockProvider(count=2)
    assert _run_propose_tailored_wording(
        arguments, provider=provider, output_fn=lambda _: None, now_fn=lambda: NOW,
    ) == 0
    assert len(provider.calls) == 1
    assert arguments.output.exists() and arguments.report_output.exists()

    arguments.wording_proposal = arguments.output
    arguments.output = tmp_path / "wording-review.json"
    answers = iter(("a", "r", "s"))
    assert _run_review_tailored_wording(
        arguments,
        input_fn=lambda _: next(answers),
        output_fn=lambda _: None,
        now_fn=lambda: NOW,
    ) == 0
    arguments.wording_review = arguments.output
    assert arguments.wording_review.exists()

    arguments.output = tmp_path / "wording-tailored.json"
    assert _run_apply_tailored_wording(
        arguments, output_fn=lambda _: None, now_fn=lambda: NOW,
    ) == 0
    assert arguments.output.exists()
    assert arguments.markdown_output.exists()
    assert arguments.change_log_output.exists()
    assert "Applied wording changes: 1" in arguments.change_log_output.read_text(encoding="utf-8")

    second = _arguments(tmp_path / "failure")
    second.wording_proposal = arguments.wording_proposal
    second.wording_review = arguments.wording_review
    second.output = tmp_path / "failed.json"
    second.markdown_output = tmp_path / "failed.md"
    second.change_log_output = tmp_path / "failed-changes.md"
    from aarvia import cli
    original = cli._save_new_text
    calls = 0

    def fail_second(path: Path, content: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic late write failure")
        return original(path, content)

    monkeypatch.setattr("aarvia.cli._save_new_text", fail_second)
    with pytest.raises(OSError, match="synthetic late write failure"):
        _run_apply_tailored_wording(
            second, output_fn=lambda _: None, now_fn=lambda: NOW,
        )
    assert not second.output.exists()
    assert not second.markdown_output.exists()
    assert not second.change_log_output.exists()


@pytest.mark.parametrize("command", [
    "propose-tailored-wording", "review-tailored-wording", "apply-tailored-wording",
])
def test_tailored_wording_help(command, capsys):
    with pytest.raises(SystemExit):
        main([command, "--help"])
    output = capsys.readouterr().out
    assert "--tailored-resume" in output
    assert "--output" in output
