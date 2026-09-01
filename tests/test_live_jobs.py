from copy import deepcopy

import pytest

from aarvia.live_jobs import LiveJobCollection
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from phase2_fixtures import live_job_fixture_data


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
