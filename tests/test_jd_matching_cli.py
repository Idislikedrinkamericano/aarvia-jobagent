from __future__ import annotations

import json

from aarvia.cli import main
from test_base_resume_cli import _chain_args, _fixture


class FixtureJDMatcher:
    provider_name = "fixture"
    provider_model = "fixture-model"

    def __init__(self):
        self.calls = 0
        self.seen_facts = None

    def analyze(self, jd_content, facts, *, analysis_date, correction=None):
        self.calls += 1; self.seen_facts = tuple(facts)
        return {"requirements": [{
            "requirement_type": "capability",
            "normalized_text": "Python systems",
            "exact_quote": "Build Python systems.",
            "condition_clauses": [{
                "normalized_text": "Python systems",
                "exact_quote": "Build Python systems.",
            }],
            "evaluation_mode": "evidence_match", "logic_operator": "atomic",
            "support_scope": "partial", "fact_ids": [facts[0].fact_id],
            "reasoning": "A verified fact partially supports the requirement.",
        }]}


def test_match_jd_cli_end_to_end_with_mock_provider(tmp_path):
    value = _fixture(tmp_path); base = tmp_path / "base.json"
    assert main(["build-base-resume", *_chain_args(value), "--output", str(base)],
                input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    jd = tmp_path / "synthetic-jd.txt"; jd.write_text("Build Python systems.", encoding="utf-8")
    analysis = tmp_path / "analysis.json"; match = tmp_path / "match.json"; report = tmp_path / "report.md"
    matcher = FixtureJDMatcher(); messages = []
    args = ["match-jd", *_chain_args(value), "--base-resume", str(base),
            "--jd", str(jd), "--analysis-date", "2026-10-14",
            "--jd-analysis-output", str(analysis),
            "--output", str(match), "--report-output", str(report)]
    assert main(args, jd_matcher=matcher, output_fn=messages.append) == 0
    assert matcher.calls == 1 and analysis.exists() and match.exists() and report.exists()
    assert json.loads(match.read_text())["schema"] == "aarvia.jd_match_report"
    assert "owner@example.test" not in json.dumps([item.to_provider_dict() for item in matcher.seen_facts])
    assert all("owner@example.test" not in item for item in messages)
    assert main(args, jd_matcher=matcher, output_fn=lambda _: None) == 1
    assert matcher.calls == 1


def test_match_jd_provider_failure_leaves_no_outputs(tmp_path):
    value = _fixture(tmp_path); base = tmp_path / "base.json"
    assert main(["build-base-resume", *_chain_args(value), "--output", str(base)],
                input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    jd = tmp_path / "jd.txt"; jd.write_text("Build Python systems.")
    class Broken(FixtureJDMatcher):
        def analyze(self, jd_content, facts, *, analysis_date, correction=None):
            self.calls += 1
            return {"requirements": [{"invented": True}]}
    outputs = [tmp_path / "analysis.json", tmp_path / "match.json", tmp_path / "report.md"]
    args = ["match-jd", *_chain_args(value), "--base-resume", str(base), "--jd", str(jd),
            "--analysis-date", "2026-10-14",
            "--jd-analysis-output", str(outputs[0]), "--output", str(outputs[1]),
            "--report-output", str(outputs[2])]
    matcher = Broken()
    assert main(args, jd_matcher=matcher, output_fn=lambda _: None) == 1
    assert matcher.calls == 2
    assert not any(item.exists() for item in outputs)


def test_match_jd_help_lists_local_input_and_outputs(capsys):
    try:
        main(["match-jd", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    for flag in ("--jd", "--analysis-date", "--base-resume", "--jd-analysis-output", "--report-output"):
        assert flag in output
