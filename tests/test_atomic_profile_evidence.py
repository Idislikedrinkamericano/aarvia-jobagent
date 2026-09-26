from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import production_capability_rubric
from aarvia.llm_client import LLMSettings
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    AtomicEvidenceLocator,
    MappingRejectionReason,
    OpenAIProfileDimensionMapper,
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
    deterministic_mapping_reasoning,
    generate_atomic_mapping_id,
    generate_mapping_id,
    load_mapping_candidates,
    save_mapping_candidates,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    RoleRecommendationArtifact,
    build_role_recommendation,
    generate_recommendation_id,
    load_role_recommendation,
    save_role_recommendation,
)
from recommendation_fixtures import (
    clone_payload,
    mapping_transport_payload,
    synthetic_profile,
)


NOW = "2026-09-20T12:00:00+00:00"


def parse(payload: dict, profile: CareerProfile | None = None):
    return ProfileDimensionMappingCandidateSet.from_provider_payload(
        payload,
        profile=profile or synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
    )


def two_same_role_mappings(payload: dict) -> tuple[dict, dict]:
    first = payload["mappings"][0]
    second = next(
        item
        for item in payload["mappings"][1:]
        if item["role_id"] == first["role_id"]
    )
    return first, second


def set_summary_evidence(mapping: dict, excerpt: str) -> None:
    mapping["profile_fact_references"] = [{
        "path": "experience_overview[0].short_factual_summary",
        "value_snapshot": "Built and evaluated a small software system.",
        "exact_excerpt": excerpt,
    }]


def long_summary_profile() -> CareerProfile:
    data = synthetic_profile().to_dict()
    data["experience_overview"][0]["short_factual_summary"] = (
        "Built agent workflows; integrated external tools and provider APIs. "
        "Implemented Python validation, and developed deterministic tests."
    )
    return CareerProfile.from_dict(data)


def one_span_transport_payload(span_id: str) -> dict:
    mapping = deepcopy(clone_payload()["mappings"][0])
    mapping["profile_fact_references"] = [{"span_id": span_id}]
    return {
        "mappings": [mapping],
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }


def isolate_transport(payload: dict, profile: CareerProfile):
    encoded = json.dumps(payload, sort_keys=True)
    return ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload,
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=1,
        response_hash_reference=(
            "sha256:" + __import__("hashlib").sha256(encoded.encode()).hexdigest()
        ),
        evidence_spans=canonical_evidence_span_inventory(profile),
    )


def test_canonical_span_inventory_splits_realistic_long_summary_deterministically() -> None:
    profile = long_summary_profile()
    first = canonical_evidence_span_inventory(profile)
    second = canonical_evidence_span_inventory(profile)
    summary_spans = tuple(
        item for item in first
        if item.path == "experience_overview[0].short_factual_summary"
    )

    assert first == second
    assert [item.text for item in summary_spans] == [
        "Built agent workflows;",
        "integrated external tools and provider APIs.",
        "Implemented Python validation",
        "and developed deterministic tests.",
    ]
    assert all(item.span_id.startswith("span_") for item in summary_spans)
    assert all(
        left.end_offset <= right.start_offset
        for left, right in zip(summary_spans, summary_spans[1:])
    )


def test_canonical_span_inventory_splits_semicolon_without_rewriting_text() -> None:
    data = synthetic_profile().to_dict()
    data["experience_overview"][0]["short_factual_summary"] = (
        "Built workflow;validated output."
    )
    profile = CareerProfile.from_dict(data)
    spans = [
        item.text for item in canonical_evidence_span_inventory(profile)
        if item.path == "experience_overview[0].short_factual_summary"
    ]

    assert spans == ["Built workflow;", "validated output."]


def test_span_id_materializes_existing_atomic_locator_without_persisting_transport_id() -> None:
    profile = long_summary_profile()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.text == "Implemented Python validation"
    )

    result = isolate_transport(one_span_transport_payload(span.span_id), profile)
    locator = result.mappings[0].atomic_evidence[0]

    assert result.validation_report is None
    assert locator == span.materialize(profile)
    assert locator.profile_reference.path == span.path
    assert locator.exact_excerpt == span.text
    assert (locator.start_offset, locator.end_offset) == (
        span.start_offset, span.end_offset
    )
    assert locator.evidence_fingerprint.startswith("sha256:")
    assert "span_id" not in json.dumps(result.to_dict())


def test_transport_materialization_mapping_and_recommendation_round_trip(tmp_path) -> None:
    profile = long_summary_profile()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.text == "Built agent workflows;"
    )
    candidates = isolate_transport(one_span_transport_payload(span.span_id), profile)
    mapping_path = save_mapping_candidates(
        candidates,
        tmp_path / "mapping.json",
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    loaded_candidates = load_mapping_candidates(
        mapping_path,
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    recommendation = build_role_recommendation(
        profile=profile,
        mapping_candidates=loaded_candidates,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )
    recommendation_path = save_role_recommendation(
        recommendation,
        tmp_path / "recommendation.json",
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    loaded_recommendation = load_role_recommendation(
        recommendation_path,
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )

    assert loaded_candidates == candidates
    assert loaded_recommendation == recommendation
    assert "span_id" not in mapping_path.read_text()
    assert "span_id" not in recommendation_path.read_text()


def test_forged_span_id_is_rejected_without_provider_chosen_evidence() -> None:
    profile = long_summary_profile()
    result = isolate_transport(one_span_transport_payload("span_forged"), profile)

    assert result.validation_report is not None
    assert result.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.INVALID_PROFILE_REFERENCE
    )
    assert not result.mappings


@pytest.mark.parametrize(
    "collection",
    [
        "directional_signals",
        "constraints",
    ],
)
def test_forged_span_id_is_rejected_for_every_reference_kind(
    collection
) -> None:
    profile = synthetic_profile()
    payload = mapping_transport_payload(profile=profile)
    payload[collection][0]["profile_fact_references"] = [
        {"span_id": "span_forged"}
    ]

    result = isolate_transport(payload, profile)

    reasons = {
        item.reason_code for item in result.validation_report.rejections
    }
    assert MappingRejectionReason.INVALID_PROFILE_REFERENCE in reasons


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exact_excerpt", "Invented evidence"),
        ("start_offset", 0),
        ("end_offset", 10),
        ("evidence_fingerprint", "sha256:forged"),
        ("path", "experience_overview[0].short_factual_summary"),
    ],
)
def test_provider_cannot_inject_materialized_span_fields(field, value) -> None:
    profile = long_summary_profile()
    span = canonical_evidence_span_inventory(profile)[0]
    payload = one_span_transport_payload(span.span_id)
    payload["mappings"][0]["profile_fact_references"][0][field] = value

    result = isolate_transport(payload, profile)

    assert result.validation_report is not None
    assert result.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.PROVIDER_FIELD_INJECTION
    )


def test_duplicate_and_cross_dimension_span_reuse_remain_rejected() -> None:
    profile = long_summary_profile()
    span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path == "experience_overview[0].short_factual_summary"
    )
    duplicate = one_span_transport_payload(span.span_id)
    duplicate["mappings"][0]["profile_fact_references"].append(
        {"span_id": span.span_id}
    )
    duplicate_result = isolate_transport(duplicate, profile)
    assert duplicate_result.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.DUPLICATE_ATOMIC_EVIDENCE
    )

    reused = one_span_transport_payload(span.span_id)
    second = deepcopy(clone_payload()["mappings"][1])
    second["profile_fact_references"] = [{"span_id": span.span_id}]
    reused["mappings"].append(second)
    reused_result = isolate_transport(reused, profile)
    assert MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE in {
        item.reason_code for item in reused_result.validation_report.rejections
    }


def test_two_non_overlapping_excerpts_support_two_dimensions() -> None:
    payload = clone_payload(all_demonstrated=True)
    first, second = two_same_role_mappings(payload)
    set_summary_evidence(first, "Built")
    set_summary_evidence(second, "evaluated")

    result = parse(payload)
    selected = [
        item for item in result.mappings
        if item.dimension_id in {first["dimension_id"], second["dimension_id"]}
    ]

    assert len(selected) == 2
    assert {item.atomic_evidence[0].exact_excerpt for item in selected} == {
        "Built", "evaluated"
    }


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Built and evaluated", "Built and evaluated"),
        ("Built and evaluated", "evaluated a small"),
    ],
)
def test_duplicate_or_overlapping_excerpt_is_rejected(left: str, right: str) -> None:
    payload = clone_payload(all_demonstrated=True)
    first, second = two_same_role_mappings(payload)
    set_summary_evidence(first, left)
    set_summary_evidence(second, right)

    with pytest.raises(
        Phase2ValidationError,
        match="overlapping atomic evidence|one broad Profile fact",
    ):
        parse(payload)


def test_excerpt_must_be_exact_unique_and_use_complete_token_boundaries() -> None:
    data = synthetic_profile().to_dict()
    data.pop("open_questions")
    data["experience_overview"][0]["short_factual_summary"] = (
        "Used Python for validation and Python for automation."
    )
    profile = CareerProfile.from_dict(data)

    with pytest.raises(Phase2ValidationError, match="exactly once"):
        AtomicEvidenceLocator.capture(
            profile, "experience_overview[0].short_factual_summary", "Python"
        )
    with pytest.raises(Phase2ValidationError, match="exactly once"):
        AtomicEvidenceLocator.capture(
            profile, "experience_overview[0].short_factual_summary", "python"
        )
    with pytest.raises(Phase2ValidationError, match="token boundaries"):
        AtomicEvidenceLocator.capture(
            profile, "experience_overview[0].short_factual_summary", "validat"
        )


@pytest.mark.parametrize(
    "mutation",
    ["path", "excerpt", "start", "end", "evidence_fingerprint", "profile_fingerprint"],
)
def test_typed_load_recomputes_atomic_locator_and_rejects_tampering(
    tmp_path, mutation: str
) -> None:
    profile = synthetic_profile()
    value = parse(clone_payload())
    path = save_mapping_candidates(
        value,
        tmp_path / "mapping.json",
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    data = json.loads(path.read_text())
    locator = data["mappings"][0]["atomic_evidence"][0]
    if mutation == "path":
        locator["profile_reference"]["path"] = "skills[99].skill_name"
    elif mutation == "excerpt":
        locator["exact_excerpt"] = "Invented capability"
    elif mutation == "start":
        locator["start_offset"] += 1
    elif mutation == "end":
        locator["end_offset"] += 1
    elif mutation == "evidence_fingerprint":
        locator["evidence_fingerprint"] = "sha256:" + "0" * 64
    else:
        locator["profile_reference"]["profile_fingerprint"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(data))

    with pytest.raises(Phase2ValidationError):
        load_mapping_candidates(
            path,
            profile=profile,
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


def test_provider_cannot_inject_local_locator_or_global_decision_fields() -> None:
    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["start_offset"] = 0
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        parse(payload)

    for field in ("score", "rank"):
        payload = clone_payload()
        payload[field] = 100
        with pytest.raises(Phase2ValidationError, match="unknown fields"):
            parse(payload)


def test_typed_load_recomputes_cross_dimension_contribution_cap(tmp_path) -> None:
    current = parse(clone_payload(all_demonstrated=True))
    first = current.mappings[0]
    second_index = next(
        index
        for index, item in enumerate(current.mappings[1:], 1)
        if item.role_id == first.role_id and item.dimension_id != first.dimension_id
    )
    second = current.mappings[second_index]
    locator = first.atomic_evidence[0]
    replacement = replace(
        second,
        profile_fact_references=(locator.profile_reference,),
        atomic_evidence=(locator,),
        mapping_id=generate_atomic_mapping_id(
            role_id=second.role_id,
            dimension_id=second.dimension_id,
            status=second.match_status,
            relationship=second.relationship,
            evidence_locators=(locator,),
        ),
        reasoning=deterministic_mapping_reasoning(
            dimension_name=production_capability_rubric().dimension(
                second.dimension_id
            ).name,
            status=second.match_status,
            inference_type=second.inference_type,
            evidence_locators=(locator,),
        ),
    )
    tampered = replace(
        current,
        mappings=tuple(
            replacement if index == second_index else item
            for index, item in enumerate(current.mappings)
        ),
    )
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(tampered.to_dict()))

    with pytest.raises(Phase2ValidationError, match="one broad Profile fact"):
        load_mapping_candidates(
            path,
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )

    payload = clone_payload()
    payload["decision"] = {"primary_role": "applied_ai_engineer"}
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        parse(payload)


def test_recommendation_reasoning_is_derived_from_exact_evidence(tmp_path) -> None:
    payload = clone_payload()
    payload["mappings"][0]["reasoning"] = (
        "Built retrieval-grounded applications and production RAG workflows."
    )
    candidates = parse(payload)
    artifact = build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=candidates,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )
    encoded = json.dumps(artifact.to_dict())

    assert "retrieval-grounded" not in encoded
    assert "RAG workflows" not in encoded
    assert "Synthetic Skill" in encoded

    path = save_role_recommendation(
        artifact,
        tmp_path / "recommendation.json",
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    data = json.loads(path.read_text())
    assessment = next(
        item
        for role in data["role_results"]
        for item in role["extended_current_fit"]["dimension_assessments"]
        if item["supporting_atomic_evidence"]
    )
    assessment["reasoning"] = ["Profile proves retrieval-grounded applications."]
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError, match="reasoning"):
        load_role_recommendation(
            path,
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


class FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0))


def test_retry_preserves_legal_atomic_mappings_without_third_call() -> None:
    invalid = clone_payload()
    invalid["mappings"][0]["profile_fact_references"][0]["exact_excerpt"] = "invented"
    legal = clone_payload()
    responses = FakeResponses([
        json.dumps(mapping_transport_payload(invalid, strict=False)),
        json.dumps(mapping_transport_payload(legal)),
    ])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.mappings
    assert result.validation_report is None
    assert len(responses.calls) == 2


def test_mapping_schema_one_remains_explicitly_readable_and_stable() -> None:
    current = parse(clone_payload())
    legacy_data = current.to_dict()
    legacy_data["schema_version"] = 1
    for encoded, candidate in zip(legacy_data["mappings"], current.mappings):
        encoded.pop("atomic_evidence")
        encoded["mapping_id"] = generate_mapping_id(
            role_id=candidate.role_id,
            dimension_id=candidate.dimension_id,
            status=candidate.match_status,
            relationship=candidate.relationship,
            references=candidate.profile_fact_references,
        )

    legacy = ProfileDimensionMappingCandidateSet.from_dict(legacy_data)
    legacy.validate(
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )

    assert legacy.schema_version == 1
    assert legacy.to_dict() == legacy_data
    recommendation = build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=legacy,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )
    assert recommendation.schema_version == 2
    assert recommendation.mapping_schema_version == 1


def test_recommendation_schema_two_remains_explicitly_readable_and_stable() -> None:
    current = build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=parse(clone_payload()),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )
    legacy_data = current.to_dict()
    legacy_data["schema_version"] = 2
    legacy_data["mapping_schema_version"] = 1
    for role in legacy_data["role_results"]:
        for axis_name in ("core_current_fit", "extended_current_fit"):
            for assessment in role[axis_name]["dimension_assessments"]:
                assessment.pop("supporting_atomic_evidence")
    parsed = RoleRecommendationArtifact.from_dict(legacy_data)
    legacy_data["recommendation_set_id"] = generate_recommendation_id(
        profile_fingerprint=parsed.profile_fingerprint,
        rubric_version=parsed.rubric_version,
        catalog_version=parsed.catalog_version,
        provider_name=parsed.provider_name,
        provider_model=parsed.provider_model,
        role_results=parsed.role_results,
        schema_version=2,
    )
    legacy = RoleRecommendationArtifact.from_dict(legacy_data)
    legacy.validate(
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )

    assert legacy.schema_version == 2
    assert legacy.to_dict() == legacy_data


def test_direct_construction_cannot_mix_mapping_and_recommendation_schemas() -> None:
    current = build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=parse(clone_payload()),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at=NOW,
    )
    with pytest.raises(Phase2ValidationError, match="schema versions"):
        replace(current, mapping_schema_version=1).validate(
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )
