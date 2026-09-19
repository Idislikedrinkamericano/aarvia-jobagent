from copy import deepcopy
import json

import pytest

from aarvia.capability_rubric import (
    CapabilityRubric,
    generate_dimension_id,
    load_capability_rubric,
    production_capability_rubric,
    save_capability_rubric,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog


def test_production_capability_rubric_loads_all_mvp_dimensions() -> None:
    rubric = production_capability_rubric()
    assert rubric.rubric_version == "1.0.0"
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
    data["dimensions"][1]["dimension_id"] = generate_dimension_id(
        role_id=data["dimensions"][1]["role_id"],
        display_code=data["dimensions"][1]["display_code"],
        name=data["dimensions"][1]["name"],
    )


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
    assert json.loads(first.read_text())["schema_version"] == 1


def test_rubric_validates_roles_against_catalog() -> None:
    rubric = production_capability_rubric()
    rubric.validate(production_role_catalog())


def test_packaged_rubric_contains_only_public_aggregate_provenance() -> None:
    from importlib import resources
    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-1.0.0.json")
    text = resource.read_text(encoding="utf-8")
    assert "local_data" not in text
    assert "http://" not in text and "https://" not in text
