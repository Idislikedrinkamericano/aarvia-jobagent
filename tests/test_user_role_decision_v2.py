from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.capability_rubric import capability_rubric_fingerprint
from aarvia.career_direction import (
    DecisionStatus,
    SelectedRole,
    UserRoleDecision,
    create_user_role_decision,
    generate_user_role_decision_id,
    load_user_role_decision,
    save_user_role_decision,
)
from aarvia.profile import CareerProfile
from aarvia.role_catalog import Phase2ValidationError
from test_recommendation_ranking_v7 import _ranking_fixture


NOW = "2026-10-04T00:00:00+00:00"
LATER = "2026-10-05T00:00:00+00:00"


def _decision_context(*, include_research: bool = False):
    profile, rubric, catalog, mapping, reviews, recommendation = _ranking_fixture(
        include_research=include_research
    )
    return profile, rubric, catalog, mapping, reviews, recommendation


def _decision(**changes):
    profile, rubric, catalog, _, _, recommendation = _decision_context()
    values = {
        "status": DecisionStatus.CONFIRMED,
        "catalog": catalog,
        "rubric": rubric,
        "profile": profile,
        "recommendation_set": recommendation,
        "primary_role": SelectedRole("machine_learning_engineer"),
        "secondary_roles": (SelectedRole("applied_ai_engineer"),),
        "rejected_role_ids": ("research_engineer",),
        "explore_later_role_ids": (),
        "user_reason": "Build production machine learning systems.",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return create_user_role_decision(**values), profile, rubric, catalog, recommendation


def test_schema_two_round_trip_and_deterministic_id():
    decision, profile, rubric, catalog, recommendation = _decision()

    assert decision.schema_version == 2
    assert decision.source_recommendation_schema_version == 7
    assert decision.source_recommendation_reference == recommendation.recommendation_set_id
    assert decision.profile_fingerprint == recommendation.profile_fingerprint
    assert decision.rubric_version == rubric.rubric_version
    assert decision.rubric_fingerprint == capability_rubric_fingerprint(rubric)
    assert decision.decision_id == generate_user_role_decision_id(decision)
    assert UserRoleDecision.from_dict(decision.to_dict()) == decision
    decision.validate(catalog, recommendation, profile, rubric)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision_id", "decision_forged"),
        ("source_recommendation_reference", "recommendations_forged"),
        ("source_recommendation_schema_version", 6),
        ("profile_fingerprint", "sha256:" + "0" * 64),
        ("rubric_version", "9.9.9"),
        ("rubric_fingerprint", "sha256:" + "1" * 64),
    ],
)
def test_schema_two_typed_load_rejects_identity_and_provenance_tampering(field, value):
    decision, *_ = _decision()
    raw = decision.to_dict()
    raw[field] = value
    with pytest.raises(Phase2ValidationError):
        UserRoleDecision.from_dict(raw)


def test_direct_construction_cannot_bypass_deterministic_id():
    decision, profile, rubric, catalog, recommendation = _decision()
    tampered = replace(decision, decision_id="decision_forged")
    with pytest.raises(Phase2ValidationError, match="not deterministic"):
        tampered.validate(catalog, recommendation, profile, rubric)


def test_schema_two_rejects_unknown_fields():
    decision, *_ = _decision()
    raw = decision.to_dict()
    raw["provider_selected_primary"] = True
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        UserRoleDecision.from_dict(raw)


def test_confirmed_deferred_and_draft_shape_rules():
    with pytest.raises(Phase2ValidationError, match="confirmed"):
        _decision(primary_role=None)
    with pytest.raises(Phase2ValidationError, match="deferred"):
        _decision(status=DecisionStatus.DEFERRED)

    deferred, *_ = _decision(
        status=DecisionStatus.DEFERRED,
        primary_role=None,
        secondary_roles=(),
        rejected_role_ids=(),
        explore_later_role_ids=("applied_ai_engineer",),
    )
    assert deferred.primary_role is None
    assert deferred.status == DecisionStatus.DEFERRED

    draft, *_ = _decision(
        status=DecisionStatus.DRAFT,
        primary_role=None,
        secondary_roles=(),
        rejected_role_ids=(),
    )
    assert draft.status == DecisionStatus.DRAFT


@pytest.mark.parametrize(
    "changes",
    [
        {"secondary_roles": (SelectedRole("applied_ai_engineer"),) * 2},
        {"rejected_role_ids": ("research_engineer", "research_engineer")},
        {
            "rejected_role_ids": ("research_engineer",),
            "explore_later_role_ids": ("research_engineer",),
        },
        {"explore_later_role_ids": ("machine_learning_engineer",)},
        {
            "secondary_roles": (
                SelectedRole("applied_ai_engineer"),
                SelectedRole("research_engineer"),
                SelectedRole("data_engineer"),
            )
        },
    ],
)
def test_schema_two_role_sets_are_unique_bounded_and_fully_disjoint(changes):
    with pytest.raises(Phase2ValidationError):
        _decision(**changes)


def test_schema_two_specialization_must_belong_to_role():
    with pytest.raises(Phase2ValidationError, match="unknown specializations"):
        _decision(primary_role=SelectedRole("machine_learning_engineer", ("agentic_ai",)))


def test_confirmed_modification_creates_explicit_revision():
    original, profile, rubric, catalog, recommendation = _decision()
    revised = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("applied_ai_engineer"),
        secondary_roles=(SelectedRole("machine_learning_engineer"),),
        explore_later_role_ids=("research_engineer",),
        user_reason="Prefer applied product work.",
        created_at=LATER,
        updated_at=LATER,
        superseded_decision=original,
    )

    assert revised.decision_id != original.decision_id
    assert revised.supersedes_decision_id == original.decision_id
    revised.validate(catalog, recommendation, profile, rubric, original)
    with pytest.raises(Phase2ValidationError, match="requires the confirmed"):
        revised.validate(catalog, recommendation, profile, rubric)


def test_revision_rejects_wrong_or_nonconfirmed_prior_decision():
    original, profile, rubric, catalog, recommendation = _decision()
    revised = create_user_role_decision(
        status=DecisionStatus.CONFIRMED,
        catalog=catalog,
        rubric=rubric,
        profile=profile,
        recommendation_set=recommendation,
        primary_role=SelectedRole("applied_ai_engineer"),
        created_at=LATER,
        updated_at=LATER,
        superseded_decision=original,
    )
    draft = replace(
        original,
        status=DecisionStatus.DRAFT,
        primary_role=None,
        decision_id="decision_pending",
    )
    draft = replace(draft, decision_id=generate_user_role_decision_id(draft))
    with pytest.raises(Phase2ValidationError, match="confirmed"):
        revised.validate(catalog, recommendation, profile, rubric, draft)


def test_profile_rubric_catalog_and_recommendation_changes_make_decision_stale():
    decision, profile, rubric, catalog, recommendation = _decision()
    changed_profile = CareerProfile.from_dict(profile.to_dict())
    changed_profile.basic_profile.current_status = "Working"
    with pytest.raises(Phase2ValidationError, match="Profile fingerprint"):
        decision.validate(catalog, recommendation, changed_profile, rubric)

    changed_rubric = replace(rubric, rubric_version="9.9.9")
    with pytest.raises(Phase2ValidationError, match="Rubric"):
        decision.validate(catalog, recommendation, profile, changed_rubric)

    changed_rubric_content = replace(
        rubric, provenance_summary=rubric.provenance_summary + " Changed."
    )
    with pytest.raises(Phase2ValidationError, match="fingerprint"):
        decision.validate(
            catalog, recommendation, profile, changed_rubric_content
        )

    changed_catalog = replace(catalog, catalog_version="9.9.9")
    with pytest.raises(Phase2ValidationError, match="Catalog|catalog"):
        decision.validate(changed_catalog, recommendation, profile, rubric)

    *_, changed_recommendation = _decision_context(include_research=True)
    with pytest.raises(Phase2ValidationError, match="different Recommendation"):
        decision.validate(catalog, changed_recommendation, profile, rubric)


def test_schema_two_file_round_trip_and_stale_load(tmp_path):
    decision, profile, rubric, catalog, recommendation = _decision()
    path = save_user_role_decision(
        decision,
        tmp_path / "decision.json",
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
    )
    assert load_user_role_decision(
        path,
        catalog=catalog,
        recommendation_set=recommendation,
        profile=profile,
        rubric=rubric,
    ) == decision

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["decision_id"] = "decision_forged"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="not deterministic"):
        load_user_role_decision(
            path,
            catalog=catalog,
            recommendation_set=recommendation,
            profile=profile,
            rubric=rubric,
        )


def test_schema_two_save_never_overwrites_existing_decision(tmp_path):
    decision, profile, rubric, catalog, recommendation = _decision()
    path = tmp_path / "decision.json"
    original = b'{"existing": true}\n'
    path.write_bytes(original)
    with pytest.raises(FileExistsError, match="immutable"):
        save_user_role_decision(
            decision,
            path,
            catalog=catalog,
            recommendation_set=recommendation,
            profile=profile,
            rubric=rubric,
        )
    assert path.read_bytes() == original


def test_schema_one_wire_round_trip_remains_unchanged():
    from test_career_direction import decision_data

    value = UserRoleDecision.from_dict(decision_data())
    assert value.schema_version == 1
    assert "profile_fingerprint" not in value.to_dict()
    assert "rubric_fingerprint" not in value.to_dict()
    assert UserRoleDecision.from_dict(value.to_dict()) == value
