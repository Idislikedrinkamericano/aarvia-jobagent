"""Versioned role taxonomy, requirements, and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from importlib import resources
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, TypeVar
from urllib.parse import urlparse


class Phase2ValidationError(ValueError):
    """Raised when a Phase 2 contract fails strict validation."""


class CatalogType(str, Enum):
    PRODUCTION = "production"
    TEST_FIXTURE = "test_fixture"


class SourceType(str, Enum):
    OFFICIAL_JOB_POSTING = "official_job_posting"
    OFFICIAL_CAREER_PAGE = "official_career_page"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    TEST_FIXTURE = "test_fixture"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    EXPIRED = "expired"
    UNVERIFIABLE = "unverifiable"
    TEST_FIXTURE = "test_fixture"


class RequirementCategory(str, Enum):
    TECHNICAL_SKILL = "technical_skill"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    RESPONSIBILITY = "responsibility"
    COLLABORATION = "collaboration"
    DELIVERY = "delivery"
    INFRASTRUCTURE = "infrastructure"


class RequirementImportance(str, Enum):
    CORE = "core"
    SUPPORTING = "supporting"
    OPTIONAL = "optional"


class RequirementPrevalence(str, Enum):
    COMMON = "common"
    FREQUENT = "frequent"
    VARIABLE = "variable"
    UNKNOWN = "unknown"


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_T = TypeVar("_T", bound=Enum)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Phase2ValidationError(f"{path} must be a dictionary")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise Phase2ValidationError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _text(value: Any, path: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        expected = "a non-empty string" if required else "a non-empty string or null"
        raise Phase2ValidationError(f"{path} must be {expected}")
    return value.strip()


def _stable_id(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not _ID_PATTERN.fullmatch(result):
        raise Phase2ValidationError(
            f"{path} must be a stable lowercase ID using letters, numbers, '.', '_' or '-'"
        )
    return result


def _version(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    if not _VERSION_PATTERN.fullmatch(result):
        raise Phase2ValidationError(f"{path} must use semantic version format X.Y.Z")
    return result


def _enum(value: Any, enum_type: type[_T], path: str) -> _T:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(item.value for item in enum_type)
        raise Phase2ValidationError(f"{path} must be one of: {choices}") from error


def _string_tuple(value: Any, path: str, *, ids: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list of strings")
    result = tuple(
        _stable_id(item, f"{path}[{index}]") if ids else _text(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise Phase2ValidationError(f"{path} contains duplicate values")
    return result  # type: ignore[return-value]


def _iso_date(value: Any, path: str, *, required: bool = False) -> str | None:
    result = _text(value, path, required=required)
    if result is None:
        return None
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise Phase2ValidationError(f"{path} must use YYYY-MM-DD format") from error
    return result


def _iso_datetime(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise Phase2ValidationError(f"{path} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise Phase2ValidationError(f"{path} must include a timezone")
    return result


def _https_url(value: Any, path: str) -> str:
    result = _text(value, path)
    assert result is not None
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise Phase2ValidationError(f"{path} must be an HTTPS URL without credentials")
    return result


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    return {value for value in values if value in seen or seen.add(value)}


@dataclass(frozen=True, eq=True)
class SourceReference:
    source_id: str
    source_type: SourceType
    company: str | None
    job_title: str | None
    official_url: str
    captured_date: str
    publication_date: str | None = None
    expiration_date: str | None = None
    notes: str | None = None
    status: SourceStatus = SourceStatus.ACTIVE
    is_test_fixture: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "source") -> SourceReference:
        data = _mapping(value, path)
        allowed = {
            "source_id", "source_type", "company", "job_title", "official_url",
            "captured_date", "publication_date", "expiration_date", "notes",
            "status", "is_test_fixture",
        }
        _reject_unknown(data, allowed, path)
        fixture = data.get("is_test_fixture", False)
        if not isinstance(fixture, bool):
            raise Phase2ValidationError(f"{path}.is_test_fixture must be a boolean")
        source = cls(
            source_id=_stable_id(data.get("source_id"), f"{path}.source_id"),
            source_type=_enum(data.get("source_type"), SourceType, f"{path}.source_type"),
            company=_text(data.get("company"), f"{path}.company", required=False),
            job_title=_text(data.get("job_title"), f"{path}.job_title", required=False),
            official_url=_https_url(data.get("official_url"), f"{path}.official_url"),
            captured_date=_iso_date(data.get("captured_date"), f"{path}.captured_date", required=True),
            publication_date=_iso_date(data.get("publication_date"), f"{path}.publication_date"),
            expiration_date=_iso_date(data.get("expiration_date"), f"{path}.expiration_date"),
            notes=_text(data.get("notes"), f"{path}.notes", required=False),
            status=_enum(data.get("status", "active"), SourceStatus, f"{path}.status"),
            is_test_fixture=fixture,
        )
        if source.source_type == SourceType.OFFICIAL_JOB_POSTING and (
            source.company is None or source.job_title is None
        ):
            raise Phase2ValidationError(
                f"{path} official_job_posting requires company and job_title"
            )
        if (
            source.source_type == SourceType.TEST_FIXTURE
            or source.status == SourceStatus.TEST_FIXTURE
        ) and not source.is_test_fixture:
            raise Phase2ValidationError(f"{path} test fixture source must set is_test_fixture")
        return source

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "company": self.company,
            "job_title": self.job_title,
            "official_url": self.official_url,
            "captured_date": self.captured_date,
            "publication_date": self.publication_date,
            "expiration_date": self.expiration_date,
            "notes": self.notes,
            "status": self.status.value,
            "is_test_fixture": self.is_test_fixture,
        }


@dataclass(frozen=True, eq=True)
class RoleSpecialization:
    specialization_id: str
    display_name: str
    description: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "specialization") -> RoleSpecialization:
        data = _mapping(value, path)
        _reject_unknown(data, {"specialization_id", "display_name", "description"}, path)
        return cls(
            specialization_id=_stable_id(data.get("specialization_id"), f"{path}.specialization_id"),
            display_name=_text(data.get("display_name"), f"{path}.display_name"),
            description=_text(data.get("description"), f"{path}.description", required=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "specialization_id": self.specialization_id,
            "display_name": self.display_name,
            "description": self.description,
        }


@dataclass(frozen=True, eq=True)
class RoleRequirement:
    requirement_id: str
    role_id: str
    name: str
    description: str
    category: RequirementCategory
    importance: RequirementImportance
    prevalence: RequirementPrevalence
    applicable_specializations: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()
    notes: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "requirement") -> RoleRequirement:
        data = _mapping(value, path)
        allowed = {
            "requirement_id", "role_id", "name", "description", "category",
            "importance", "prevalence", "applicable_specializations",
            "source_references", "notes",
        }
        _reject_unknown(data, allowed, path)
        return cls(
            requirement_id=_stable_id(data.get("requirement_id"), f"{path}.requirement_id"),
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            name=_text(data.get("name"), f"{path}.name"),
            description=_text(data.get("description"), f"{path}.description"),
            category=_enum(data.get("category"), RequirementCategory, f"{path}.category"),
            importance=_enum(data.get("importance"), RequirementImportance, f"{path}.importance"),
            prevalence=_enum(data.get("prevalence"), RequirementPrevalence, f"{path}.prevalence"),
            applicable_specializations=_string_tuple(
                data.get("applicable_specializations"), f"{path}.applicable_specializations", ids=True
            ),
            source_references=_string_tuple(
                data.get("source_references"), f"{path}.source_references", ids=True
            ),
            notes=_text(data.get("notes"), f"{path}.notes", required=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "importance": self.importance.value,
            "prevalence": self.prevalence.value,
            "applicable_specializations": list(self.applicable_specializations),
            "source_references": list(self.source_references),
            "notes": self.notes,
        }


@dataclass(frozen=True, eq=True)
class RoleFamily:
    role_id: str
    display_name: str
    description: str
    specializations: tuple[RoleSpecialization, ...]
    search_title_aliases: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    catalog_version: str
    source_references: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "role") -> RoleFamily:
        data = _mapping(value, path)
        allowed = {
            "role_id", "display_name", "description", "specializations",
            "search_title_aliases", "requirement_ids", "catalog_version",
            "source_references",
        }
        _reject_unknown(data, allowed, path)
        raw_specializations = data.get("specializations", [])
        if not isinstance(raw_specializations, list):
            raise Phase2ValidationError(f"{path}.specializations must be a list")
        specializations = tuple(
            RoleSpecialization.from_dict(item, f"{path}.specializations[{index}]")
            for index, item in enumerate(raw_specializations)
        )
        duplicate_specs = _duplicates(item.specialization_id for item in specializations)
        if duplicate_specs:
            raise Phase2ValidationError(
                f"{path}.specializations contains duplicate IDs: {', '.join(sorted(duplicate_specs))}"
            )
        return cls(
            role_id=_stable_id(data.get("role_id"), f"{path}.role_id"),
            display_name=_text(data.get("display_name"), f"{path}.display_name"),
            description=_text(data.get("description"), f"{path}.description"),
            specializations=specializations,
            search_title_aliases=_string_tuple(
                data.get("search_title_aliases"), f"{path}.search_title_aliases"
            ),
            requirement_ids=_string_tuple(data.get("requirement_ids"), f"{path}.requirement_ids", ids=True),
            catalog_version=_version(data.get("catalog_version"), f"{path}.catalog_version"),
            source_references=_string_tuple(
                data.get("source_references"), f"{path}.source_references", ids=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "display_name": self.display_name,
            "description": self.description,
            "specializations": [item.to_dict() for item in self.specializations],
            "search_title_aliases": list(self.search_title_aliases),
            "requirement_ids": list(self.requirement_ids),
            "catalog_version": self.catalog_version,
            "source_references": list(self.source_references),
        }


@dataclass(frozen=True, eq=True)
class RoleCatalog:
    catalog_version: str
    catalog_type: CatalogType
    roles: tuple[RoleFamily, ...]
    requirements: tuple[RoleRequirement, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    schema: str = "aarvia.role_catalog"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RoleCatalog:
        data = _mapping(value, "catalog")
        allowed = {
            "schema", "schema_version", "catalog_version", "catalog_type",
            "roles", "requirements", "sources",
        }
        _reject_unknown(data, allowed, "catalog")
        if data.get("schema") != "aarvia.role_catalog" or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Role Catalog schema version")
        catalog_version = _version(data.get("catalog_version"), "catalog.catalog_version")
        catalog_type = _enum(data.get("catalog_type"), CatalogType, "catalog.catalog_type")
        roles = _model_tuple(data.get("roles"), RoleFamily, "catalog.roles")
        requirements = _model_tuple(data.get("requirements"), RoleRequirement, "catalog.requirements")
        sources = _model_tuple(data.get("sources"), SourceReference, "catalog.sources")
        catalog = cls(catalog_version, catalog_type, roles, requirements, sources)
        catalog.validate()
        return catalog

    def validate(self) -> None:
        role_ids = [role.role_id for role in self.roles]
        requirement_ids = [item.requirement_id for item in self.requirements]
        source_ids = [source.source_id for source in self.sources]
        for label, values in (
            ("role ID", role_ids),
            ("requirement ID", requirement_ids),
            ("source ID", source_ids),
        ):
            duplicates = _duplicates(values)
            if duplicates:
                raise Phase2ValidationError(
                    f"catalog contains duplicate {label}s: {', '.join(sorted(duplicates))}"
                )
        role_map = {role.role_id: role for role in self.roles}
        requirement_map = {item.requirement_id: item for item in self.requirements}
        source_map = {source.source_id: source for source in self.sources}
        if self.catalog_type == CatalogType.PRODUCTION:
            fixtures = [source.source_id for source in self.sources if source.is_test_fixture]
            if fixtures:
                raise Phase2ValidationError(
                    f"production catalog cannot use test fixture sources: {', '.join(fixtures)}"
                )
        for role in self.roles:
            if role.catalog_version != self.catalog_version:
                raise Phase2ValidationError(
                    f"role {role.role_id} catalog_version does not match catalog"
                )
            _require_references(role.source_references, source_map, f"role {role.role_id}")
            for requirement_id in role.requirement_ids:
                requirement = requirement_map.get(requirement_id)
                if requirement is None:
                    raise Phase2ValidationError(
                        f"role {role.role_id} references unknown requirement: {requirement_id}"
                    )
                if requirement.role_id != role.role_id:
                    raise Phase2ValidationError(
                        f"requirement {requirement_id} belongs to {requirement.role_id}, not {role.role_id}"
                    )
        linked_requirement_ids = {
            requirement_id for role in self.roles for requirement_id in role.requirement_ids
        }
        unlinked = set(requirement_map) - linked_requirement_ids
        if unlinked:
            raise Phase2ValidationError(
                f"catalog contains requirements not linked by their role: {', '.join(sorted(unlinked))}"
            )
        for requirement in self.requirements:
            role = role_map.get(requirement.role_id)
            if role is None:
                raise Phase2ValidationError(
                    f"requirement {requirement.requirement_id} references unknown role: {requirement.role_id}"
                )
            specializations = {item.specialization_id for item in role.specializations}
            unknown_specs = set(requirement.applicable_specializations) - specializations
            if unknown_specs:
                raise Phase2ValidationError(
                    f"requirement {requirement.requirement_id} references unknown specializations: "
                    f"{', '.join(sorted(unknown_specs))}"
                )
            _require_references(
                requirement.source_references,
                source_map,
                f"requirement {requirement.requirement_id}",
            )
            if requirement.prevalence in {
                RequirementPrevalence.COMMON,
                RequirementPrevalence.FREQUENT,
            } and not requirement.source_references:
                raise Phase2ValidationError(
                    f"requirement {requirement.requirement_id} cannot be {requirement.prevalence.value} without sources"
                )
            if self.catalog_type == CatalogType.PRODUCTION and not requirement.source_references:
                raise Phase2ValidationError(
                    f"production requirement {requirement.requirement_id} requires provenance"
                )

    def role(self, role_id: str) -> RoleFamily:
        for role in self.roles:
            if role.role_id == role_id:
                return role
        raise Phase2ValidationError(f"unknown role ID: {role_id}")

    def requirement(self, requirement_id: str) -> RoleRequirement:
        for requirement in self.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        raise Phase2ValidationError(f"unknown requirement ID: {requirement_id}")

    def source(self, source_id: str) -> SourceReference:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise Phase2ValidationError(f"unknown source ID: {source_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "catalog_version": self.catalog_version,
            "catalog_type": self.catalog_type.value,
            "roles": [role.to_dict() for role in self.roles],
            "requirements": [item.to_dict() for item in self.requirements],
            "sources": [source.to_dict() for source in self.sources],
        }


def _model_tuple(value: Any, model: type[Any], path: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise Phase2ValidationError(f"{path} must be a list")
    return tuple(model.from_dict(item, f"{path}[{index}]") for index, item in enumerate(value))


def _require_references(
    references: Iterable[str], sources: Mapping[str, SourceReference], path: str
) -> None:
    missing = sorted(set(references) - set(sources))
    if missing:
        raise Phase2ValidationError(f"{path} references unknown sources: {', '.join(missing)}")


def save_role_catalog(catalog: RoleCatalog, path: str | Path) -> Path:
    """Validate and atomically save a deterministic Role Catalog document."""
    if not isinstance(catalog, RoleCatalog):
        raise TypeError("catalog must be a RoleCatalog")
    validated = RoleCatalog.from_dict(catalog.to_dict())
    from .phase2_storage import save_phase2_json

    return save_phase2_json(validated.to_dict(), path)


def load_role_catalog(path: str | Path) -> RoleCatalog:
    from .phase2_storage import load_phase2_json

    return RoleCatalog.from_dict(load_phase2_json(path))


def production_role_catalog() -> RoleCatalog:
    """Load the packaged, source-free Phase 2A production taxonomy."""
    artifact = resources.files("aarvia").joinpath(
        "catalog_data", "role-catalog-1.0.0.json"
    )
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise Phase2ValidationError("packaged production Role Catalog is missing") from error
    except json.JSONDecodeError as error:
        raise Phase2ValidationError("packaged production Role Catalog is invalid JSON") from error
    catalog = RoleCatalog.from_dict(raw)
    if catalog.catalog_version != "1.0.0" or catalog.catalog_type != CatalogType.PRODUCTION:
        raise Phase2ValidationError("packaged production Role Catalog identity is invalid")
    return catalog
