from __future__ import annotations

import json

import pytest

from aarvia.career_direction import (
    DecisionStatus,
    SelectedRole,
    create_user_role_decision,
    load_user_role_decision,
    save_user_role_decision,
)
from aarvia.cli import main
from aarvia.storage import save_profile
from test_recommendation_ranking_v7 import _ranking_fixture


NOW = "2026-01-01T00:00:00+00:00"


def _cli_context(tmp_path):
    profile, rubric, catalog, _, _, recommendation = _ranking_fixture()
    profile_path = save_profile(profile, tmp_path / "profile.json")
    recommendation_path = tmp_path / "recommendation.json"
    recommendation_path.write_text(
        json.dumps(recommendation.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return profile, rubric, catalog, recommendation, profile_path, recommendation_path


def _input(values):
    iterator = iter(values)
    return lambda _: next(iterator)


def _args(profile_path, recommendation_path, output_path, *extra):
    return [
        "decide",
        "--profile",
        str(profile_path),
        "--recommendation",
        str(recommendation_path),
        "--output",
        str(output_path),
        *extra,
    ]


def test_decide_cli_saves_confirmed_v15_shape_without_provider(tmp_path):
    profile, rubric, catalog, recommendation, profile_path, recommendation_path = (
        _cli_context(tmp_path)
    )
    output = tmp_path / "decision.json"
    messages: list[str] = []

    class NoProvider:
        def map(self, *_):
            raise AssertionError("decide must not call the Provider")

    exit_code = main(
        _args(profile_path, recommendation_path, output),
        input_fn=_input(["c", "1", "2", "3", "", "Prefer ML systems.", "y"]),
        output_fn=messages.append,
        mapper=NoProvider(),
    )

    assert exit_code == 0
    decision = load_user_role_decision(
        output,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
    )
    assert decision.status == DecisionStatus.CONFIRMED
    assert decision.primary_role.role_id == "machine_learning_engineer"
    assert tuple(item.role_id for item in decision.secondary_roles) == (
        "applied_ai_engineer",
    )
    assert decision.rejected_role_ids == ("research_engineer",)
    assert any("Core Supported" in item for item in messages)
    assert any("Extended Only" in item for item in messages)
    assert any("Provisional: Yes" in item for item in messages)


def test_decide_cli_can_defer_and_mark_roles_explore_later(tmp_path):
    profile, rubric, catalog, recommendation, profile_path, recommendation_path = (
        _cli_context(tmp_path)
    )
    output = tmp_path / "deferred.json"
    assert main(
        _args(profile_path, recommendation_path, output),
        input_fn=_input(["d", "", "2,3", "Decide after more evidence.", "y"]),
        output_fn=lambda _: None,
    ) == 0
    decision = load_user_role_decision(
        output,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
    )
    assert decision.status == DecisionStatus.DEFERRED
    assert decision.primary_role is None
    assert decision.explore_later_role_ids == (
        "applied_ai_engineer",
        "research_engineer",
    )


@pytest.mark.parametrize(
    ("input_fn", "expected_code"),
    [
        (_input(["q"]), 130),
        (_input(["c", "1", "", "", "", "", "n"]), 0),
        (lambda _: (_ for _ in ()).throw(EOFError), 130),
        (lambda _: (_ for _ in ()).throw(KeyboardInterrupt), 130),
    ],
)
def test_decide_cli_cancel_paths_write_nothing(tmp_path, input_fn, expected_code):
    *_, profile_path, recommendation_path = _cli_context(tmp_path)
    output = tmp_path / "decision.json"
    messages: list[str] = []
    assert main(
        _args(profile_path, recommendation_path, output),
        input_fn=input_fn,
        output_fn=messages.append,
    ) == expected_code
    assert not output.exists()
    assert any("No files were changed" in item for item in messages)


def test_decide_cli_requires_new_output_and_preserves_existing_file(tmp_path):
    *_, profile_path, recommendation_path = _cli_context(tmp_path)
    output = tmp_path / "decision.json"
    original = b'{"existing": true}\n'
    output.write_bytes(original)
    messages: list[str] = []
    assert main(
        _args(profile_path, recommendation_path, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt expected")),
        output_fn=messages.append,
    ) == 1
    assert output.read_bytes() == original
    assert any("already exists" in item for item in messages)


def test_decide_cli_explicitly_supersedes_confirmed_decision_to_new_path(tmp_path):
    profile, rubric, catalog, recommendation, profile_path, recommendation_path = (
        _cli_context(tmp_path)
    )
    original = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("machine_learning_engineer"),
        created_at=NOW,
        updated_at=NOW,
    )
    original_path = save_user_role_decision(
        original,
        tmp_path / "original.json",
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
    )
    revised_path = tmp_path / "revised.json"
    assert main(
        _args(
            profile_path,
            recommendation_path,
            revised_path,
            "--supersede",
            str(original_path),
        ),
        input_fn=_input(["c", "2", "1", "", "3", "Prefer applied AI.", "y"]),
        output_fn=lambda _: None,
    ) == 0
    revised = load_user_role_decision(
        revised_path,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
        superseded_decision=original,
    )
    assert revised.supersedes_decision_id == original.decision_id
    assert revised.primary_role.role_id == "applied_ai_engineer"
    assert revised.explore_later_role_ids == ("research_engineer",)
    assert original_path.read_bytes() == json.dumps(
        original.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"


def test_decide_cli_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    *_, profile_path, recommendation_path = _cli_context(tmp_path)
    output = tmp_path / "decision.json"

    def fail_replace(*_):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    messages: list[str] = []
    assert main(
        _args(profile_path, recommendation_path, output),
        input_fn=_input(["c", "1", "", "", "", "", "y"]),
        output_fn=messages.append,
    ) == 1
    assert not output.exists()
    assert not list(tmp_path.glob("tmp*"))
    assert any("simulated replacement failure" in item for item in messages)


def test_decide_cli_rejects_non_schema_seven_recommendation(tmp_path):
    *_, profile_path, recommendation_path = _cli_context(tmp_path)
    raw = json.loads(recommendation_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 6
    for role in raw["role_results"]:
        role.pop("ranking_tier")
    recommendation_path.write_text(json.dumps(raw), encoding="utf-8")
    output = tmp_path / "decision.json"
    assert main(
        _args(profile_path, recommendation_path, output),
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no prompt expected")),
        output_fn=lambda _: None,
    ) == 1
    assert not output.exists()
