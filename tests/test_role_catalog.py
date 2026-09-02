from copy import deepcopy
from importlib import resources
import json
from pathlib import Path

import pytest

from aarvia.role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    load_role_catalog,
    production_role_catalog,
    save_role_catalog,
)
from phase2_fixtures import catalog_fixture, catalog_fixture_data
from phase2b_fixtures import source_data


def test_production_catalog_has_taxonomy_without_unsourced_market_claims() -> None:
    catalog = production_role_catalog()

    assert len(catalog.roles) == 8
    assert catalog.requirements == ()
    assert catalog.sources == ()
    assert catalog.role("applied_ai_engineer").display_name == "Applied AI Engineer"
    assert "AI Agent Engineer" in catalog.role("applied_ai_engineer").search_title_aliases


def test_production_catalog_is_loaded_from_packaged_versioned_json() -> None:
    artifact = resources.files("aarvia").joinpath(
        "catalog_data", "role-catalog-1.0.0.json"
    )
    raw = json.loads(artifact.read_text(encoding="utf-8"))
    catalog = production_role_catalog()

    assert catalog == RoleCatalog.from_dict(raw)
    assert catalog.catalog_version == "1.0.0"
    assert catalog.schema_version == 1
    assert len(catalog.roles) == 8
    assert catalog.requirements == catalog.sources == ()

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'aarvia = ["catalog_data/*.json"]' in pyproject


def test_legacy_production_catalog_bytes_remain_immutable() -> None:
    import hashlib

    artifact = Path("src/aarvia/catalog_data/role-catalog-1.0.0.json")
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == (
        "fb846b0d468652e5006e40fe77679f469f14804dc5f5cc79a264d74a19005a90"
    )


def test_catalog_schema_two_explicitly_uses_generic_jd_sources() -> None:
    data = production_role_catalog().to_dict()
    data["schema_version"] = 2
    data["catalog_version"] = "1.1.0"
    data["catalog_type"] = "test_fixture"
    for role in data["roles"]:
        role["catalog_version"] = "1.1.0"
    data["sources"] = [source_data()]

    catalog = RoleCatalog.from_dict(data)
    assert catalog.schema_version == 2
    assert catalog.sources[0].source_url.startswith("https://")

    mixed = deepcopy(data)
    mixed["sources"] = [catalog_fixture_data()["sources"][0]]
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        RoleCatalog.from_dict(mixed)


def test_valid_catalog_round_trip_and_stable_serialization(tmp_path) -> None:
    catalog = catalog_fixture()
    first = save_role_catalog(catalog, tmp_path / "first.json")
    second = save_role_catalog(RoleCatalog.from_dict(catalog.to_dict()), tmp_path / "second.json")

    assert load_role_catalog(first) == catalog
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8"))["catalog_type"] == "test_fixture"


def test_catalog_version_is_strict_and_roles_must_match_it() -> None:
    data = catalog_fixture_data()
    data["catalog_version"] = "v1"
    with pytest.raises(Phase2ValidationError, match="semantic version"):
        RoleCatalog.from_dict(data)

    data = catalog_fixture_data()
    data["roles"][0]["catalog_version"] = "2.0.0"
    with pytest.raises(Phase2ValidationError, match="does not match"):
        RoleCatalog.from_dict(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["roles"].append(deepcopy(data["roles"][0])), "duplicate role IDs"),
        (
            lambda data: data["requirements"].append(deepcopy(data["requirements"][0])),
            "duplicate requirement IDs",
        ),
    ],
)
def test_duplicate_stable_ids_are_rejected(change, message) -> None:
    data = catalog_fixture_data()
    change(data)

    with pytest.raises(Phase2ValidationError, match=message):
        RoleCatalog.from_dict(data)


def test_unknown_specialization_and_source_references_are_rejected() -> None:
    data = catalog_fixture_data()
    data["requirements"][0]["applicable_specializations"] = ["invented_specialization"]
    with pytest.raises(Phase2ValidationError, match="unknown specializations"):
        RoleCatalog.from_dict(data)

    data = catalog_fixture_data()
    data["requirements"][0]["source_references"] = ["missing_source"]
    with pytest.raises(Phase2ValidationError, match="unknown sources"):
        RoleCatalog.from_dict(data)


@pytest.mark.parametrize("prevalence", ["common", "frequent"])
def test_unsourced_requirement_cannot_claim_market_prevalence(prevalence) -> None:
    data = catalog_fixture_data()
    data["requirements"][0]["prevalence"] = prevalence
    data["requirements"][0]["source_references"] = []

    with pytest.raises(Phase2ValidationError, match=f"cannot be {prevalence} without sources"):
        RoleCatalog.from_dict(data)


def test_production_catalog_rejects_test_fixture_sources() -> None:
    data = catalog_fixture_data()
    data["catalog_type"] = "production"

    with pytest.raises(Phase2ValidationError, match="production catalog cannot use test fixture"):
        RoleCatalog.from_dict(data)


def test_catalog_rejects_unknown_fields_and_invalid_json(tmp_path) -> None:
    data = catalog_fixture_data()
    data["future_claim"] = "unsupported"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        RoleCatalog.from_dict(data)

    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(Phase2ValidationError, match="valid JSON"):
        load_role_catalog(path)
