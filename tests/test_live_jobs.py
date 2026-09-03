from copy import deepcopy
from dataclasses import replace
import json

import pytest

from aarvia.jd_sources import JDSourceCollection, generate_canonical_job_id
from aarvia.live_jobs import (
    LiveJobCollection,
    LiveJobCollectionV2,
    load_live_job_collection,
    save_live_job_collection,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from phase2_fixtures import live_job_fixture_data
from phase2b_fixtures import NOW, source_collection_data, source_data


def live_job_v2_data(source: dict, *, listing_status: str) -> dict:
    application_verified = listing_status in {
        "verified_official_open",
        "verified_platform_open",
    } and source["application_url"] is not None
    return {
        "schema": "aarvia.live_jobs",
        "schema_version": 2,
        "collection_id": "fixture_live_jobs_v2",
        "catalog_version": "1.0.0",
        "collection_type": "test_fixture",
        "source_collection_id": "fixture_source_collection",
        "captured_at": NOW,
        "jobs": [
            {
                "job_id": source["canonical_job_id"],
                "canonical_job_id": source["canonical_job_id"],
                "canonical_source_reference": source["source_id"],
                "discovery_source_references": source["discovery_source_references"],
                "company_id": source["company_id"],
                "company": source["company_display_name"],
                "exact_job_title": source["exact_job_title"],
                "job_url": source["source_url"],
                "application_url": source["application_url"],
                "application_url_status": (
                    "verified_active"
                    if application_verified
                    else (
                        "provided_unverified"
                        if source["application_url"] is not None
                        else "not_provided"
                    )
                ),
                "application_url_last_verified_at": (
                    source["last_verified_at"] if application_verified else None
                ),
                "application_url_source_reference": (
                    source["source_id"] if source["application_url"] else None
                ),
                "location": source["location"],
                "employment_type": "internship",
                "posting_date": None,
                "expiration_date": None,
                "last_verified_at": source["last_verified_at"],
                "listing_status": listing_status,
                "mapped_role_id": "applied_ai_engineer",
                "mapped_specialization_id": "agentic_ai",
                "eligibility_status": "unknown",
                "preliminary_match_status": "not_analyzed",
                "structured_jd_requirements": [],
            }
        ],
    }


def test_valid_fixture_live_job_contract_round_trips() -> None:
    collection = LiveJobCollection.from_dict(live_job_fixture_data())

    collection.validate(production_role_catalog())
    assert LiveJobCollection.from_dict(collection.to_dict()) == collection
    assert collection.jobs[0].preliminary_match_status.value == "not_analyzed"


def test_live_job_requires_an_existing_official_source() -> None:
    data = live_job_fixture_data()
    data["jobs"][0]["source_reference"] = "missing_source"
    collection = LiveJobCollection.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="unknown official source"):
        collection.validate(production_role_catalog())


def test_non_official_source_cannot_support_a_live_job() -> None:
    data = live_job_fixture_data()
    data["sources"][0].update(
        source_type="official_documentation",
        company=None,
        job_title=None,
    )
    collection = LiveJobCollection.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="official hiring source"):
        collection.validate(production_role_catalog())


def test_general_careers_page_cannot_prove_verified_open() -> None:
    data = live_job_fixture_data()
    data["sources"][0].update(
        source_type="official_career_page",
        company=None,
        job_title=None,
    )
    collection = LiveJobCollection.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="specific official job posting"):
        collection.validate(production_role_catalog())

    data["jobs"][0]["listing_status"] = "possibly_open"
    data["jobs"][0]["official_application_url"] = None
    data["jobs"][0]["application_url_status"] = "not_provided"
    data["jobs"][0]["application_url_source_reference"] = None
    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog())
    assert collection.jobs[0].listing_status.value == "possibly_open"


@pytest.mark.parametrize("source_status", ["closed", "expired"])
def test_closed_or_expired_source_cannot_be_verified_open(source_status) -> None:
    data = live_job_fixture_data(source_status="expired")
    if source_status == "closed":
        data["jobs"][0]["listing_status"] = "closed"
        collection = LiveJobCollection.from_dict(data)
        collection.validate(production_role_catalog())
        assert collection.jobs[0].listing_status.value == "closed"
        return
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="cannot be verified_open"):
        collection.validate(production_role_catalog())


def test_expiration_before_verification_cannot_be_verified_open() -> None:
    data = live_job_fixture_data()
    data["jobs"][0]["expiration_date"] = "2026-08-31"
    collection = LiveJobCollection.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="after its expiration"):
        collection.validate(production_role_catalog())


def test_job_role_and_specialization_must_exist_in_catalog() -> None:
    data = live_job_fixture_data()
    data["jobs"][0]["mapped_specialization_id"] = "invented_specialization"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="unknown specialization"):
        collection.validate(production_role_catalog())

    data = live_job_fixture_data()
    data["jobs"][0]["mapped_role_id"] = "invented_role"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="unknown role ID"):
        collection.validate(production_role_catalog())


def test_production_collection_rejects_explicit_fixture_source() -> None:
    data = live_job_fixture_data()
    data["collection_type"] = "production"
    source = data["sources"][0]
    source.update(
        source_type="test_fixture",
        status="test_fixture",
        is_test_fixture=True,
    )

    with pytest.raises(Phase2ValidationError, match="production Live Job collection"):
        LiveJobCollection.from_dict(data)


def test_job_contract_rejects_unknown_fields() -> None:
    data = live_job_fixture_data()
    data["jobs"][0]["offer_probability"] = 0.9

    with pytest.raises(Phase2ValidationError, match="unknown fields: offer_probability"):
        LiveJobCollection.from_dict(data)


def test_application_url_presence_does_not_imply_verification() -> None:
    collection = LiveJobCollection.from_dict(live_job_fixture_data())
    collection.validate(production_role_catalog())

    job = collection.jobs[0]
    assert job.official_application_url is not None
    assert job.application_url_status.value == "provided_unverified"
    assert job.application_url_last_verified_at is None


def test_verified_application_url_requires_timestamp_and_official_source() -> None:
    data = live_job_fixture_data()
    job = data["jobs"][0]
    job["application_url_status"] = "verified_active"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="requires a verification timestamp"):
        collection.validate(production_role_catalog())

    job["application_url_last_verified_at"] = "2026-09-01T12:00:00+08:00"
    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog())
    assert collection.jobs[0].application_url_status.value == "verified_active"

    job["application_url_source_reference"] = "missing_source"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="unknown source"):
        collection.validate(production_role_catalog())


def test_missing_application_url_has_consistent_status() -> None:
    data = live_job_fixture_data()
    job = data["jobs"][0]
    job.update(
        official_application_url=None,
        application_url_status="not_provided",
        application_url_last_verified_at=None,
        application_url_source_reference=None,
    )
    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog())

    job["application_url_status"] = "verified_active"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="must be not_provided"):
        collection.validate(production_role_catalog())


def test_schema_two_distinguishes_official_and_platform_verified_open() -> None:
    official = source_data()
    official_sources = JDSourceCollection.from_dict(source_collection_data([official]))
    collection = LiveJobCollection.from_dict(
        live_job_v2_data(official, listing_status="verified_official_open")
    )
    assert isinstance(collection, LiveJobCollectionV2)
    collection.validate(production_role_catalog(), official_sources)

    platform = source_data(2, tier="tier_b_verified_platform")
    platform_sources = JDSourceCollection.from_dict(source_collection_data([platform]))
    collection = LiveJobCollection.from_dict(
        live_job_v2_data(platform, listing_status="verified_platform_open")
    )
    collection.validate(production_role_catalog(), platform_sources)


def test_schema_two_verified_status_requires_matching_source_verification() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["last_verified_at"] = "2026-09-01T12:01:00+00:00"
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="verification time does not match source"):
        collection.validate(production_role_catalog(), sources)

    unverified = source_data()
    unverified["source_status"] = "captured"
    unverified["last_verified_at"] = None
    unverified_sources = JDSourceCollection.from_dict(source_collection_data([unverified]))
    collection = LiveJobCollection.from_dict(
        live_job_v2_data(unverified, listing_status="verified_official_open")
    )
    with pytest.raises(Phase2ValidationError, match="verified Tier A posting"):
        collection.validate(production_role_catalog(), unverified_sources)


def test_schema_two_rejects_contradictory_posting_expiration_and_capture_times() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["expiration_date"] = "2026-08-31"
    with pytest.raises(Phase2ValidationError, match="after expiration"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["posting_date"] = "2026-09-02"
    with pytest.raises(Phase2ValidationError, match="posting date cannot follow source capture"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["posting_date"] = "2026-09-01"
    data["jobs"][0]["expiration_date"] = "2026-08-31"
    with pytest.raises(Phase2ValidationError, match="expiration date cannot precede posting date"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["captured_at"] = "2026-09-01T11:59:59+00:00"
    with pytest.raises(Phase2ValidationError, match="source capture cannot follow collection capture"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_careers_homepage_can_only_support_possibly_open() -> None:
    source = source_data(source_type="official_career_page")
    source["canonical_job_id"] = generate_canonical_job_id(
        company_id=source["company_id"], requisition_id=source["requisition_id"],
        canonical_url=source["source_url"], platform_name=source["platform_name"],
        platform_job_id=None,
    )
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="verified_official_open")
    collection = LiveJobCollection.from_dict(data)
    with pytest.raises(Phase2ValidationError, match="verified Tier A posting"):
        collection.validate(production_role_catalog(), sources)

    data["jobs"][0]["listing_status"] = "possibly_open"
    data["jobs"][0]["application_url_status"] = "provided_unverified"
    data["jobs"][0]["application_url_last_verified_at"] = None
    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog(), sources)


def test_schema_two_verified_application_url_has_complete_provenance() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="verified_official_open")

    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog(), sources)

    job = collection.jobs[0]
    assert job.application_url_status.value == "verified_active"
    assert job.application_url_last_verified_at == NOW
    assert job.application_url_source_reference == source["source_id"]


def test_schema_two_application_url_can_be_provided_without_verification() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")

    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog(), sources)
    assert collection.jobs[0].application_url_status.value == "provided_unverified"
    assert collection.jobs[0].application_url_last_verified_at is None


def test_schema_two_missing_application_url_has_consistent_state() -> None:
    source = source_data()
    source["application_url"] = None
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")

    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog(), sources)
    job = collection.jobs[0]
    assert job.application_url is None
    assert job.application_url_status.value == "not_provided"
    assert job.application_url_last_verified_at is None
    assert job.application_url_source_reference is None

    source_with_url = source_data(2)
    sources_with_url = JDSourceCollection.from_dict(
        source_collection_data([source_with_url])
    )
    invalid = live_job_v2_data(source_with_url, listing_status="possibly_open")
    invalid["jobs"][0].update(
        application_url_status="not_provided",
        application_url_source_reference=None,
    )
    with pytest.raises(Phase2ValidationError, match="marks it not_provided"):
        LiveJobCollection.from_dict(invalid).validate(
            production_role_catalog(), sources_with_url
        )


def test_schema_two_verified_application_requires_url_timestamp_and_source() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0].update(
        application_url=None,
        application_url_source_reference=None,
    )
    with pytest.raises(Phase2ValidationError, match="application URL"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["application_url_last_verified_at"] = None
    with pytest.raises(Phase2ValidationError, match="requires a verification timestamp"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["application_url_source_reference"] = None
    with pytest.raises(Phase2ValidationError, match="requires a source reference"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_two_application_source_must_exist_and_belong_to_job() -> None:
    source = source_data()
    other = source_data(2)
    sources = JDSourceCollection.from_dict(source_collection_data([source, other]))

    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["application_url_source_reference"] = "missing_source"
    with pytest.raises(Phase2ValidationError, match="unknown source"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)

    data["jobs"][0]["application_url_source_reference"] = other["source_id"]
    with pytest.raises(Phase2ValidationError, match="outside the job provenance"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_two_careers_homepage_cannot_verify_application_url() -> None:
    source = source_data(source_type="official_career_page")
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")
    data["jobs"][0].update(
        application_url_status="verified_unavailable",
        application_url_last_verified_at=NOW,
    )

    with pytest.raises(Phase2ValidationError, match="specific qualified posting source"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_two_verified_application_rejects_unverified_source_status() -> None:
    source = source_data()
    source["source_status"] = "captured"
    source["last_verified_at"] = None
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")
    data["jobs"][0].update(
        application_url_status="verified_unavailable",
        application_url_last_verified_at=NOW,
    )

    with pytest.raises(Phase2ValidationError, match="requires source verification"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


@pytest.mark.parametrize(
    ("verified_at", "message"),
    [
        ("2026-09-01T11:59:59+00:00", "cannot precede source capture"),
        ("2026-09-01T12:00:01+00:00", "cannot follow source verification"),
    ],
)
def test_schema_two_application_verification_time_is_bounded_by_source(
    verified_at, message
) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["application_url_last_verified_at"] = verified_at

    with pytest.raises(Phase2ValidationError, match=message):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_two_unverified_application_cannot_claim_verification_time() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")
    data["jobs"][0]["application_url_last_verified_at"] = NOW

    with pytest.raises(Phase2ValidationError, match="unverified application URL"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_two_verified_unavailable_application_is_explicitly_checked() -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")
    data["jobs"][0].update(
        application_url_status="verified_unavailable",
        application_url_last_verified_at=NOW,
    )

    collection = LiveJobCollection.from_dict(data)
    collection.validate(production_role_catalog(), sources)
    assert collection.jobs[0].application_url_status.value == "verified_unavailable"


def test_schema_two_unverifiable_source_cannot_claim_verified_unavailable() -> None:
    source = source_data()
    source["source_status"] = "unverifiable"
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="possibly_open")
    data["jobs"][0].update(
        application_url_status="verified_unavailable",
        application_url_last_verified_at=NOW,
    )

    with pytest.raises(Phase2ValidationError, match="reviewed source status"):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


@pytest.mark.parametrize(
    ("listing_status", "application_status", "message"),
    [
        ("possibly_open", "verified_active", "requires a verified-open listing"),
        (
            "verified_official_open",
            "verified_unavailable",
            "conflicts with a verified-open listing",
        ),
    ],
)
def test_schema_two_application_and_listing_statuses_cannot_conflict(
    listing_status, application_status, message
) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status=listing_status)
    data["jobs"][0].update(
        application_url_status=application_status,
        application_url_last_verified_at=NOW,
    )

    with pytest.raises(Phase2ValidationError, match=message):
        LiveJobCollection.from_dict(data).validate(production_role_catalog(), sources)


def test_schema_one_and_two_live_job_shapes_cannot_be_silently_mixed() -> None:
    legacy = live_job_fixture_data()
    legacy["schema_version"] = 2
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        LiveJobCollection.from_dict(legacy)

    source = source_data()
    modern = live_job_v2_data(source, listing_status="verified_official_open")
    modern["schema_version"] = 1
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        LiveJobCollection.from_dict(modern)


def test_schema_two_live_job_typed_round_trip_requires_source_context(tmp_path) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    value = LiveJobCollection.from_dict(
        live_job_v2_data(source, listing_status="verified_official_open")
    )
    path = save_live_job_collection(
        value,
        tmp_path / "jobs-v2.json",
        catalog=production_role_catalog(),
        sources=sources,
    )
    loaded = load_live_job_collection(
        path, catalog=production_role_catalog(), sources=sources
    )
    assert loaded == value

    with pytest.raises(Phase2ValidationError, match="requires a JD Source Collection"):
        load_live_job_collection(path, catalog=production_role_catalog())


def test_schema_two_typed_load_rejects_tampered_application_state(tmp_path) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    data = live_job_v2_data(source, listing_status="verified_official_open")
    data["jobs"][0]["application_url_last_verified_at"] = None
    path = tmp_path / "tampered-jobs-v2.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(Phase2ValidationError, match="requires a verification timestamp"):
        load_live_job_collection(
            path,
            catalog=production_role_catalog(),
            sources=sources,
        )


def test_schema_two_direct_dataclass_cannot_bypass_save_validation(tmp_path) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    value = LiveJobCollection.from_dict(
        live_job_v2_data(source, listing_status="verified_official_open")
    )
    invalid_job = replace(value.jobs[0], application_url_last_verified_at=None)
    invalid_value = replace(value, jobs=(invalid_job,))

    with pytest.raises(Phase2ValidationError, match="requires a verification timestamp"):
        save_live_job_collection(
            invalid_value,
            tmp_path / "jobs-v2.json",
            catalog=production_role_catalog(),
            sources=sources,
        )


def test_schema_two_atomic_replacement_failure_preserves_existing_file(
    tmp_path, monkeypatch
) -> None:
    source = source_data()
    sources = JDSourceCollection.from_dict(source_collection_data([source]))
    value = LiveJobCollection.from_dict(
        live_job_v2_data(source, listing_status="verified_official_open")
    )
    path = tmp_path / "jobs-v2.json"
    original = b'{"existing": true}\n'
    path.write_bytes(original)

    def fail_replace(source_path, target_path):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("aarvia.phase2_storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        save_live_job_collection(
            value,
            path,
            catalog=production_role_catalog(),
            sources=sources,
        )

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
