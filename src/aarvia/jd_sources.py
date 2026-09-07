"""Schema 2 contracts for JD source identity, verification, and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
from pathlib import Path
import re
import unicodedata
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .role_catalog import (
    CatalogType,
    Phase2ValidationError,
    _enum,
    _https_url,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
)


SOURCE_SCHEMA = "aarvia.jd_sources"
SOURCE_SCHEMA_VERSION = 2
SOURCE_SCHEMA_V3_VERSION = 3
CONTENT_HASH_NORMALIZATION_VERSION = "jd-text-v1"
IDENTITY_NORMALIZATION_VERSION = "jd-identity-v1"
CAPTURE_ID_VERSION = "jd-capture-v1"


class SourceTier(str, Enum):
    TIER_A_OFFICIAL = "tier_a_official"
    TIER_B_VERIFIED_PLATFORM = "tier_b_verified_platform"
    TIER_C_DISCOVERY_ONLY = "tier_c_discovery_only"


class JDSourceType(str, Enum):
    OFFICIAL_JOB_POSTING = "official_job_posting"
    OFFICIAL_CAREER_PAGE = "official_career_page"
    PLATFORM_JOB_POSTING = "platform_job_posting"
    AGGREGATOR_LISTING = "aggregator_listing"
    SEARCH_RESULT = "search_result"


class PlatformName(str, Enum):
    COMPANY_CAREERS = "company_careers"
    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    OTHER_VERIFIED_PLATFORM = "other_verified_platform"
    OTHER = "other"


class SourceLifecycleStatus(str, Enum):
    DISCOVERED = "discovered"
    CAPTURED = "captured"
    VERIFIED = "verified"
    UNVERIFIABLE = "unverifiable"
    ARCHIVED = "archived"
    EXPIRED = "expired"


class Market(str, Enum):
    UNITED_STATES_EARLY_CAREER = "united_states_early_career"


class CareerStage(str, Enum):
    INTERNSHIP = "internship"
    NEW_GRAD = "new_grad"
    EARLY_CAREER = "early_career"


class ExperienceRangeStatus(str, Enum):
    EXPLICIT = "explicit"
    NOT_STATED = "not_stated"
    AMBIGUOUS = "ambiguous"


class VerificationMethod(str, Enum):
    OFFICIAL_PAGE_DIRECT = "official_page_direct"
    OFFICIAL_ATS_DIRECT = "official_ats_direct"
    PLATFORM_APPLY_AVAILABLE = "platform_apply_available"
    PLATFORM_REDIRECT_TO_OFFICIAL = "platform_redirect_to_official"
    MANUAL_REVIEW = "manual_review"
    NOT_VERIFIED = "not_verified"


class ListingStatus(str, Enum):
    VERIFIED_OFFICIAL_OPEN = "verified_official_open"
    VERIFIED_PLATFORM_OPEN = "verified_platform_open"
    POSSIBLY_OPEN = "possibly_open"
    CLOSED = "closed"
    EXPIRED = "expired"
    UNVERIFIABLE = "unverifiable"


class CaptureScope(str, Enum):
    STATUS_ONLY = "status_only"
    PARTIAL_EXCERPT = "partial_excerpt"
    FULL_JOB_DESCRIPTION = "full_job_description"
    LEGACY_UNSPECIFIED = "legacy_unspecified"


def normalize_jd_content(
    content: str, *, version: str = CONTENT_HASH_NORMALIZATION_VERSION
) -> str:
    """Normalize captured JD text according to an immutable named rule set."""
    if version != CONTENT_HASH_NORMALIZATION_VERSION:
        raise Phase2ValidationError(f"unsupported content hash normalization version: {version}")
    if not isinstance(content, str):
        raise Phase2ValidationError("JD content must be a string")
    normalized = unicodedata.normalize("NFKC", content).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def content_sha256(
    content: str, *, version: str = CONTENT_HASH_NORMALIZATION_VERSION
) -> str:
    payload = normalize_jd_content(content, version=version).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def normalize_source_url(url: str) -> str:
    value = _https_url(url, "source_url")
    parts = urlsplit(value)
    query = [
        pair for pair in parse_qsl(parts.query, keep_blank_values=True)
        if not pair[0].lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def _digest_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def generate_source_id(
    source_url: str, *, platform_name: str | None = None, platform_job_id: str | None = None
) -> str:
    identity = "|".join(
        (
            IDENTITY_NORMALIZATION_VERSION,
            normalize_source_url(source_url),
            platform_name or "",
            platform_job_id or "",
        )
    )
    return _digest_id("src", identity)


def generate_canonical_job_id(
    *,
    company_id: str,
    requisition_id: str | None,
    canonical_url: str | None,
    platform_name: str | None,
    platform_job_id: str | None,
) -> str:
    company = _stable_id(company_id, "company_id")
    if requisition_id:
        key = f"requisition|{company}|{requisition_id.strip().casefold()}"
    elif canonical_url:
        key = f"url|{company}|{normalize_source_url(canonical_url)}"
    elif platform_name and platform_job_id:
        key = f"platform|{company}|{platform_name}|{platform_job_id.strip()}"
    else:
        raise Phase2ValidationError("canonical job identity requires a verified stable identifier")
    return _digest_id("job", f"{IDENTITY_NORMALIZATION_VERSION}|{key}")


@dataclass(frozen=True, eq=True)
class ExperienceRange:
    status: ExperienceRangeStatus
    minimum_years: int | None = None
    maximum_years: int | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "experience_range") -> ExperienceRange:
        data = _mapping(value, path)
        _reject_unknown(data, {"status", "minimum_years", "maximum_years"}, path)
        minimum = data.get("minimum_years")
        maximum = data.get("maximum_years")
        for name, item in (("minimum_years", minimum), ("maximum_years", maximum)):
            if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
                raise Phase2ValidationError(f"{path}.{name} must be a non-negative integer or null")
        result = cls(_enum(data.get("status"), ExperienceRangeStatus, f"{path}.status"), minimum, maximum)
        if result.status == ExperienceRangeStatus.EXPLICIT:
            if minimum is None and maximum is None:
                raise Phase2ValidationError(f"{path} explicit range requires a stated bound")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise Phase2ValidationError(f"{path} minimum cannot exceed maximum")
        elif minimum is not None or maximum is not None:
            raise Phase2ValidationError(f"{path} non-explicit range cannot contain numeric bounds")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "minimum_years": self.minimum_years,
            "maximum_years": self.maximum_years,
        }


@dataclass(frozen=True, eq=True)
class JDSource:
    source_id: str
    source_url: str
    source_type: JDSourceType
    source_tier: SourceTier
    source_status: SourceLifecycleStatus
    company_id: str
    company_display_name: str
    exact_job_title: str | None
    platform_name: PlatformName
    platform_job_id: str | None
    requisition_id: str | None
    application_url: str | None
    location: str
    country_code: str
    market: Market
    career_stage: CareerStage
    experience_range: ExperienceRange
    canonical_job_id: str | None
    canonical_source_reference: str | None
    discovery_source_references: tuple[str, ...]
    captured_at: str
    last_verified_at: str | None
    verification_method: VerificationMethod
    content_hash: str | None
    hash_normalization_version: str
    identity_normalization_version: str = IDENTITY_NORMALIZATION_VERSION
    is_test_fixture: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str = "source") -> JDSource:
        data = _mapping(value, path)
        allowed = {
            "source_id", "source_url", "source_type", "source_tier", "source_status",
            "company_id", "company_display_name", "exact_job_title", "platform_name",
            "platform_job_id", "requisition_id", "application_url", "location", "country_code", "market",
            "career_stage", "experience_range", "canonical_job_id",
            "canonical_source_reference", "discovery_source_references", "captured_at",
            "last_verified_at", "verification_method", "content_hash",
            "hash_normalization_version", "identity_normalization_version", "is_test_fixture",
        }
        _reject_unknown(data, allowed, path)
        fixture = data.get("is_test_fixture", False)
        if not isinstance(fixture, bool):
            raise Phase2ValidationError(f"{path}.is_test_fixture must be a boolean")
        source_url = normalize_source_url(data.get("source_url"))
        platform = _enum(data.get("platform_name"), PlatformName, f"{path}.platform_name")
        platform_job_id = _text(data.get("platform_job_id"), f"{path}.platform_job_id", required=False)
        source_id = _stable_id(data.get("source_id"), f"{path}.source_id")
        expected_source_id = generate_source_id(
            source_url, platform_name=platform.value, platform_job_id=platform_job_id
        )
        if source_id != expected_source_id:
            raise Phase2ValidationError(f"{path}.source_id was not generated by Aarvia identity rules")
        country = _text(data.get("country_code"), f"{path}.country_code")
        assert country is not None
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise Phase2ValidationError(f"{path}.country_code must be ISO alpha-2 uppercase")
        content_hash = _text(data.get("content_hash"), f"{path}.content_hash", required=False)
        if content_hash is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
            raise Phase2ValidationError(f"{path}.content_hash must be a SHA-256 digest")
        application_url = data.get("application_url")
        result = cls(
            source_id=source_id,
            source_url=source_url,
            source_type=_enum(data.get("source_type"), JDSourceType, f"{path}.source_type"),
            source_tier=_enum(data.get("source_tier"), SourceTier, f"{path}.source_tier"),
            source_status=_enum(
                data.get("source_status"), SourceLifecycleStatus, f"{path}.source_status"
            ),
            company_id=_stable_id(data.get("company_id"), f"{path}.company_id"),
            company_display_name=_text(data.get("company_display_name"), f"{path}.company_display_name"),
            exact_job_title=_text(data.get("exact_job_title"), f"{path}.exact_job_title", required=False),
            platform_name=platform,
            platform_job_id=platform_job_id,
            requisition_id=_text(data.get("requisition_id"), f"{path}.requisition_id", required=False),
            application_url=(None if application_url is None else _https_url(application_url, f"{path}.application_url")),
            location=_text(data.get("location"), f"{path}.location"),
            country_code=country,
            market=_enum(data.get("market"), Market, f"{path}.market"),
            career_stage=_enum(data.get("career_stage"), CareerStage, f"{path}.career_stage"),
            experience_range=ExperienceRange.from_dict(data.get("experience_range"), f"{path}.experience_range"),
            canonical_job_id=(None if data.get("canonical_job_id") is None else _stable_id(data.get("canonical_job_id"), f"{path}.canonical_job_id")),
            canonical_source_reference=(None if data.get("canonical_source_reference") is None else _stable_id(data.get("canonical_source_reference"), f"{path}.canonical_source_reference")),
            discovery_source_references=_string_tuple(data.get("discovery_source_references"), f"{path}.discovery_source_references", ids=True),
            captured_at=_iso_datetime(data.get("captured_at"), f"{path}.captured_at"),
            last_verified_at=(None if data.get("last_verified_at") is None else _iso_datetime(data.get("last_verified_at"), f"{path}.last_verified_at")),
            verification_method=_enum(data.get("verification_method"), VerificationMethod, f"{path}.verification_method"),
            content_hash=content_hash,
            hash_normalization_version=_text(data.get("hash_normalization_version"), f"{path}.hash_normalization_version"),
            identity_normalization_version=_text(data.get("identity_normalization_version"), f"{path}.identity_normalization_version"),
            is_test_fixture=fixture,
        )
        result._validate_shape(path)
        return result

    def _validate_shape(self, path: str) -> None:
        if self.hash_normalization_version != CONTENT_HASH_NORMALIZATION_VERSION:
            raise Phase2ValidationError(f"{path} uses unsupported content hash normalization")
        if self.identity_normalization_version != IDENTITY_NORMALIZATION_VERSION:
            raise Phase2ValidationError(f"{path} uses unsupported identity normalization")
        if self.country_code != "US" or self.market != Market.UNITED_STATES_EARLY_CAREER:
            raise Phase2ValidationError(f"{path} is outside the Phase 2B-1 US market")
        if self.experience_range.status == ExperienceRangeStatus.EXPLICIT and any(
            bound is not None and bound > 2
            for bound in (
                self.experience_range.minimum_years,
                self.experience_range.maximum_years,
            )
        ):
            raise Phase2ValidationError(f"{path} explicitly requires more than two years experience")
        if self.source_tier == SourceTier.TIER_A_OFFICIAL:
            if self.source_type not in {JDSourceType.OFFICIAL_JOB_POSTING, JDSourceType.OFFICIAL_CAREER_PAGE}:
                raise Phase2ValidationError(f"{path} Tier A requires an official source type")
            if self.platform_name not in {
                PlatformName.COMPANY_CAREERS, PlatformName.WORKDAY, PlatformName.GREENHOUSE,
                PlatformName.LEVER, PlatformName.ASHBY, PlatformName.OTHER,
            }:
                raise Phase2ValidationError(f"{path} Tier A uses an invalid platform")
            if self.source_status == SourceLifecycleStatus.VERIFIED and self.verification_method not in {
                VerificationMethod.OFFICIAL_PAGE_DIRECT,
                VerificationMethod.OFFICIAL_ATS_DIRECT,
            }:
                raise Phase2ValidationError(f"{path} verified Tier A source requires official verification")
        elif self.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM:
            if self.source_type != JDSourceType.PLATFORM_JOB_POSTING:
                raise Phase2ValidationError(f"{path} Tier B requires a platform job posting")
            if self.platform_name in {
                PlatformName.COMPANY_CAREERS, PlatformName.WORKDAY, PlatformName.GREENHOUSE,
                PlatformName.LEVER, PlatformName.ASHBY, PlatformName.INDEED, PlatformName.OTHER,
            }:
                raise Phase2ValidationError(f"{path} Tier B requires a verified hiring platform")
            if not self.platform_job_id or not self.exact_job_title or not self.application_url:
                raise Phase2ValidationError(
                    f"{path} Tier B requires company, exact posting, platform job ID, and application URL"
                )
            if self.last_verified_at is None or self.verification_method not in {
                VerificationMethod.PLATFORM_APPLY_AVAILABLE,
                VerificationMethod.PLATFORM_REDIRECT_TO_OFFICIAL,
            }:
                raise Phase2ValidationError(f"{path} Tier B requires explicit platform verification")
            if self.source_status != SourceLifecycleStatus.VERIFIED:
                raise Phase2ValidationError(f"{path} Tier B source must be verified")
        else:
            if self.source_type in {JDSourceType.OFFICIAL_JOB_POSTING, JDSourceType.PLATFORM_JOB_POSTING} and self.source_status == SourceLifecycleStatus.VERIFIED:
                raise Phase2ValidationError(f"{path} verified posting cannot be silently downgraded to Tier C")
        if self.source_status in {SourceLifecycleStatus.CAPTURED, SourceLifecycleStatus.VERIFIED, SourceLifecycleStatus.ARCHIVED, SourceLifecycleStatus.EXPIRED} and self.content_hash is None:
            raise Phase2ValidationError(f"{path} captured source requires a content hash")
        if self.source_status == SourceLifecycleStatus.VERIFIED and self.last_verified_at is None:
            raise Phase2ValidationError(f"{path} verified source requires last_verified_at")
        if self.last_verified_at is not None:
            captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
            verified = datetime.fromisoformat(self.last_verified_at.replace("Z", "+00:00"))
            if verified < captured:
                raise Phase2ValidationError(f"{path}.last_verified_at cannot precede captured_at")
        if self.canonical_job_id is not None and self.canonical_source_reference in {
            None,
            self.source_id,
        }:
            expected = generate_canonical_job_id(
                company_id=self.company_id,
                requisition_id=self.requisition_id,
                canonical_url=self.source_url if self.source_tier == SourceTier.TIER_A_OFFICIAL else None,
                platform_name=self.platform_name.value,
                platform_job_id=self.platform_job_id,
            )
            if self.canonical_job_id != expected:
                raise Phase2ValidationError(f"{path}.canonical_job_id was not generated by Aarvia rules")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_type": self.source_type.value,
            "source_tier": self.source_tier.value,
            "source_status": self.source_status.value,
            "company_id": self.company_id,
            "company_display_name": self.company_display_name,
            "exact_job_title": self.exact_job_title,
            "platform_name": self.platform_name.value,
            "platform_job_id": self.platform_job_id,
            "requisition_id": self.requisition_id,
            "application_url": self.application_url,
            "location": self.location,
            "country_code": self.country_code,
            "market": self.market.value,
            "career_stage": self.career_stage.value,
            "experience_range": self.experience_range.to_dict(),
            "canonical_job_id": self.canonical_job_id,
            "canonical_source_reference": self.canonical_source_reference,
            "discovery_source_references": list(self.discovery_source_references),
            "captured_at": self.captured_at,
            "last_verified_at": self.last_verified_at,
            "verification_method": self.verification_method.value,
            "content_hash": self.content_hash,
            "hash_normalization_version": self.hash_normalization_version,
            "identity_normalization_version": self.identity_normalization_version,
            "is_test_fixture": self.is_test_fixture,
        }


@dataclass(frozen=True, eq=True)
class JDSourceCollection:
    collection_id: str
    collection_type: CatalogType
    created_at: str
    sources: tuple[JDSource, ...]
    schema: str = SOURCE_SCHEMA
    schema_version: int = SOURCE_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JDSourceCollection:
        data = _mapping(value, "jd_sources")
        if data.get("schema") != SOURCE_SCHEMA or data.get("schema_version") != SOURCE_SCHEMA_VERSION:
            raise Phase2ValidationError("unsupported JD Source schema version")
        _reject_unknown(data, {"schema", "schema_version", "collection_id", "collection_type", "created_at", "sources"}, "jd_sources")
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, list):
            raise Phase2ValidationError("jd_sources.sources must be a list")
        result = cls(
            collection_id=_stable_id(data.get("collection_id"), "jd_sources.collection_id"),
            collection_type=_enum(data.get("collection_type"), CatalogType, "jd_sources.collection_type"),
            created_at=_iso_datetime(data.get("created_at"), "jd_sources.created_at"),
            sources=tuple(JDSource.from_dict(item, f"jd_sources.sources[{index}]") for index, item in enumerate(raw_sources)),
        )
        result.validate()
        return result

    def validate(self) -> None:
        for index, source in enumerate(self.sources):
            if JDSource.from_dict(source.to_dict(), f"jd_sources.sources[{index}]") != source:
                raise Phase2ValidationError("JD Source collection contains non-canonical source data")
        source_map = {item.source_id: item for item in self.sources}
        if len(source_map) != len(self.sources):
            raise Phase2ValidationError("JD Source collection contains duplicate source IDs")
        if self.collection_type == CatalogType.PRODUCTION and any(item.is_test_fixture for item in self.sources):
            raise Phase2ValidationError("production JD Source collection cannot use test fixtures")
        for source in self.sources:
            if source.canonical_source_reference is not None:
                canonical = source_map.get(source.canonical_source_reference)
                if canonical is None:
                    raise Phase2ValidationError(f"source {source.source_id} references unknown canonical source")
                if canonical.canonical_job_id != source.canonical_job_id:
                    raise Phase2ValidationError(f"source {source.source_id} canonical job does not match canonical source")
                if source.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM and canonical.source_tier != SourceTier.TIER_A_OFFICIAL:
                    raise Phase2ValidationError(f"source {source.source_id} platform redirect must target Tier A")
            for discovery_id in source.discovery_source_references:
                discovery = source_map.get(discovery_id)
                if discovery is None:
                    raise Phase2ValidationError(f"source {source.source_id} references unknown discovery source")
                if discovery.canonical_job_id != source.canonical_job_id:
                    raise Phase2ValidationError(f"source {source.source_id} discovery source represents another job")

    def source(self, source_id: str) -> JDSource:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise Phase2ValidationError(f"unknown JD source ID: {source_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "collection_id": self.collection_id,
            "collection_type": self.collection_type.value,
            "created_at": self.created_at,
            "sources": [item.to_dict() for item in self.sources],
        }


def generate_capture_id(
    *,
    source_reference: str,
    captured_at: str,
    content_hash: str,
    hash_normalization_version: str,
) -> str:
    source_id = _stable_id(source_reference, "source_reference")
    captured = _iso_datetime(captured_at, "captured_at")
    digest = _text(content_hash, "content_hash")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise Phase2ValidationError("content_hash must be a SHA-256 digest")
    normalization = _text(
        hash_normalization_version, "hash_normalization_version"
    )
    identity = "|".join(
        (CAPTURE_ID_VERSION, source_id, captured, digest, normalization)
    )
    return _digest_id("capture", identity)


@dataclass(frozen=True, eq=True)
class LogicalJDSource:
    """Stable job-source identity, independent from any captured page content."""

    source_id: str
    source_url: str
    source_type: JDSourceType
    source_tier: SourceTier
    source_status: SourceLifecycleStatus
    company_id: str
    company_display_name: str
    exact_job_title: str | None
    platform_name: PlatformName
    platform_job_id: str | None
    requisition_id: str | None
    application_url: str | None
    location: str
    country_code: str
    market: Market
    career_stage: CareerStage
    experience_range: ExperienceRange
    canonical_job_id: str | None
    canonical_source_reference: str | None
    discovery_source_references: tuple[str, ...]
    identity_normalization_version: str = IDENTITY_NORMALIZATION_VERSION
    is_test_fixture: bool = False

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "source"
    ) -> LogicalJDSource:
        data = _mapping(value, path)
        allowed = {
            "source_id", "source_url", "source_type", "source_tier", "source_status",
            "company_id", "company_display_name", "exact_job_title", "platform_name",
            "platform_job_id", "requisition_id", "application_url", "location",
            "country_code", "market", "career_stage", "experience_range",
            "canonical_job_id", "canonical_source_reference",
            "discovery_source_references", "identity_normalization_version",
            "is_test_fixture",
        }
        _reject_unknown(data, allowed, path)
        fixture = data.get("is_test_fixture", False)
        if not isinstance(fixture, bool):
            raise Phase2ValidationError(f"{path}.is_test_fixture must be a boolean")
        source_url = normalize_source_url(data.get("source_url"))
        platform = _enum(data.get("platform_name"), PlatformName, f"{path}.platform_name")
        platform_job_id = _text(
            data.get("platform_job_id"), f"{path}.platform_job_id", required=False
        )
        source_id = _stable_id(data.get("source_id"), f"{path}.source_id")
        if source_id != generate_source_id(
            source_url,
            platform_name=platform.value,
            platform_job_id=platform_job_id,
        ):
            raise Phase2ValidationError(
                f"{path}.source_id was not generated by Aarvia identity rules"
            )
        country = _text(data.get("country_code"), f"{path}.country_code")
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise Phase2ValidationError(
                f"{path}.country_code must be ISO alpha-2 uppercase"
            )
        application_url = data.get("application_url")
        result = cls(
            source_id=source_id,
            source_url=source_url,
            source_type=_enum(data.get("source_type"), JDSourceType, f"{path}.source_type"),
            source_tier=_enum(data.get("source_tier"), SourceTier, f"{path}.source_tier"),
            source_status=_enum(data.get("source_status"), SourceLifecycleStatus, f"{path}.source_status"),
            company_id=_stable_id(data.get("company_id"), f"{path}.company_id"),
            company_display_name=_text(data.get("company_display_name"), f"{path}.company_display_name"),
            exact_job_title=_text(data.get("exact_job_title"), f"{path}.exact_job_title", required=False),
            platform_name=platform,
            platform_job_id=platform_job_id,
            requisition_id=_text(data.get("requisition_id"), f"{path}.requisition_id", required=False),
            application_url=(None if application_url is None else _https_url(application_url, f"{path}.application_url")),
            location=_text(data.get("location"), f"{path}.location"),
            country_code=country,
            market=_enum(data.get("market"), Market, f"{path}.market"),
            career_stage=_enum(data.get("career_stage"), CareerStage, f"{path}.career_stage"),
            experience_range=ExperienceRange.from_dict(data.get("experience_range"), f"{path}.experience_range"),
            canonical_job_id=(None if data.get("canonical_job_id") is None else _stable_id(data.get("canonical_job_id"), f"{path}.canonical_job_id")),
            canonical_source_reference=(None if data.get("canonical_source_reference") is None else _stable_id(data.get("canonical_source_reference"), f"{path}.canonical_source_reference")),
            discovery_source_references=_string_tuple(data.get("discovery_source_references"), f"{path}.discovery_source_references", ids=True),
            identity_normalization_version=_text(data.get("identity_normalization_version"), f"{path}.identity_normalization_version"),
            is_test_fixture=fixture,
        )
        result._validate_shape(path)
        return result

    def _validate_shape(self, path: str) -> None:
        if self.identity_normalization_version != IDENTITY_NORMALIZATION_VERSION:
            raise Phase2ValidationError(f"{path} uses unsupported identity normalization")
        if self.country_code != "US" or self.market != Market.UNITED_STATES_EARLY_CAREER:
            raise Phase2ValidationError(f"{path} is outside the Phase 2B-1 US market")
        if self.experience_range.status == ExperienceRangeStatus.EXPLICIT and any(
            bound is not None and bound > 2
            for bound in (
                self.experience_range.minimum_years,
                self.experience_range.maximum_years,
            )
        ):
            raise Phase2ValidationError(
                f"{path} explicitly requires more than two years experience"
            )
        if self.source_tier == SourceTier.TIER_A_OFFICIAL:
            if self.source_type not in {
                JDSourceType.OFFICIAL_JOB_POSTING,
                JDSourceType.OFFICIAL_CAREER_PAGE,
            }:
                raise Phase2ValidationError(f"{path} Tier A requires an official source type")
            if self.platform_name not in {
                PlatformName.COMPANY_CAREERS,
                PlatformName.WORKDAY,
                PlatformName.GREENHOUSE,
                PlatformName.LEVER,
                PlatformName.ASHBY,
                PlatformName.OTHER,
            }:
                raise Phase2ValidationError(f"{path} Tier A uses an invalid platform")
        elif self.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM:
            if self.source_type != JDSourceType.PLATFORM_JOB_POSTING:
                raise Phase2ValidationError(f"{path} Tier B requires a platform job posting")
            if self.platform_name in {
                PlatformName.COMPANY_CAREERS,
                PlatformName.WORKDAY,
                PlatformName.GREENHOUSE,
                PlatformName.LEVER,
                PlatformName.ASHBY,
                PlatformName.INDEED,
                PlatformName.OTHER,
            }:
                raise Phase2ValidationError(f"{path} Tier B requires a verified hiring platform")
            if not self.platform_job_id or not self.exact_job_title or not self.application_url:
                raise Phase2ValidationError(
                    f"{path} Tier B requires company, exact posting, platform job ID, and application URL"
                )
            if self.source_status != SourceLifecycleStatus.VERIFIED:
                raise Phase2ValidationError(f"{path} Tier B source must be verified")
        elif (
            self.source_type in {
                JDSourceType.OFFICIAL_JOB_POSTING,
                JDSourceType.PLATFORM_JOB_POSTING,
            }
            and self.source_status == SourceLifecycleStatus.VERIFIED
        ):
            raise Phase2ValidationError(
                f"{path} verified posting cannot be silently downgraded to Tier C"
            )
        if self.canonical_job_id is not None and self.canonical_source_reference in {
            None,
            self.source_id,
        }:
            expected = generate_canonical_job_id(
                company_id=self.company_id,
                requisition_id=self.requisition_id,
                canonical_url=(
                    self.source_url
                    if self.source_tier == SourceTier.TIER_A_OFFICIAL
                    else None
                ),
                platform_name=self.platform_name.value,
                platform_job_id=self.platform_job_id,
            )
            if self.canonical_job_id != expected:
                raise Phase2ValidationError(
                    f"{path}.canonical_job_id was not generated by Aarvia rules"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_type": self.source_type.value,
            "source_tier": self.source_tier.value,
            "source_status": self.source_status.value,
            "company_id": self.company_id,
            "company_display_name": self.company_display_name,
            "exact_job_title": self.exact_job_title,
            "platform_name": self.platform_name.value,
            "platform_job_id": self.platform_job_id,
            "requisition_id": self.requisition_id,
            "application_url": self.application_url,
            "location": self.location,
            "country_code": self.country_code,
            "market": self.market.value,
            "career_stage": self.career_stage.value,
            "experience_range": self.experience_range.to_dict(),
            "canonical_job_id": self.canonical_job_id,
            "canonical_source_reference": self.canonical_source_reference,
            "discovery_source_references": list(self.discovery_source_references),
            "identity_normalization_version": self.identity_normalization_version,
            "is_test_fixture": self.is_test_fixture,
        }


@dataclass(frozen=True, eq=True)
class JDSourceCapture:
    capture_id: str
    source_reference: str
    captured_at: str
    verified_at: str | None
    content_hash: str
    content_length: int | None
    hash_normalization_version: str
    verification_method: VerificationMethod
    capture_scope: CaptureScope
    previous_capture_reference: str | None = None

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], path: str = "capture"
    ) -> JDSourceCapture:
        data = _mapping(value, path)
        _reject_unknown(
            data,
            {
                "capture_id", "source_reference", "captured_at", "verified_at",
                "content_hash", "content_length", "hash_normalization_version",
                "verification_method", "capture_scope",
                "previous_capture_reference",
            },
            path,
        )
        content_length = data.get("content_length")
        if content_length is not None and (
            not isinstance(content_length, int)
            or isinstance(content_length, bool)
            or content_length < 0
        ):
            raise Phase2ValidationError(
                f"{path}.content_length must be a non-negative integer or null"
            )
        source_reference = _stable_id(
            data.get("source_reference"), f"{path}.source_reference"
        )
        captured_at = _iso_datetime(data.get("captured_at"), f"{path}.captured_at")
        content_hash = _text(data.get("content_hash"), f"{path}.content_hash")
        normalization = _text(
            data.get("hash_normalization_version"),
            f"{path}.hash_normalization_version",
        )
        result = cls(
            capture_id=_stable_id(data.get("capture_id"), f"{path}.capture_id"),
            source_reference=source_reference,
            captured_at=captured_at,
            verified_at=(None if data.get("verified_at") is None else _iso_datetime(data.get("verified_at"), f"{path}.verified_at")),
            content_hash=content_hash,
            content_length=content_length,
            hash_normalization_version=normalization,
            verification_method=_enum(data.get("verification_method"), VerificationMethod, f"{path}.verification_method"),
            capture_scope=_enum(data.get("capture_scope"), CaptureScope, f"{path}.capture_scope"),
            previous_capture_reference=(None if data.get("previous_capture_reference") is None else _stable_id(data.get("previous_capture_reference"), f"{path}.previous_capture_reference")),
        )
        if normalization != CONTENT_HASH_NORMALIZATION_VERSION:
            raise Phase2ValidationError(f"{path} uses unsupported content hash normalization")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
            raise Phase2ValidationError(f"{path}.content_hash must be a SHA-256 digest")
        expected = generate_capture_id(
            source_reference=source_reference,
            captured_at=captured_at,
            content_hash=content_hash,
            hash_normalization_version=normalization,
        )
        if result.capture_id != expected:
            raise Phase2ValidationError(
                f"{path}.capture_id was not generated by Aarvia"
            )
        if result.verification_method == VerificationMethod.NOT_VERIFIED:
            if result.verified_at is not None:
                raise Phase2ValidationError(
                    f"{path} unverified capture cannot have verified_at"
                )
        elif result.verified_at is None:
            raise Phase2ValidationError(
                f"{path} verified capture requires verified_at"
            )
        if result.verified_at is not None and _as_datetime(result.verified_at) < _as_datetime(result.captured_at):
            raise Phase2ValidationError(
                f"{path}.verified_at cannot precede captured_at"
            )
        if (
            result.capture_scope == CaptureScope.FULL_JOB_DESCRIPTION
            and (result.content_length is None or result.content_length == 0)
        ):
            raise Phase2ValidationError(
                f"{path} full_job_description requires normalized content_length"
            )
        return result

    @property
    def supports_not_stated(self) -> bool:
        return self.capture_scope == CaptureScope.FULL_JOB_DESCRIPTION

    def contains_offsets(self, start: int | None, end: int | None) -> bool:
        if start is None and end is None:
            return True
        return (
            self.content_length is not None
            and start is not None
            and end is not None
            and 0 <= start < end <= self.content_length
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "source_reference": self.source_reference,
            "captured_at": self.captured_at,
            "verified_at": self.verified_at,
            "content_hash": self.content_hash,
            "content_length": self.content_length,
            "hash_normalization_version": self.hash_normalization_version,
            "verification_method": self.verification_method.value,
            "capture_scope": self.capture_scope.value,
            "previous_capture_reference": self.previous_capture_reference,
        }


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, eq=True)
class JDSourceCollectionV3:
    collection_id: str
    collection_type: CatalogType
    created_at: str
    sources: tuple[LogicalJDSource, ...]
    captures: tuple[JDSourceCapture, ...]
    schema: str = SOURCE_SCHEMA
    schema_version: int = SOURCE_SCHEMA_V3_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JDSourceCollectionV3:
        data = _mapping(value, "jd_sources")
        if data.get("schema") != SOURCE_SCHEMA or data.get("schema_version") != SOURCE_SCHEMA_V3_VERSION:
            raise Phase2ValidationError("unsupported JD Source schema version")
        _reject_unknown(
            data,
            {
                "schema", "schema_version", "collection_id", "collection_type",
                "created_at", "sources", "captures",
            },
            "jd_sources",
        )
        raw_sources = data.get("sources")
        raw_captures = data.get("captures")
        if not isinstance(raw_sources, list) or not isinstance(raw_captures, list):
            raise Phase2ValidationError("jd_sources sources and captures must be lists")
        result = cls(
            collection_id=_stable_id(data.get("collection_id"), "jd_sources.collection_id"),
            collection_type=_enum(data.get("collection_type"), CatalogType, "jd_sources.collection_type"),
            created_at=_iso_datetime(data.get("created_at"), "jd_sources.created_at"),
            sources=tuple(LogicalJDSource.from_dict(item, f"jd_sources.sources[{index}]") for index, item in enumerate(raw_sources)),
            captures=tuple(JDSourceCapture.from_dict(item, f"jd_sources.captures[{index}]") for index, item in enumerate(raw_captures)),
        )
        result.validate()
        return result

    def validate(self) -> None:
        for index, source in enumerate(self.sources):
            if LogicalJDSource.from_dict(source.to_dict(), f"jd_sources.sources[{index}]") != source:
                raise Phase2ValidationError("JD Source collection contains non-canonical source data")
        for index, capture in enumerate(self.captures):
            if JDSourceCapture.from_dict(capture.to_dict(), f"jd_sources.captures[{index}]") != capture:
                raise Phase2ValidationError("JD Source collection contains non-canonical capture data")
        source_map = {item.source_id: item for item in self.sources}
        capture_map = {item.capture_id: item for item in self.captures}
        if len(source_map) != len(self.sources):
            raise Phase2ValidationError("JD Source collection contains duplicate source IDs")
        if len(capture_map) != len(self.captures):
            raise Phase2ValidationError("JD Source collection contains duplicate capture IDs")
        if self.collection_type == CatalogType.PRODUCTION and any(item.is_test_fixture for item in self.sources):
            raise Phase2ValidationError("production JD Source collection cannot use test fixtures")
        for source in self.sources:
            if source.canonical_source_reference is not None:
                canonical = source_map.get(source.canonical_source_reference)
                if canonical is None:
                    raise Phase2ValidationError(f"source {source.source_id} references unknown canonical source")
                if canonical.canonical_job_id != source.canonical_job_id:
                    raise Phase2ValidationError(f"source {source.source_id} canonical job does not match canonical source")
                if source.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM and canonical.source_tier != SourceTier.TIER_A_OFFICIAL:
                    raise Phase2ValidationError(f"source {source.source_id} platform redirect must target Tier A")
            for discovery_id in source.discovery_source_references:
                discovery = source_map.get(discovery_id)
                if discovery is None:
                    raise Phase2ValidationError(f"source {source.source_id} references unknown discovery source")
                if discovery.canonical_job_id != source.canonical_job_id:
                    raise Phase2ValidationError(f"source {source.source_id} discovery source represents another job")
            source_captures = [item for item in self.captures if item.source_reference == source.source_id]
            if source.source_status in {
                SourceLifecycleStatus.CAPTURED,
                SourceLifecycleStatus.VERIFIED,
                SourceLifecycleStatus.ARCHIVED,
                SourceLifecycleStatus.EXPIRED,
            } and not source_captures:
                raise Phase2ValidationError(f"source {source.source_id} status requires a capture")
            if source.source_status == SourceLifecycleStatus.VERIFIED and not any(
                self._capture_qualifies_source(source, item) for item in source_captures
            ):
                raise Phase2ValidationError(f"source {source.source_id} lacks qualified verification capture")
        edges: dict[str, str] = {}
        for capture in self.captures:
            if capture.source_reference not in source_map:
                raise Phase2ValidationError(f"capture {capture.capture_id} references unknown source")
            if capture.previous_capture_reference is not None:
                previous = capture_map.get(capture.previous_capture_reference)
                if previous is None:
                    raise Phase2ValidationError(f"capture {capture.capture_id} previous reference is missing")
                if previous.source_reference != capture.source_reference:
                    raise Phase2ValidationError(f"capture {capture.capture_id} previous reference crosses sources")
                edges[capture.capture_id] = previous.capture_id
        for capture_id in edges:
            seen: set[str] = set()
            current = capture_id
            while current in edges:
                if current in seen:
                    raise Phase2ValidationError("JD capture lineage contains a cycle")
                seen.add(current)
                current = edges[current]
        for capture_id, previous_id in edges.items():
            capture = capture_map[capture_id]
            previous = capture_map[previous_id]
            if _as_datetime(previous.captured_at) >= _as_datetime(capture.captured_at):
                raise Phase2ValidationError(
                    f"capture {capture.capture_id} lineage time must strictly increase"
                )

    @staticmethod
    def _capture_qualifies_source(source: LogicalJDSource, capture: JDSourceCapture) -> bool:
        if capture.verified_at is None:
            return False
        if source.source_tier == SourceTier.TIER_A_OFFICIAL:
            return capture.verification_method in {
                VerificationMethod.OFFICIAL_PAGE_DIRECT,
                VerificationMethod.OFFICIAL_ATS_DIRECT,
            }
        if source.source_tier == SourceTier.TIER_B_VERIFIED_PLATFORM:
            return capture.verification_method in {
                VerificationMethod.PLATFORM_APPLY_AVAILABLE,
                VerificationMethod.PLATFORM_REDIRECT_TO_OFFICIAL,
            }
        return False

    def source(self, source_id: str) -> LogicalJDSource:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise Phase2ValidationError(f"unknown JD source ID: {source_id}")

    def capture(self, capture_id: str) -> JDSourceCapture:
        for capture in self.captures:
            if capture.capture_id == capture_id:
                return capture
        raise Phase2ValidationError(f"unknown JD capture ID: {capture_id}")

    def capture_supports_not_stated(self, capture_id: str) -> bool:
        return self.capture(capture_id).supports_not_stated

    def matching_captures(self, source_id: str, content_hash: str) -> tuple[JDSourceCapture, ...]:
        return tuple(
            item
            for item in self.captures
            if item.source_reference == source_id and item.content_hash == content_hash
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "collection_id": self.collection_id,
            "collection_type": self.collection_type.value,
            "created_at": self.created_at,
            "sources": [item.to_dict() for item in self.sources],
            "captures": [item.to_dict() for item in self.captures],
        }


def migrate_source_v2_to_v3(
    value: JDSourceCollection,
    *,
    scope_by_source_id: Mapping[str, CaptureScope | str] | None = None,
    content_length_by_source_id: Mapping[str, int] | None = None,
) -> JDSourceCollectionV3:
    if not isinstance(value, JDSourceCollection):
        raise TypeError("value must be a schema 2 JDSourceCollection")
    scopes = scope_by_source_id or {}
    lengths = content_length_by_source_id or {}
    unknown_scope_ids = set(scopes) - {item.source_id for item in value.sources}
    unknown_length_ids = set(lengths) - {item.source_id for item in value.sources}
    if unknown_scope_ids or unknown_length_ids:
        raise Phase2ValidationError("source migration mappings contain unknown source IDs")
    logical_sources: list[LogicalJDSource] = []
    captures: list[JDSourceCapture] = []
    for source in value.sources:
        source_data = source.to_dict()
        for key in (
            "captured_at", "last_verified_at", "verification_method", "content_hash",
            "hash_normalization_version",
        ):
            source_data.pop(key)
        logical_sources.append(LogicalJDSource.from_dict(source_data))
        if source.content_hash is None:
            if source.source_id in scopes or source.source_id in lengths:
                raise Phase2ValidationError("cannot assign capture metadata to an uncaptured legacy source")
            continue
        raw_scope = scopes.get(source.source_id, CaptureScope.LEGACY_UNSPECIFIED)
        scope = _enum(raw_scope, CaptureScope, "capture_scope")
        length = lengths.get(source.source_id)
        if length is not None and (
            not isinstance(length, int) or isinstance(length, bool) or length < 0
        ):
            raise Phase2ValidationError("content length migration value is invalid")
        capture_data = {
            "capture_id": generate_capture_id(
                source_reference=source.source_id,
                captured_at=source.captured_at,
                content_hash=source.content_hash,
                hash_normalization_version=source.hash_normalization_version,
            ),
            "source_reference": source.source_id,
            "captured_at": source.captured_at,
            "verified_at": source.last_verified_at,
            "content_hash": source.content_hash,
            "content_length": length,
            "hash_normalization_version": source.hash_normalization_version,
            "verification_method": source.verification_method.value,
            "capture_scope": scope.value,
            "previous_capture_reference": None,
        }
        captures.append(JDSourceCapture.from_dict(capture_data))
    result = JDSourceCollectionV3(
        collection_id=value.collection_id,
        collection_type=value.collection_type,
        created_at=value.created_at,
        sources=tuple(logical_sources),
        captures=tuple(captures),
    )
    result.validate()
    return result


JDSourceCollectionType = JDSourceCollection | JDSourceCollectionV3


def save_jd_source_collection(value: JDSourceCollectionType, path: str | Path) -> Path:
    if not isinstance(value, (JDSourceCollection, JDSourceCollectionV3)):
        raise TypeError("value must be a JDSourceCollection")
    from .phase2_storage import save_phase2_json
    target = Path(path)
    if isinstance(value, JDSourceCollectionV3) and target.exists():
        from .phase2_storage import load_phase2_json

        existing_data = _mapping(load_phase2_json(target), "jd_sources")
        if existing_data.get("schema_version") != SOURCE_SCHEMA_V3_VERSION:
            raise Phase2ValidationError(
                "schema 3 Source artifact cannot overwrite another schema"
            )
        existing = JDSourceCollectionV3.from_dict(existing_data)
        if existing.collection_id != value.collection_id:
            raise Phase2ValidationError(
                "schema 3 Source artifact cannot replace another collection"
            )
        new_captures = {item.capture_id: item for item in value.captures}
        for prior_capture in existing.captures:
            if new_captures.get(prior_capture.capture_id) != prior_capture:
                raise Phase2ValidationError(
                    f"immutable capture cannot be removed or overwritten: {prior_capture.capture_id}"
                )
    value.validate()
    canonical = (
        JDSourceCollectionV3.from_dict(value.to_dict())
        if isinstance(value, JDSourceCollectionV3)
        else JDSourceCollection.from_dict(value.to_dict())
    )
    return save_phase2_json(canonical.to_dict(), path)


def load_jd_source_collection(path: str | Path) -> JDSourceCollectionType:
    from .phase2_storage import load_phase2_json
    data = _mapping(load_phase2_json(path), "jd_sources")
    if data.get("schema_version") == SOURCE_SCHEMA_VERSION:
        return JDSourceCollection.from_dict(data)
    if data.get("schema_version") == SOURCE_SCHEMA_V3_VERSION:
        return JDSourceCollectionV3.from_dict(data)
    raise Phase2ValidationError("unsupported JD Source schema version")
