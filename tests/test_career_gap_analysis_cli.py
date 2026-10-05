from __future__ import annotations

import json

import pytest

from aarvia.career_direction import (
    DecisionStatus,
    SelectedRole,
    create_user_role_decision,
    save_user_role_decision,
)
from aarvia.career_gap_analysis import load_career_gap_analysis
from aarvia.cli import main
from aarvia.evidence_binding_review import save_evidence_binding_reviews
from aarvia.profile_dimension_mapping import save_mapping_candidates
from aarvia.role_recommendation import save_role_recommendation
from aarvia.storage import save_profile
from test_recommendation_ranking_v7 import _ranking_fixture


NOW = "2026-10-05T00:00:00+00:00"


def _context(tmp_path):
    profile, rubric, catalog, mapping, reviews, recommendation = _ranking_fixture()
    decision = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        secondary_roles=(SelectedRole("applied_ai_engineer"),),
        explore_later_role_ids=("research_engineer",),
        created_at=NOW,
        updated_at=NOW,
    )
    profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile, rubric=rubric, catalog=catalog
    )
    review_path = save_evidence_binding_reviews(
        reviews, tmp_path / "review.json", profile=profile, rubric=rubric,
        mapping=mapping, catalog=catalog,
    )
    recommendation_path = save_role_recommendation(
        recommendation, tmp_path / "recommendation.json", profile=profile,
        rubric=rubric, catalog=catalog, mapping_candidates=mapping,
        evidence_reviews=reviews,
    )
    decision_path = save_user_role_decision(
        decision, tmp_path / "decision.json", catalog=catalog,
        recommendation_set=recommendation, profile=profile, rubric=rubric,
    )
    return {
        "profile": profile,
        "rubric": rubric,
        "catalog": catalog,
        "mapping": mapping,
        "reviews": reviews,
        "recommendation": recommendation,
        "decision": decision,
        "profile_path": profile_path,
        "mapping_path": mapping_path,
        "review_path": review_path,
        "recommendation_path": recommendation_path,
        "decision_path": decision_path,
    }


def _args(context, output, *extra):
    return [
        "analyze-gaps",
        "--profile", str(context["profile_path"]),
        "--mapping", str(context["mapping_path"]),
        "--recommendation", str(context["recommendation_path"]),
        "--decision", str(context["decision_path"]),
        "--review-artifact", str(context["review_path"]),
        "--output", str(output),
        *extra,
    ]


def test_analyze_gaps_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["analyze-gaps", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--profile", "--mapping", "--recommendation", "--decision",
        "--review-artifact", "--allocation-review-artifact", "--output",
    ):
        assert flag in output


def test_analyze_gaps_saves_real_decision_shape_without_provider(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "gaps.json"
    messages: list[str] = []

    class NoProvider:
        def map(self, *_):
            raise AssertionError("Gap Analysis must not call the Provider")

    assert main(
        _args(context, output), input_fn=lambda _: "y", output_fn=messages.append,
        mapper=NoProvider(),
    ) == 0
    gap = load_career_gap_analysis(
        output, profile=context["profile"], rubric=context["rubric"],
        catalog=context["catalog"], mapping=context["mapping"],
        recommendation=context["recommendation"], decision=context["decision"],
        evidence_reviews=context["reviews"],
    )
    assert gap.primary_role_analysis.role_id == "machine_learning_engineer"
    assert tuple(item.role_id for item in gap.secondary_role_analyses) == (
        "applied_ai_engineer",
    )
    assert "research_engineer" not in json.dumps(gap.to_dict())
    assert any("Confirmed strengths" in item for item in messages)
    assert any("Development areas" in item for item in messages)
    assert any("Transferable foundations" in item for item in messages)
    assert any("Unknown evidence" in item for item in messages)
    assert not any("dimension_" in item for item in messages)


@pytest.mark.parametrize(
    ("input_fn", "expected_code"),
    [
        (lambda _: "q", 130),
        (lambda _: "n", 0),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_analyze_gaps_cancel_paths_write_nothing(
    tmp_path, input_fn, expected_code
):
    context = _context(tmp_path)
    output = tmp_path / "gaps.json"
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=input_fn, output_fn=messages.append
    ) == expected_code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_analyze_gaps_requires_review_referenced_by_recommendation(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "gaps.json"
    args = _args(context, output)
    review_index = args.index("--review-artifact")
    del args[review_index:review_index + 2]
    messages: list[str] = []
    assert main(args, input_fn=lambda _: "y", output_fn=messages.append) == 1
    assert not output.exists()
    assert any("Evidence Review context" in item for item in messages)


def test_analyze_gaps_preserves_existing_output(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "gaps.json"
    output.write_bytes(b"existing")
    messages: list[str] = []
    assert main(
        _args(context, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt")),
        output_fn=messages.append,
    ) == 1
    assert output.read_bytes() == b"existing"


def test_analyze_gaps_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    context = _context(tmp_path)
    output = tmp_path / "gaps.json"

    def fail_replace(*_):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    messages: list[str] = []
    assert main(
        _args(context, output), input_fn=lambda _: "y", output_fn=messages.append
    ) == 1
    assert not output.exists()
    assert not list(tmp_path.glob("tmp*"))
    assert any("simulated replacement failure" in item for item in messages)
