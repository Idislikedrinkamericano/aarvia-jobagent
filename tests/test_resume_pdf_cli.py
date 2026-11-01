from __future__ import annotations

from types import SimpleNamespace

import pytest

from aarvia.cli import _run_export_final_resume_pdf, main
from aarvia.resume_fact_check import (
    build_final_resume_fact_check,
    prepare_final_resume_bullet_checks,
    save_final_resume_fact_check,
)
from test_resume_fact_check import NOW, final_context


def test_export_final_resume_pdf_cli_uses_typed_fact_check(
    final_context, tmp_path, monkeypatch,
):
    checks, _, _, _ = prepare_final_resume_bullet_checks(**final_context)
    artifact = build_final_resume_fact_check(
        decisions={item.bullet_check_id: "passed" for item in checks},
        reasons={}, created_at=NOW, **final_context,
    )
    fact_check = tmp_path / "fact-check.json"
    save_final_resume_fact_check(artifact, fact_check, **final_context)
    monkeypatch.setattr(
        "aarvia.cli._load_final_resume_fact_check_context",
        lambda arguments: final_context,
    )
    output = tmp_path / "resume.pdf"
    arguments = SimpleNamespace(
        output=output,
        fact_check=fact_check,
        wording_proposal=None,
        wording_review=None,
        wording_tailored_resume=None,
    )
    messages = []
    assert _run_export_final_resume_pdf(
        arguments, output_fn=messages.append,
    ) == 0
    assert output.exists()
    assert any("bullets only" in message for message in messages)
    assert not any("sha256" in message.lower() for message in messages)


def test_export_final_resume_pdf_help(capsys):
    with pytest.raises(SystemExit):
        main(["export-final-resume-pdf", "--help"])
    output = capsys.readouterr().out
    assert "--fact-check" in output
    assert "--wording-tailored-resume" in output
    assert "--output" in output
