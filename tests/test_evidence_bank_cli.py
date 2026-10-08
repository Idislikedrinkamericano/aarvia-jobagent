from __future__ import annotations

import pytest

from aarvia.career_direction import save_user_role_decision
from aarvia.career_gap_analysis import build_career_gap_analysis, save_career_gap_analysis
from aarvia.cli import main
from aarvia.evidence_bank import load_evidence_bank
from aarvia.evidence_binding_review import save_evidence_binding_reviews
from aarvia.profile_dimension_mapping import save_mapping_candidates
from aarvia.role_recommendation import save_role_recommendation
from aarvia.storage import save_profile
from test_career_gap_analysis_v2 import _context


NOW = "2026-10-06T00:00:00+00:00"


def _cli_context(tmp_path):
    profile, rubric, catalog, mapping, reviews, recommendation, decision, gap = _context()
    paths = {
        "profile": save_profile(profile, tmp_path / "profile.json"),
        "mapping": save_mapping_candidates(
            mapping, tmp_path / "mapping.json", profile=profile, rubric=rubric, catalog=catalog
        ),
        "review": save_evidence_binding_reviews(
            reviews, tmp_path / "review.json", profile=profile, rubric=rubric,
            mapping=mapping, catalog=catalog,
        ),
        "recommendation": save_role_recommendation(
            recommendation, tmp_path / "recommendation.json", profile=profile,
            rubric=rubric, catalog=catalog, mapping_candidates=mapping,
            evidence_reviews=reviews,
        ),
        "decision": save_user_role_decision(
            decision, tmp_path / "decision.json", catalog=catalog,
            recommendation_set=recommendation, profile=profile, rubric=rubric,
        ),
    }
    paths["gap"] = save_career_gap_analysis(
        gap, tmp_path / "gap.json", profile=profile, rubric=rubric, catalog=catalog,
        mapping=mapping, recommendation=recommendation, decision=decision,
        evidence_reviews=reviews,
    )
    return {
        "profile_object": profile, "rubric": rubric, "catalog": catalog,
        "mapping_object": mapping, "review_object": reviews,
        "recommendation_object": recommendation, "decision_object": decision,
        "gap_object": gap, **paths,
    }


def _args(context, output, *extra):
    return [
        "build-evidence-bank",
        "--profile", str(context["profile"]),
        "--mapping", str(context["mapping"]),
        "--review-artifact", str(context["review"]),
        "--recommendation", str(context["recommendation"]),
        "--decision", str(context["decision"]),
        "--gap-analysis", str(context["gap"]),
        "--output", str(output),
        *extra,
    ]


def test_build_evidence_bank_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["build-evidence-bank", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--review-artifact",
        "--allocation-review-artifact", "--recommendation", "--decision",
        "--gap-analysis", "--supersede", "--output",
    ):
        assert flag in output


def test_cli_builds_without_provider_and_shows_safe_summary(tmp_path):
    context = _cli_context(tmp_path)
    output = tmp_path / "bank.json"
    messages: list[str] = []

    class NoProvider:
        def map(self, *_):
            raise AssertionError("Evidence Bank must not call a Provider")

    assert main(
        _args(context, output), input_fn=lambda _: "y",
        output_fn=messages.append, mapper=NoProvider(),
    ) == 0
    bank = load_evidence_bank(
        output,
        profile=context["profile_object"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping_object"],
        evidence_reviews=context["review_object"],
        recommendation=context["recommendation_object"],
        decision=context["decision_object"], gap_analysis=context["gap_object"],
    )
    assert bank.summary.item_count == len(bank.items)
    for label in (
        "Confirmed profile facts", "Confirmed capability support",
        "Developing evidence", "Transferable foundations",
        "Unknown evidence needs", "Explicit development needs",
    ):
        assert any(label in message for message in messages)


@pytest.mark.parametrize(
    ("input_fn", "expected_code"),
    [
        (lambda _: "q", 130),
        (lambda _: "n", 0),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_cli_cancel_paths_write_nothing(tmp_path, input_fn, expected_code):
    context = _cli_context(tmp_path)
    output = tmp_path / "bank.json"
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=input_fn, output_fn=messages.append
    ) == expected_code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_cli_preserves_existing_output_without_prompt(tmp_path):
    context = _cli_context(tmp_path)
    output = tmp_path / "bank.json"
    output.write_bytes(b"existing")
    assert main(
        _args(context, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt")),
        output_fn=lambda _: None,
    ) == 1
    assert output.read_bytes() == b"existing"


def test_cli_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    context = _cli_context(tmp_path)
    output = tmp_path / "bank.json"

    def fail_replace(*_):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=lambda _: "y", output_fn=messages.append
    ) == 1
    assert not output.exists()
    assert any("simulated replacement failure" in item for item in messages)


def test_cli_requires_review_context_referenced_by_recommendation(tmp_path):
    context = _cli_context(tmp_path)
    output = tmp_path / "bank.json"
    args = _args(context, output)
    index = args.index("--review-artifact")
    del args[index:index + 2]
    assert main(args, input_fn=lambda _: "y", output_fn=lambda _: None) == 1
    assert not output.exists()

