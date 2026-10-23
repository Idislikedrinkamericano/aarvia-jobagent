from __future__ import annotations

import json

from aarvia.cli import main
from test_base_resume_cli import _chain_args, _fixture


class TailoringMatcher:
    provider_name = "fixture"
    provider_model = "fixture-model"

    def __init__(self):
        self.calls = 0

    def analyze(self, jd_content, facts, *, analysis_date, correction=None):
        self.calls += 1
        fact = next((item for item in facts if item.enrichment_claim_ids), facts[0])
        return {"requirements": [{
            "requirement_type": "capability",
            "normalized_text": "Build verified systems",
            "exact_quote": "Build verified systems.",
            "condition_clauses": [{
                "normalized_text": "Build verified systems",
                "exact_quote": "Build verified systems.",
            }],
            "evaluation_mode": "evidence_match",
            "logic_operator": "atomic",
            "support_scope": "complete",
            "fact_ids": [fact.fact_id],
            "reasoning": "Fixture-only Provider explanation.",
        }]}


def _inputs(tmp_path):
    value = _fixture(tmp_path)
    base = tmp_path / "base.json"
    assert main(
        ["build-base-resume", *_chain_args(value), "--output", str(base)],
        input_fn=lambda _: "s",
        output_fn=lambda _: None,
    ) == 0
    jd = tmp_path / "jd.txt"
    jd.write_text("Build verified systems.", encoding="utf-8")
    analysis = tmp_path / "analysis.json"
    match = tmp_path / "match.json"
    match_report = tmp_path / "match.md"
    matcher = TailoringMatcher()
    assert main(
        [
            "match-jd", *_chain_args(value), "--base-resume", str(base),
            "--jd", str(jd), "--analysis-date", "2026-10-21",
            "--jd-analysis-output", str(analysis), "--output", str(match),
            "--report-output", str(match_report),
        ],
        jd_matcher=matcher,
        output_fn=lambda _: None,
    ) == 0
    return value, base, analysis, match, matcher


def _proposal_args(value, base, analysis, match, output, report):
    return [
        "propose-tailoring", *_chain_args(value), "--base-resume", str(base),
        "--jd-analysis", str(analysis), "--jd-match", str(match),
        "--output", str(output), "--report-output", str(report),
    ]


def test_propose_cli_is_provider_free_and_writes_separate_outputs(tmp_path):
    value, base, analysis, match, matcher = _inputs(tmp_path)
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "proposal.md"
    calls_before = matcher.calls
    assert main(
        _proposal_args(value, base, analysis, match, proposal, report),
        output_fn=lambda _: None,
    ) == 0
    assert matcher.calls == calls_before
    payload = json.loads(proposal.read_text(encoding="utf-8"))
    assert payload["schema"] == "aarvia.tailoring_proposal"
    assert "Fixture-only Provider explanation" not in report.read_text(encoding="utf-8")


def test_review_quit_eof_and_interrupt_write_nothing(tmp_path, monkeypatch):
    value, base, analysis, match, _ = _inputs(tmp_path)
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "proposal.md"
    assert main(
        _proposal_args(value, base, analysis, match, proposal, report),
        output_fn=lambda _: None,
    ) == 0
    from test_tailoring_proposal import _fixture as proposal_fixture

    context, proposal_analysis, proposal_match = proposal_fixture(tmp_path / "review-fixture")
    from aarvia.tailoring_proposal import build_tailoring_proposal

    populated = build_tailoring_proposal(
        context=context,
        jd_analysis=proposal_analysis,
        match_report=proposal_match,
        created_at="2026-10-21T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "aarvia.cli.load_tailoring_proposal",
        lambda *args, **kwargs: populated,
    )
    base_args = [
        "review-tailoring-proposal", *_chain_args(value),
        "--base-resume", str(base), "--jd-analysis", str(analysis),
        "--jd-match", str(match), "--proposal", str(proposal),
    ]
    for name, input_fn in (
        ("quit", lambda _: "q"),
        ("eof", lambda _: (_ for _ in ()).throw(EOFError())),
        ("interrupt", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())),
    ):
        output = tmp_path / f"{name}.json"
        assert main(
            [*base_args, "--output", str(output)],
            input_fn=input_fn,
            output_fn=lambda _: None,
        ) == 130
        assert not output.exists()


def test_proposal_outputs_are_never_overwritten(tmp_path):
    value, base, analysis, match, _ = _inputs(tmp_path)
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "proposal.md"
    args = _proposal_args(value, base, analysis, match, proposal, report)
    assert main(args, output_fn=lambda _: None) == 0
    before = proposal.read_bytes(), report.read_bytes()
    assert main(args, output_fn=lambda _: None) == 1
    assert (proposal.read_bytes(), report.read_bytes()) == before


def test_report_write_failure_rolls_back_proposal(tmp_path, monkeypatch):
    value, base, analysis, match, _ = _inputs(tmp_path)
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "proposal.md"

    def fail(*args, **kwargs):
        raise OSError("synthetic report failure")

    monkeypatch.setattr("aarvia.cli._save_new_text", fail)
    assert main(
        _proposal_args(value, base, analysis, match, proposal, report),
        output_fn=lambda _: None,
    ) == 1
    assert not proposal.exists()
    assert not report.exists()


def test_tailoring_help_lists_traceable_inputs(capsys):
    for command in ("propose-tailoring", "review-tailoring-proposal"):
        try:
            main([command, "--help"])
        except SystemExit:
            pass
        output = capsys.readouterr().out
        for flag in ("--base-resume", "--jd-analysis", "--jd-match", "--output"):
            assert flag in output
