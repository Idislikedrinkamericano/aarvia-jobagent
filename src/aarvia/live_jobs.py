"""Strict Phase 2A contracts for externally verified job listings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .role_catalog import (
    CatalogType,
    Phase2ValidationError,
    RequirementCategory,
    RequirementImportance,
    RoleCatalog,
    SourceReference,
    SourceStatus,
    SourceType,
    _enum,
    _iso_date,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
    _version,
    _https_url,
)


class ListingStatus(str, Enum):
    VERIFIED_OPEN = "verified_open"
    POSSIBLY_OPEN = "possibly_open"
    CLOSED = "closed"
    EXPIRED = "expired"
    UNVERIFIABLE = "unverifiable"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    LIKELY_ELIGIBLE = "likely_eligible"
    CONDITIONALLY_ELIGIBLE = "conditionally_eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class PreliminaryMatchStatus(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    STRONG_COVERAGE = "strong_coverage"
    PARTIAL_COVERAGE = "partial_coverage"
    WEAK_COVERAGE = "weak_coverage"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class EmploymentType(str, Enum):
    INTERNSHIP = "internship"
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    OTHER = "other"
    UNKNOWN = "unknown"


class ApplicationURLStatus(str, Enum):
    NOT_PROVIDED = "not_provided"
    PROVIDED_UNVERIFIED = "provided_unverified"
    VERIFIED_ACTIVE = "verified_active"
    VERIFIED_UNAVAILABLE = "verified_unavailable"


@dataclass(frozen=True, eq=True)
class JobRequirement:
    job_requirement_id: str
    name: str
    description: str
    category: RequirementCategory
    importance: RequirementImportance
    explicitly_required: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "job_requirement") -> JobRequirement:
        data = _mapping(value, path)
        allowed = {
            "job_requirement_id", "name", "description", "category", "importance",
            "explicitly_required",
        }
        _reject_unknown(data, allowed, path)
        explicitly_required = data.get("explicitly_required")
        if not isinstance(explicitly_required, bool):
            raise Phase2ValidationError(f"{path}.explicitly_required must be a boolean")
        return cls(
            job_requirement_id=_stable_id(
                data.get("job_requirement_id"), f"{path}.job_requirement_id"
            ),
            name=_text(data.get("name"), f"{path}.name"),
            description=_text(data.get("description"), f"{path}.description"),
            category=_enum(data.get("category"), RequirementCategory, f"{path}.category"),
            importance=_enum(data.get("importance"), RequirementImportance, f"{path}.importance"),
            explicitly_required=explicitly_required,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_requirement_id": self.job_requirement_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "importance": self.importance.value,
            "explicitly_required": self.explicitly_required,
        }


@dataclass(frozen=True, eq=True)
class LiveJob:
    job_id: str
    company: str
    exact_job_title: str
    official_job_url: str
    official_application_url: str | None
    application_url_status: ApplicationURLStatus
    application_url_last_verified_at: str | None
    application_url_source_reference: str | None
    location: str
    employment_type: EmploymentType
    posting_date: str | None
    expiration_date: str | None
    last_verified_at: str
    listing_status: ListingStatus
    source_reference: str
    mapped_role_id: str | None
    mapped_specialization_id: str | None
    eligibility_status: EligibilityStatus
    preliminary_match_status: PreliminaryMatchStatus
    structured_jd_requirements: tuple[JobRequirement, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "job") -> LiveJob:
        data = _mapping(value, path)
        allowed = {
            "job_id", "company", "exact_job_title", "official_job_url",
            "official_application_url", "location", "employment_type", "posting_date",
            "expiration_date", "last_verified_at", "listing_status", "source_reference",
            "mapped_role_id", "mapped_specialization_id", "eligibility_status",
            "preliminary_match_status", "structured_jd_requirements", "application_url_status",
            "application_url_last_verified_at", "application_url_source_reference",
        }
        _reject_unknown(data, allowed, path)
        raw_requirements = data.get("structured_jd_requirements", [])
        if not isinstance(raw_requirements, list):
            raise Phase2ValidationError(f"{path}.structured_jd_requirements must be a list")
        requirements = tuple(
            JobRequirement.from_dict(item, f"{path}.structured_jd_requirements[{index}]")
            for index, item in enumerate(raw_requirements)
        )
        requirement_ids = [item.job_requirement_id for item in requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise Phase2ValidationError(f"{path} contains duplicate job requirement IDs")
        mapped_role_id = data.get("mapped_role_id")
        mapped_specialization_id = data.get("mapped_specialization_id")
        application_url = data.get("official_application_url")
        application_source = data.get("application_url_source_reference")
        application_verified_at = data.get("application_url_last_verified_at")
        return cls(
            job_id=_stable_id(data.get("job_id"), f"{path}.job_id"),
            company=_text(data.get("company"), f"{path}.company"),
            exact_job_title=_text(data.get("exact_job_title"), f"{path}.exact_job_title"),
            official_job_url=_https_url(data.get("official_job_url"), f"{path}.official_job_url"),
            official_application_url=(
                None
                if application_url is None
                else _https_url(application_url, f"{path}.official_application_url")
            ),
            application_url_status=_enum(
                data.get("application_url_status"),
                ApplicationURLStatus,
                f"{path}.application_url_status",
            ),
            application_url_last_verified_at=(
                None
                if application_verified_at is None
                else _iso_datetime(
                    application_verified_at, f"{path}.application_url_last_verified_at"
                )
            ),
            application_url_source_reference=(
                None
                if application_source is None
                else _stable_id(
                    application_source, f"{path}.application_url_source_reference"
                )
            ),
            location=_text(data.get("location"), f"{path}.location"),
            employment_type=_enum(
                data.get("employment_type"), EmploymentType, f"{path}.employment_type"
            ),
            posting_date=_iso_date(data.get("posting_date"), f"{path}.posting_date"),
            expiration_date=_iso_date(data.get("expiration_date"), f"{path}.expiration_date"),
            last_verified_at=_iso_datetime(data.get("last_verified_at"), f"{path}.last_verified_at"),
            listing_status=_enum(
                data.get("listing_status"), ListingStatus, f"{path}.listing_status"
            ),
            source_reference=_stable_id(
                data.get("source_reference"), f"{path}.source_reference"
            ),
            mapped_role_id=(
                None
                if mapped_role_id is None
                else _stable_id(mapped_role_id, f"{path}.mapped_role_id")
            ),
            mapped_specialization_id=(
                None
                if mapped_specialization_id is None
                else _stable_id(mapped_specialization_id, f"{path}.mapped_specialization_id")
            ),
            eligibility_status=_enum(
                data.get("eligibility_status"), EligibilityStatus, f"{path}.eligibility_status"
            ),
            preliminary_match_status=_enum(
                data.get("preliminary_match_status"),
                PreliminaryMatchStatus,
                f"{path}.preliminary_match_status",
            ),
            structured_jd_requirements=requirements,
        )

    def validate(self, sources: Mapping[str, SourceReference], catalog: RoleCatalog) -> None:
        source = sources.get(self.source_reference)
        if source is None:
            raise Phase2ValidationError(
                f"job {self.job_id} references unknown official source: {self.source_reference}"
            )
        if source.source_type not in {
            SourceType.OFFICIAL_JOB_POSTING,
            SourceType.OFFICIAL_CAREER_PAGE,
        }:
            raise Phase2ValidationError(f"job {self.job_id} requires an official hiring source")
        if source.official_url != self.official_job_url:
            raise Phase2ValidationError(f"job {self.job_id} official URL does not match its source")
        if source.company is not None and source.company != self.company:
            raise Phase2ValidationError(f"job {self.job_id} company does not match its source")
        if source.job_title is not None and source.job_title != self.exact_job_title:
            raise Phase2ValidationError(f"job {self.job_id} title does not match its source")
        if self.listing_status == ListingStatus.VERIFIED_OPEN:
            if source.source_type != SourceType.OFFICIAL_JOB_POSTING:
                raise Phase2ValidationError(
                    f"job {self.job_id} requires a specific official job posting for verified_open"
                )
            if source.status != SourceStatus.ACTIVE:
                raise Phase2ValidationError(
                    f"job {self.job_id} cannot be verified_open with source status {source.status.value}"
                )
            verified_date = self.last_verified_at[:10]
            expiration = self.expiration_date or source.expiration_date
            if expiration is not None and expiration < verified_date:
                raise Phase2ValidationError(
                    f"job {self.job_id} cannot be verified_open after its expiration date"
                )
        self._validate_application_url(sources)
        if self.mapped_role_id is None:
            if self.mapped_specialization_id is not None:
                raise Phase2ValidationError(
                    f"job {self.job_id} cannot map a specialization without a Role Family"
                )
        else:
            role = catalog.role(self.mapped_role_id)
            if self.mapped_specialization_id is not None:
                allowed = {item.specialization_id for item in role.specializations}
                if self.mapped_specialization_id not in allowed:
                    raise Phase2ValidationError(
                        f"job {self.job_id} references an unknown specialization"
                    )

    def _validate_application_url(self, sources: Mapping[str, SourceReference]) -> None:
        if self.official_application_url is None:
            if self.application_url_status != ApplicationURLStatus.NOT_PROVIDED:
                raise Phase2ValidationError(
                    f"job {self.job_id} application URL status must be not_provided when no URL exists"
                )
            if (
                self.application_url_last_verified_at is not None
                or self.application_url_source_reference is not None
            ):
                raise Phase2ValidationError(
                    f"job {self.job_id} cannot verify or source a missing application URL"
                )
            return
        if self.application_url_status == ApplicationURLStatus.NOT_PROVIDED:
            raise Phase2ValidationError(
                f"job {self.job_id} has an application URL but marks it not_provided"
            )
        if self.application_url_source_reference is None:
            raise Phase2ValidationError(
                f"job {self.job_id} application URL requires a source reference"
            )
        source = sources.get(self.application_url_source_reference)
        if source is None:
            raise Phase2ValidationError(
                f"job {self.job_id} application URL references an unknown source"
            )
        if source.source_type != SourceType.OFFICIAL_JOB_POSTING:
            raise Phase2ValidationError(
                f"job {self.job_id} application URL requires a specific official job posting source"
            )
        verified_statuses = {
            ApplicationURLStatus.VERIFIED_ACTIVE,
            ApplicationURLStatus.VERIFIED_UNAVAILABLE,
        }
        if self.application_url_status in verified_statuses:
            if self.application_url_last_verified_at is None:
                raise Phase2ValidationError(
                    f"job {self.job_id} verified application URL requires a verification timestamp"
                )
        elif self.application_url_last_verified_at is not None:
            raise Phase2ValidationError(
                f"job {self.job_id} unverified application URL cannot have a verification timestamp"
            )
        if (
            self.application_url_status == ApplicationURLStatus.VERIFIED_ACTIVE
            and source.status != SourceStatus.ACTIVE
        ):
            raise Phase2ValidationError(
                f"job {self.job_id} application URL cannot be verified_active from an inactive source"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "company": self.company,
            "exact_job_title": self.exact_job_title,
            "official_job_url": self.official_job_url,
            "official_application_url": self.official_application_url,
            "application_url_status": self.application_url_status.value,
            "application_url_last_verified_at": self.application_url_last_verified_at,
            "application_url_source_reference": self.application_url_source_reference,
            "location": self.location,
            "employment_type": self.employment_type.value,
            "posting_date": self.posting_date,
            "expiration_date": self.expiration_date,
            "last_verified_at": self.last_verified_at,
            "listing_status": self.listing_status.value,
            "source_reference": self.source_reference,
            "mapped_role_id": self.mapped_role_id,
            "mapped_specialization_id": self.mapped_specialization_id,
            "eligibility_status": self.eligibility_status.value,
            "preliminary_match_status": self.preliminary_match_status.value,
            "structured_jd_requirements": [item.to_dict() for item in self.structured_jd_requirements],
        }


@dataclass(frozen=True, eq=True)
class LiveJobCollection:
    collection_id: str
    catalog_version: str
    collection_type: CatalogType
    captured_at: str
    sources: tuple[SourceReference, ...]
    jobs: tuple[LiveJob, ...]
    schema: str = "aarvia.live_jobs"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LiveJobCollection:
        data = _mapping(value, "live_jobs")
        allowed = {
            "schema", "schema_version", "collection_id", "catalog_version",
            "collection_type", "captured_at", "sources", "jobs",
        }
        _reject_unknown(data, allowed, "live_jobs")
        if data.get("schema") != "aarvia.live_jobs" or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Live Job schema version")
        raw_sources = data.get("sources")
        raw_jobs = data.get("jobs")
        if not isinstance(raw_sources, list) or not isinstance(raw_jobs, list):
            raise Phase2ValidationError("live_jobs sources and jobs must be lists")
        collection = cls(
            collection_id=_stable_id(data.get("collection_id"), "live_jobs.collection_id"),
            catalog_version=_version(data.get("catalog_version"), "live_jobs.catalog_version"),
            collection_type=_enum(
                data.get("collection_type"), CatalogType, "live_jobs.collection_type"
            ),
            captured_at=_iso_datetime(data.get("captured_at"), "live_jobs.captured_at"),
            sources=tuple(
                SourceReference.from_dict(item, f"live_jobs.sources[{index}]")
                for index, item in enumerate(raw_sources)
            ),
            jobs=tuple(
                LiveJob.from_dict(item, f"live_jobs.jobs[{index}]")
                for index, item in enumerate(raw_jobs)
            ),
        )
        collection._validate_shape()
        return collection

    def _validate_shape(self) -> None:
        source_ids = [item.source_id for item in self.sources]
        job_ids = [item.job_id for item in self.jobs]
        if len(source_ids) != len(set(source_ids)):
            raise Phase2ValidationError("Live Job collection contains duplicate source IDs")
        if len(job_ids) != len(set(job_ids)):
            raise Phase2ValidationError("Live Job collection contains duplicate job IDs")
        if self.collection_type == CatalogType.PRODUCTION and any(
            source.is_test_fixture for source in self.sources
        ):
            raise Phase2ValidationError("production Live Job collection cannot use test fixtures")

    def validate(self, catalog: RoleCatalog) -> None:
        self._validate_shape()
        if self.catalog_version != catalog.catalog_version:
            raise Phase2ValidationError("Live Job collection catalog version does not match Role Catalog")
        sources = {item.source_id: item for item in self.sources}
        for job in self.jobs:
            job.validate(sources, catalog)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "collection_id": self.collection_id,
            "catalog_version": self.catalog_version,
            "collection_type": self.collection_type.value,
            "captured_at": self.captured_at,
            "sources": [item.to_dict() for item in self.sources],
            "jobs": [item.to_dict() for item in self.jobs],
        }


def save_live_job_collection(
    value: LiveJobCollection, path: str | Path, *, catalog: RoleCatalog
) -> Path:
    if not isinstance(value, LiveJobCollection):
        raise TypeError("value must be a LiveJobCollection")
    value.validate(catalog)
    from .phase2_storage import save_phase2_json

    return save_phase2_json(LiveJobCollection.from_dict(value.to_dict()).to_dict(), path)


def load_live_job_collection(
    path: str | Path, *, catalog: RoleCatalog
) -> LiveJobCollection:
    from .phase2_storage import load_phase2_json

    value = LiveJobCollection.from_dict(load_phase2_json(path))
    value.validate(catalog)
    return value
