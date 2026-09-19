"""Versioned capability dimensions used by Role Recommendation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
from importlib import resources
from pathlib import Path
import re
from typing import Any, Mapping

from .phase2_storage import load_phase2_json, save_phase2_json
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    _enum,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
    _version,
    production_role_catalog,
)


CAPABILITY_RUBRIC_SCHEMA = "aarvia.role_capability_rubric"
CAPABILITY_RUBRIC_SCHEMA_VERSION = 1
DIMENSION_ID_VERSION = "capability-dimension-v1"
_DISPLAY_CODE = re.compile(r"^[A-Z]+-D\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DimensionReadiness(str, Enum):
    READY = "ready_for_profile_matching"
    CONDITIONAL = "conditional_pending_evidence_review"


class MarketBasisStatus(str, Enum):
    CONFIRMED = "confirmed_market_basis"
    CONDITIONAL = "conditional_market_basis"


def generate_dimension_id(*, role_id: str, display_code: str, name: str) -> str:
    values = (
        DIMENSION_ID_VERSION,
        _stable_id(role_id, "role_id"),
        _display_code(display_code, "display_code"),
        _text(name, "name"),
    )
    return f"dimension_{hashlib.sha256('|'.join(values).encode('utf-8')).hexdigest()[:24]}"


def _display_code(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not _DISPLAY_CODE.fullmatch(result):
        raise Phase2ValidationError(f"{path} must use an uppercase code such as AI-D01")
    return result


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


@dataclass(frozen=True, eq=True)
class CapabilityDimension:
    dimension_id: str
    display_code: str
    role_id: str
    name: str
    description: str
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    analytical_category: str
    readiness: DimensionReadiness
    market_basis_status: MarketBasisStatus
    confirmed_company_count: int
    projected_company_count: int
    sample_denominator: int
    shared_dimension_key: str | None
    profile_evidence_types: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "dimension") -> CapabilityDimension:
        data = _mapping(value, path)
        allowed = {
            "dimension_id", "display_code", "role_id", "name", "description",
            "inclusion_criteria", "exclusion_criteria", "analytical_category",
            "readiness", "market_basis_status", "confirmed_company_count",
            "projected_company_count", "sample_denominator", "shared_dimension_key",
            "profile_evidence_types",
        }
        _reject_unknown(data, allowed, path)
        result = cls(
            dimension_id=_stable_id(data.get("dimension_id"), f"{path}.dimension_id"),
            display_code=_display_code(data.get("display_code"), f"{path}.display_code"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            name=_text(data.get("name"), f"{path}.name"),
            description=_text(data.get("description"), f"{path}.description"),
            inclusion_criteria=_string_tuple(data.get("inclusion_criteria"), f"{path}.inclusion_criteria"),
            exclusion_criteria=_string_tuple(data.get("exclusion_criteria"), f"{path}.exclusion_criteria"),
            analytical_category=_stable_id(data.get("analytical_category"), f"{path}.analytical_category"),
            readiness=_enum(data.get("readiness"), DimensionReadiness, f"{path}.readiness"),
            market_basis_status=_enum(
                data.get("market_basis_status"), MarketBasisStatus, f"{path}.market_basis_status"
            ),
            confirmed_company_count=_count(
                data.get("confirmed_company_count"), f"{path}.confirmed_company_count"
            ),
            projected_company_count=_count(
                data.get("projected_company_count"), f"{path}.projected_company_count"
            ),
            sample_denominator=_count(data.get("sample_denominator"), f"{path}.sample_denominator"),
            shared_dimension_key=_stable_id(
                data.get("shared_dimension_key"), f"{path}.shared_dimension_key"
            ) if data.get("shared_dimension_key") is not None else None,
            profile_evidence_types=_string_tuple(
                data.get("profile_evidence_types"), f"{path}.profile_evidence_types", ids=True
            ),
        )
        result.validate_identity(path)
        return result

    def validate_identity(self, path: str = "dimension") -> None:
        expected = generate_dimension_id(
            role_id=self.role_id, display_code=self.display_code, name=self.name
        )
        if self.dimension_id != expected:
            raise Phase2ValidationError(f"{path}.dimension_id is not deterministic")
        if not self.inclusion_criteria or not self.exclusion_criteria:
            raise Phase2ValidationError(f"{path} requires inclusion and exclusion criteria")
        if not self.profile_evidence_types:
            raise Phase2ValidationError(f"{path} requires Profile evidence types")
        if self.sample_denominator < 1:
            raise Phase2ValidationError(f"{path}.sample_denominator must be positive")
        if self.confirmed_company_count > self.projected_company_count:
            raise Phase2ValidationError(f"{path} confirmed count cannot exceed projected count")
        if self.projected_company_count > self.sample_denominator:
            raise Phase2ValidationError(f"{path} projected count cannot exceed denominator")
        expected_basis = (
            MarketBasisStatus.CONFIRMED
            if self.readiness == DimensionReadiness.READY
            else MarketBasisStatus.CONDITIONAL
        )
        if self.market_basis_status != expected_basis:
            raise Phase2ValidationError(f"{path} readiness and market basis are inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "display_code": self.display_code,
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "inclusion_criteria": list(self.inclusion_criteria),
            "exclusion_criteria": list(self.exclusion_criteria),
            "analytical_category": self.analytical_category,
            "readiness": self.readiness.value,
            "market_basis_status": self.market_basis_status.value,
            "confirmed_company_count": self.confirmed_company_count,
            "projected_company_count": self.projected_company_count,
            "sample_denominator": self.sample_denominator,
            "shared_dimension_key": self.shared_dimension_key,
            "profile_evidence_types": list(self.profile_evidence_types),
        }


@dataclass(frozen=True, eq=True)
class CapabilityRubric:
    rubric_version: str
    role_catalog_version: str
    as_of_date: str
    supported_role_ids: tuple[str, ...]
    dimensions: tuple[CapabilityDimension, ...]
    source_analysis_hashes: tuple[str, ...]
    provenance_summary: str
    schema: str = CAPABILITY_RUBRIC_SCHEMA
    schema_version: int = CAPABILITY_RUBRIC_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityRubric:
        data = _mapping(value, "capability_rubric")
        allowed = {
            "schema", "schema_version", "rubric_version", "role_catalog_version",
            "as_of_date", "supported_role_ids", "dimensions", "source_analysis_hashes",
            "provenance_summary",
        }
        _reject_unknown(data, allowed, "capability_rubric")
        if data.get("schema") != CAPABILITY_RUBRIC_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Capability Rubric schema version")
        try:
            date.fromisoformat(str(data.get("as_of_date")))
        except ValueError as error:
            raise Phase2ValidationError("capability_rubric.as_of_date must use YYYY-MM-DD") from error
        raw_dimensions = data.get("dimensions")
        if not isinstance(raw_dimensions, list):
            raise Phase2ValidationError("capability_rubric.dimensions must be a list")
        hashes = _string_tuple(data.get("source_analysis_hashes"), "capability_rubric.source_analysis_hashes")
        if not hashes or any(not _SHA256.fullmatch(value) for value in hashes):
            raise Phase2ValidationError("source analysis hashes must be SHA-256 values")
        result = cls(
            rubric_version=_version(data.get("rubric_version"), "capability_rubric.rubric_version"),
            role_catalog_version=_version(
                data.get("role_catalog_version"), "capability_rubric.role_catalog_version"
            ),
            as_of_date=str(data.get("as_of_date")),
            supported_role_ids=_string_tuple(
                data.get("supported_role_ids"), "capability_rubric.supported_role_ids", ids=True
            ),
            dimensions=tuple(
                CapabilityDimension.from_dict(item, f"capability_rubric.dimensions[{index}]")
                for index, item in enumerate(raw_dimensions)
            ),
            source_analysis_hashes=hashes,
            provenance_summary=_text(
                data.get("provenance_summary"), "capability_rubric.provenance_summary"
            ),
        )
        result._validate_shape()
        return result

    def _validate_shape(self) -> None:
        if len(set(self.supported_role_ids)) != len(self.supported_role_ids):
            raise Phase2ValidationError("Capability Rubric contains duplicate supported roles")
        ids = [item.dimension_id for item in self.dimensions]
        codes = [item.display_code for item in self.dimensions]
        if len(ids) != len(set(ids)):
            raise Phase2ValidationError("Capability Rubric contains duplicate dimension IDs")
        if len(codes) != len(set(codes)):
            raise Phase2ValidationError("Capability Rubric contains duplicate display codes")
        dimension_roles = {item.role_id for item in self.dimensions}
        if dimension_roles != set(self.supported_role_ids):
            raise Phase2ValidationError("Capability Rubric supported roles do not match dimensions")

    def validate(self, catalog: RoleCatalog) -> None:
        self._validate_shape()
        if self.role_catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Capability Rubric Role Catalog version mismatch")
        for role_id in self.supported_role_ids:
            catalog.role(role_id)
        for index, dimension in enumerate(self.dimensions):
            dimension.validate_identity(f"capability_rubric.dimensions[{index}]")
            catalog.role(dimension.role_id)

    def dimension(self, dimension_id: str) -> CapabilityDimension:
        for item in self.dimensions:
            if item.dimension_id == dimension_id:
                return item
        raise Phase2ValidationError(f"unknown capability dimension: {dimension_id}")

    def role_dimensions(self, role_id: str) -> tuple[CapabilityDimension, ...]:
        if role_id not in self.supported_role_ids:
            raise Phase2ValidationError(f"unsupported rubric role: {role_id}")
        return tuple(item for item in self.dimensions if item.role_id == role_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "rubric_version": self.rubric_version,
            "role_catalog_version": self.role_catalog_version,
            "as_of_date": self.as_of_date,
            "supported_role_ids": list(self.supported_role_ids),
            "dimensions": [item.to_dict() for item in self.dimensions],
            "source_analysis_hashes": list(self.source_analysis_hashes),
            "provenance_summary": self.provenance_summary,
        }


def load_capability_rubric(
    path: str | Path, *, catalog: RoleCatalog | None = None
) -> CapabilityRubric:
    value = CapabilityRubric.from_dict(load_phase2_json(path))
    value.validate(catalog or production_role_catalog())
    return value


def save_capability_rubric(
    value: CapabilityRubric, path: str | Path, *, catalog: RoleCatalog | None = None
) -> Path:
    if not isinstance(value, CapabilityRubric):
        raise TypeError("value must be a CapabilityRubric")
    value.validate(catalog or production_role_catalog())
    return save_phase2_json(CapabilityRubric.from_dict(value.to_dict()).to_dict(), path)


def production_capability_rubric() -> CapabilityRubric:
    resource = resources.files("aarvia").joinpath("catalog_data/role-capability-rubric-1.0.0.json")
    with resources.as_file(resource) as path:
        return load_capability_rubric(path, catalog=production_role_catalog())
