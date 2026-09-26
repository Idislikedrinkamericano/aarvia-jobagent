from copy import deepcopy
from dataclasses import replace
import json

import pytest

from aarvia.capability_rubric import (
    CapabilityRubric,
    EvidenceClass,
    EvidenceStatusCap,
    generate_criterion_id,
    generate_dimension_id,
    load_capability_rubric,
    production_capability_rubric,
    save_capability_rubric,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog


def test_production_capability_rubric_loads_all_mvp_dimensions() -> None:
    rubric = production_capability_rubric()
    assert rubric.rubric_version == "2.0.0"
    assert rubric.schema_version == 2
    assert [len(rubric.role_dimensions(role)) for role in rubric.supported_role_ids] == [7, 7, 6]
    assert sum(item.readiness.value == "ready_for_profile_matching" for item in rubric.dimensions) == 7
    assert sum(item.market_basis_status.value == "conditional_market_basis" for item in rubric.dimensions) == 13


def test_dimension_ids_are_deterministic() -> None:
    dimension = production_capability_rubric().dimensions[0]
    assert dimension.dimension_id == generate_dimension_id(
        role_id=dimension.role_id, display_code=dimension.display_code, name=dimension.name
    )


def _duplicate_display_code(data) -> None:
    data["dimensions"][1]["display_code"] = data["dimensions"][0]["display_code"]
    old_ids = [item["criterion_id"] for item in data["dimensions"][1]["inclusion_criteria"]]
    data["dimensions"][1]["dimension_id"] = generate_dimension_id(
        role_id=data["dimensions"][1]["role_id"],
        display_code=data["dimensions"][1]["display_code"],
        name=data["dimensions"][1]["name"],
    )
    new_ids = []
    for criterion in data["dimensions"][1]["inclusion_criteria"]:
        criterion["criterion_id"] = generate_criterion_id(
            dimension_id=data["dimensions"][1]["dimension_id"],
            criterion=criterion["text"],
        )
        new_ids.append(criterion["criterion_id"])
    remap = dict(zip(old_ids, new_ids, strict=True))
    policy = data["dimensions"][1]["evidence_support_policy"]
    for rule_name in ("confirmed_partial_rule", "confirmed_demonstrated_rule"):
        policy[rule_name]["required_criterion_sets"] = [
            [remap[criterion_id] for criterion_id in criterion_set]
            for criterion_set in policy[rule_name]["required_criterion_sets"]
        ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["dimensions"].append(deepcopy(data["dimensions"][0])), "duplicate dimension"),
        (_duplicate_display_code, "duplicate display"),
        (lambda data: data["dimensions"][0].update(role_id="missing_role"), "deterministic"),
        (lambda data: data["dimensions"][0].update(confirmed_company_count=9), "confirmed count"),
        (lambda data: data["dimensions"][0].update(projected_company_count=9), "denominator"),
        (lambda data: data["dimensions"][0].update(market_basis_status="confirmed_market_basis"), "inconsistent"),
    ],
)
def test_rubric_rejects_invalid_identity_counts_and_readiness(change, message) -> None:
    data = production_capability_rubric().to_dict()
    change(data)
    with pytest.raises(Phase2ValidationError, match=message):
        CapabilityRubric.from_dict(data)


def test_rubric_rejects_unknown_fields() -> None:
    data = production_capability_rubric().to_dict()
    data["dimensions"][0]["invented"] = True
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        CapabilityRubric.from_dict(data)


def test_rubric_round_trip_and_deterministic_save(tmp_path) -> None:
    rubric = production_capability_rubric()
    first = save_capability_rubric(rubric, tmp_path / "first.json")
    second = save_capability_rubric(rubric, tmp_path / "second.json")
    assert first.read_bytes() == second.read_bytes()
    assert load_capability_rubric(first) == rubric
    assert json.loads(first.read_text())["schema_version"] == 2


def test_rubric_validates_roles_against_catalog() -> None:
    rubric = production_capability_rubric()
    rubric.validate(production_role_catalog())


def test_packaged_rubric_contains_only_public_aggregate_provenance() -> None:
    from importlib import resources
    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-2.0.0.json")
    text = resource.read_text(encoding="utf-8")
    assert "local_data" not in text
    assert "http://" not in text and "https://" not in text


def test_every_dimension_has_stable_criteria_and_complete_policy() -> None:
    rubric = production_capability_rubric()
    assert len(rubric.dimensions) == 20
    for dimension in rubric.dimensions:
        assert dimension.evidence_support_policy is not None
        assert len(dimension.criteria) == len(dimension.inclusion_criteria)
        assert dimension.criteria
        for criterion in dimension.criteria:
            assert criterion.criterion_id == generate_criterion_id(
                dimension_id=dimension.dimension_id, criterion=criterion.text
            )
        policy = dimension.evidence_support_policy
        assert set(policy.allowed_evidence_classes) | set(policy.forbidden_evidence_classes) == set(
            EvidenceClass
        )
        assert set(policy.structural_caps) == set(EvidenceClass)
        assert policy.strong_recommendation_requires_confirmed_evidence is True


def test_criterion_ids_do_not_depend_on_criterion_order() -> None:
    data = production_capability_rubric().to_dict()
    before = {
        item["text"]: item["criterion_id"]
        for item in data["dimensions"][0]["inclusion_criteria"]
    }
    data["dimensions"][0]["inclusion_criteria"].reverse()
    loaded = CapabilityRubric.from_dict(data)
    assert {item.text: item.criterion_id for item in loaded.dimensions[0].criteria} == before


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda policy: policy["confirmed_partial_rule"]["required_criterion_sets"][0].__setitem__(
                0, "criterion_missing"
            ),
            "outside its Dimension",
        ),
        (
            lambda policy: policy["confirmed_partial_rule"].update(required_criterion_sets=[]),
            "non-empty list",
        ),
        (
            lambda policy: policy["structural_status_caps"].update(
                skill_name="demonstrated"
            ),
            "structural evidence",
        ),
        (
            lambda policy: policy["allowed_evidence_classes"].append("invented_evidence"),
            "must be one of",
        ),
        (
            lambda policy: policy["confirmed_demonstrated_rule"].update(
                minimum_independent_evidence_count=0
            ),
            "must be positive",
        ),
    ],
)
def test_schema_two_rejects_invalid_policy(mutate, message) -> None:
    data = production_capability_rubric().to_dict()
    mutate(data["dimensions"][0]["evidence_support_policy"])
    with pytest.raises(Phase2ValidationError, match=message):
        CapabilityRubric.from_dict(data)


def test_schema_one_remains_explicit_and_round_trips_without_policy(tmp_path) -> None:
    from importlib import resources

    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-1.0.0.json")
    original = json.loads(resource.read_text(encoding="utf-8"))
    rubric = CapabilityRubric.from_dict(original)
    assert rubric.schema_version == 1
    assert all(not dimension.criterion_ids for dimension in rubric.dimensions)
    assert all(dimension.evidence_support_policy is None for dimension in rubric.dimensions)
    assert rubric.to_dict() == original
    path = save_capability_rubric(rubric, tmp_path / "schema-1.json")
    assert load_capability_rubric(path) == rubric
    assert json.loads(path.read_text()) == original


def test_schema_two_preserves_schema_one_dimension_meaning_and_market_statistics() -> None:
    from importlib import resources

    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-1.0.0.json")
    legacy = CapabilityRubric.from_dict(json.loads(resource.read_text(encoding="utf-8")))
    current = production_capability_rubric()
    assert len(legacy.dimensions) == len(current.dimensions) == 20
    for old, new in zip(legacy.dimensions, current.dimensions, strict=True):
        assert old.dimension_id == new.dimension_id
        assert old.display_code == new.display_code
        assert old.role_id == new.role_id
        assert old.name == new.name
        assert old.description == new.description
        assert old.inclusion_criteria == new.inclusion_criteria
        assert old.exclusion_criteria == new.exclusion_criteria
        assert old.analytical_category == new.analytical_category
        assert old.readiness == new.readiness
        assert old.market_basis_status == new.market_basis_status
        assert old.confirmed_company_count == new.confirmed_company_count
        assert old.projected_company_count == new.projected_company_count
        assert old.sample_denominator == new.sample_denominator
        assert old.shared_dimension_key == new.shared_dimension_key
        assert old.profile_evidence_types == new.profile_evidence_types


def test_schema_two_typed_load_rejects_tampering(tmp_path) -> None:
    path = save_capability_rubric(production_capability_rubric(), tmp_path / "rubric.json")
    data = json.loads(path.read_text())
    data["dimensions"][0]["inclusion_criteria"][0]["criterion_id"] = "criterion_forged"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="not deterministic"):
        load_capability_rubric(path)


def test_directly_constructed_policy_cannot_bypass_strong_types() -> None:
    rubric = production_capability_rubric()
    dimension = rubric.dimensions[0]
    assert dimension.evidence_support_policy is not None
    forged_policy = replace(
        dimension.evidence_support_policy,
        provisional_status_cap="partially_demonstrated",
    )
    forged_dimension = replace(dimension, evidence_support_policy=forged_policy)
    forged_rubric = replace(rubric, dimensions=(forged_dimension, *rubric.dimensions[1:]))
    with pytest.raises(Phase2ValidationError, match="must use EvidenceStatusCap"):
        forged_rubric.validate(production_role_catalog())


def test_directly_constructed_rubric_rejects_schema_boolean() -> None:
    rubric = replace(production_capability_rubric(), schema_version=True)
    with pytest.raises(Phase2ValidationError, match="unsupported Capability Rubric schema"):
        rubric.validate(production_role_catalog())


def test_structural_caps_never_claim_partial_or_demonstrated() -> None:
    for dimension in production_capability_rubric().dimensions:
        policy = dimension.evidence_support_policy
        assert policy is not None
        assert set(policy.structural_caps.values()) <= {
            EvidenceStatusCap.UNKNOWN,
            EvidenceStatusCap.ADJACENT_TRANSFERABLE,
        }
