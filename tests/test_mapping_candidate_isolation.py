from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import production_capability_rubric
from aarvia.llm_client import LLMRequestError, LLMSettings
from aarvia.profile_dimension_mapping import (
    CurrentMatchStatus,
    MappingCandidateKind,
    MAPPING_REJECTION_WARNING_PREFIX,
    MappingRejectionReason,
    OpenAIProfileDimensionMapper,
    ProfileDimensionMappingCandidateSet,
    _CodedMappingValidationError,
    _MappingValidationCode,
    _reason_from_validation_error,
    mapping_validation_report_from_warnings,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import (
    FitBand,
    RecommendationConfidence,
    build_role_recommendation,
    load_role_recommendation,
    save_role_recommendation,
)
from recommendation_fixtures import (
    clone_payload,
    mapping_payload,
    mapping_transport_payload,
    synthetic_profile,
)


def isolated(payload: dict, *, attempt: int = 2) -> ProfileDimensionMappingCandidateSet:
    encoded = json.dumps(payload, sort_keys=True)
    return ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=attempt,
        response_hash_reference="sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
    )


def recommendation(candidates: ProfileDimensionMappingCandidateSet):
    return build_role_recommendation(
        profile=synthetic_profile(),
        mapping_candidates=candidates,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        created_at="2026-09-19T12:00:00+00:00",
    )


def interest_mapping(payload: dict, index: int = 0, *, status: str = "demonstrated") -> str:
    item = payload["mappings"][index]
    item["match_status"] = status
    item["profile_fact_references"] = [{
        "path": "career_preferences.currently_considered_roles[0]",
        "value_snapshot": "AI Engineer",
    }]
    item["inference_type"] = "direct"
    return item["dimension_id"]


def reject_current_fit_mapping(payload: dict, index: int) -> None:
    payload["mappings"][index]["profile_fact_references"][0]["exact_excerpt"] = (
        f"missing atomic evidence {index}"
    )


def response_hash(payload_text: str) -> str:
    return "sha256:" + hashlib.sha256(payload_text.encode()).hexdigest()


def transport_text(payload: dict, **json_kwargs) -> str:
    return json.dumps(
        mapping_transport_payload(payload, strict=False), **json_kwargs
    )


@pytest.mark.parametrize(
    "status",
    ["demonstrated", "partially_demonstrated", "adjacent_transferable", "not_demonstrated"],
)
def test_interest_current_fit_statuses_are_isolated(status) -> None:
    payload = clone_payload()
    affected_dimension = interest_mapping(payload, status=status)
    result = isolated(payload)
    report = result.validation_report

    assert report is not None and report.rejected_count == 1
    assert report.rejections[0].reason_code == MappingRejectionReason.INTEREST_CURRENT_FIT
    assert report.rejections[0].dimension_id == affected_dimension
    assert all(item.dimension_id != affected_dimension for item in result.mappings)


def test_interest_directional_signal_remains_valid() -> None:
    result = isolated(mapping_payload())
    assert result.validation_report is None
    assert any(
        reference.path == "career_preferences.currently_considered_roles[0]"
        for signal in result.directional_signals
        for reference in signal.profile_fact_references
    )


def test_legal_evidence_survives_interest_rejection_and_unknown_lowers_coverage() -> None:
    payload = clone_payload(all_demonstrated=True)
    affected_dimension = interest_mapping(payload)
    result = isolated(payload)
    artifact = recommendation(result)
    role = next(
        item for item in artifact.role_results
        if any(
            assessment.dimension_id == affected_dimension
            for assessment in item.extended_current_fit.dimension_assessments
        )
    )
    assessment = next(
        item for item in role.extended_current_fit.dimension_assessments
        if item.dimension_id == affected_dimension
    )

    assert result.mappings
    assert assessment.status.value == "unknown"
    assert role.extended_current_fit.coverage < 1.0
    assert role.recommendation_confidence.result in {
        RecommendationConfidence.LOW,
        RecommendationConfidence.INSUFFICIENT,
    }
    assert "gap" not in json.dumps(artifact.to_dict()).casefold()


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0))


def test_second_recoverable_response_isolated_without_third_call() -> None:
    first = clone_payload(); interest_mapping(first)
    second = clone_payload(); interest_mapping(second)
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert len(responses.calls) == 2
    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    repair = responses.calls[1]["instructions"]
    assert "Career interests and target roles may only support Directional Fit." in repair
    assert "Never rewrite an interest as demonstrated ability." in repair
    assert "use a legal evidence span only when it independently demonstrates" in repair
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_rejection_count_not_lower"
    )


def test_run9_shape_retry_repairs_inference_rules_and_preserves_accepted_candidates() -> None:
    first = clone_payload()
    for index in (0, 1, 2, 3):
        first["mappings"][index]["inference_type"] = "provider_invented_inference"
    for index in (4, 5, 6):
        first["mappings"][index].update(
            inference_type="bounded_semantic",
            review_required=False,
        )
    accepted_dimension_ids = {
        item["dimension_id"] for item in first["mappings"][7:]
    }
    second = clone_payload()
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert len(responses.calls) == 2
    assert result.validation_report is None
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 2
    retry = responses.calls[1]["instructions"]
    assert (
        "select inference_type only from direct, bounded_semantic, or adjacent_transfer"
        in retry
    )
    assert (
        "bounded_semantic and adjacent_transfer must use review_required=true" in retry
    )
    assert "must include every one of them unchanged" in retry
    for dimension_id in accepted_dimension_ids:
        assert dimension_id in retry
    marker = "replace, or weaken accepted candidates: "
    accepted_retry_payload, _ = json.JSONDecoder().raw_decode(
        retry[retry.index(marker) + len(marker):]
    )
    serialized_accepted = json.dumps(accepted_retry_payload, sort_keys=True)
    for forbidden in (
        "mapping_id",
        "profile_fingerprint",
        "value_snapshot",
        "exact_excerpt",
        "start_offset",
        "end_offset",
        "evidence_fingerprint",
    ):
        assert forbidden not in serialized_accepted


def test_retry_that_deletes_an_accepted_candidate_cannot_replace_first_attempt() -> None:
    first = clone_payload()
    first["mappings"][0]["inference_type"] = "provider_invented_inference"
    accepted_dimension_id = first["mappings"][1]["dimension_id"]
    second = clone_payload()
    second["mappings"] = [
        item
        for item in second["mappings"]
        if item["dimension_id"] != accepted_dimension_id
    ]
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    assert any(
        item.dimension_id == accepted_dimension_id for item in result.mappings
    )
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_dimension_coverage_decreased"
    )


def test_retry_selects_second_attempt_only_when_it_strictly_improves() -> None:
    first = clone_payload()
    reject_current_fit_mapping(first, 0)
    reject_current_fit_mapping(first, 1)
    second = clone_payload()
    reject_current_fit_mapping(second, 0)
    first_text = transport_text(first)
    second_text = transport_text(second)
    responses = FakeResponses([first_text, second_text])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert len(responses.calls) == 2
    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 2
    assert result.validation_report.rejected_count == 1
    assert result.validation_report.response_hash_reference == response_hash(second_text)
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 2
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_2_selected_strictly_improved"
    )

    report = mapping_validation_report_from_warnings(
        recommendation(result).profile_conflict_warnings
    )
    assert report is not None
    assert report.attempt_number == 2
    assert report.response_hash_reference == response_hash(second_text)


def test_retry_retains_first_attempt_when_rejection_count_regresses() -> None:
    first = clone_payload()
    second = clone_payload()
    for index in range(10):
        reject_current_fit_mapping(first, index)
        reject_current_fit_mapping(second, index)
    for index in range(2):
        first["directional_signals"][index]["role_id"] = f"unknown_role_{index}"
        second["directional_signals"][index]["role_id"] = f"unknown_role_{index}"
    second["directional_signals"][2]["role_id"] = "one_more_unknown_role"
    first_text = transport_text(first)
    second_text = transport_text(second)
    responses = FakeResponses([first_text, second_text])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    assert result.validation_report.rejected_count == 12
    assert result.validation_report.response_hash_reference == response_hash(first_text)
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_accepted_candidates_lost"
    )
    report = mapping_validation_report_from_warnings(
        recommendation(result).profile_conflict_warnings
    )
    assert report is not None
    assert report.attempt_number == 1
    assert report.response_hash_reference == response_hash(first_text)


def test_retry_tie_retains_first_attempt_and_its_response_hash() -> None:
    payload = clone_payload()
    reject_current_fit_mapping(payload, 0)
    first_text = transport_text(payload, separators=(",", ":"))
    second_text = transport_text(payload, indent=2)
    responses = FakeResponses([first_text, second_text])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    assert result.validation_report.response_hash_reference == response_hash(first_text)
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_rejection_count_not_lower"
    )


def test_retry_candidate_volume_stuffing_cannot_win() -> None:
    first = clone_payload()
    reject_current_fit_mapping(first, 0)
    reject_current_fit_mapping(first, 1)
    second = clone_payload()
    reject_current_fit_mapping(second, 0)
    garbage = deepcopy(second["directional_signals"][0])
    garbage["role_id"] = "provider_invented_role"
    second["directional_signals"].append(garbage)
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_candidate_volume_increased"
    )


def test_retry_role_coverage_drop_cannot_replace_first_attempt() -> None:
    first = clone_payload()
    reject_current_fit_mapping(first, 0)
    reject_current_fit_mapping(first, 1)
    second = clone_payload()
    reject_current_fit_mapping(second, 0)
    for collection in ("mappings", "directional_signals", "constraints"):
        second[collection] = [
            item for item in second[collection]
            if item["role_id"] != "research_engineer"
        ]
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is not None
    assert result.validation_report.attempt_number == 1
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert mapper.attempt_diagnostics[-1]["retry_selection_reason"] == (
        "attempt_1_retained_role_coverage_decreased"
    )


def test_unknown_free_text_role_ids_trigger_canonical_retry_then_succeed() -> None:
    first = clone_payload()
    first["mappings"][0]["role_id"] = "ai_agent_engineer"
    first["directional_signals"][0]["role_id"] = (
        "ai_ml_software_engineer_internship"
    )
    second = clone_payload()
    responses = FakeResponses([transport_text(first), transport_text(second)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert len(responses.calls) == 2
    assert result.validation_report is None
    allowed = list(production_capability_rubric().supported_role_ids)
    repair = responses.calls[1]["instructions"]
    assert "unsupported role_id" in repair
    assert "Correct the rejected candidates" in repair
    assert "Do not expand the candidate set" in repair
    for role_id in allowed:
        assert role_id in repair
    assert all(item.role_id in allowed for item in result.mappings)
    assert all(item.role_id in allowed for item in result.directional_signals)
    assert all(item.role_id in allowed for item in result.constraints)


def test_unknown_free_text_role_ids_are_isolated_after_second_attempt() -> None:
    outputs = []
    for role_id in ("ai_agent_engineer", "ai_ml_software_engineer_internship"):
        payload = clone_payload()
        payload["mappings"][0]["role_id"] = role_id
        outputs.append(transport_text(payload))
    responses = FakeResponses(outputs)
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert len(responses.calls) == 2
    assert result.validation_report is not None
    assert result.validation_report.rejections[0].reason_code == MappingRejectionReason.UNKNOWN_ROLE
    assert all(item.role_id != "ai_ml_software_engineer_internship" for item in result.mappings)


def test_all_current_fit_rejected_remains_insufficient_not_not_demonstrated() -> None:
    payload = clone_payload()
    for index in range(len(payload["mappings"])):
        interest_mapping(payload, index)
    artifact = recommendation(isolated(payload))

    for role in artifact.role_results:
        assert role.core_current_fit.band == FitBand.INSUFFICIENT
        assert role.core_current_fit.coverage == 0.0
        assert all(
            item.status.value == "unknown"
            for item in role.core_current_fit.dimension_assessments
        )


def test_all_provider_candidates_rejected_adds_structured_blocker() -> None:
    payload = clone_payload()
    for index in range(len(payload["mappings"])):
        interest_mapping(payload, index)
    for item in payload["directional_signals"]:
        item["role_id"] = "unknown_role"
    for item in payload["constraints"]:
        item["role_id"] = "unknown_role"
    candidates = isolated(payload)
    artifact = recommendation(candidates)

    assert candidates.validation_report is not None
    assert candidates.validation_report.all_candidates_rejected
    assert "insufficient_profile_coverage" in artifact.blockers
    assert "provider_mapping_insufficient" in artifact.blockers
    assert all(
        result.recommendation_confidence.result == RecommendationConfidence.INSUFFICIENT
        for result in artifact.role_results
    )


def test_unknown_role_dimension_and_value_mismatch_are_isolated() -> None:
    payload = clone_payload()
    payload["mappings"][0]["role_id"] = "unknown_role"
    payload["mappings"][1]["dimension_id"] = "unknown_dimension"
    payload["mappings"][2]["profile_fact_references"][0]["value_snapshot"] = "Invented"
    result = isolated(payload)
    reasons = {item.reason_code for item in result.validation_report.rejections}

    assert reasons == {
        MappingRejectionReason.UNKNOWN_ROLE,
        MappingRejectionReason.UNKNOWN_DIMENSION,
        MappingRejectionReason.PROFILE_VALUE_MISMATCH,
    }


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda item: item["profile_fact_references"][0].update(
                exact_excerpt="not present in the Profile field"
            ),
            MappingRejectionReason.INVALID_EXACT_EXCERPT,
        ),
        (
            lambda item: item["profile_fact_references"][0].update(
                exact_excerpt="Synthe"
            ),
            MappingRejectionReason.INVALID_EVIDENCE_TOKEN_BOUNDARY,
        ),
        (
            lambda item: item.update(match_status="provider_invented_status"),
            MappingRejectionReason.INVALID_MATCH_STATUS,
        ),
        (
            lambda item: item.update(profile_fact_references=[]),
            MappingRejectionReason.INVALID_STATUS_EVIDENCE,
        ),
        (
            lambda item: item.update(
                inference_type="bounded_semantic", review_required=False
            ),
            MappingRejectionReason.INVALID_INFERENCE_REVIEW,
        ),
    ],
)
def test_real_v4_atomic_failure_shapes_have_specific_reason_codes(
    mutate, expected
) -> None:
    payload = clone_payload()
    mutate(payload["mappings"][0])

    result = isolated(payload)

    assert result.validation_report is not None
    assert result.validation_report.rejections[0].reason_code == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (
            _MappingValidationCode.INVALID_EXACT_EXCERPT,
            MappingRejectionReason.INVALID_EXACT_EXCERPT,
        ),
        (
            _MappingValidationCode.INVALID_EVIDENCE_TOKEN_BOUNDARY,
            MappingRejectionReason.INVALID_EVIDENCE_TOKEN_BOUNDARY,
        ),
        (
            _MappingValidationCode.DUPLICATE_ATOMIC_EVIDENCE,
            MappingRejectionReason.DUPLICATE_ATOMIC_EVIDENCE,
        ),
        (
            _MappingValidationCode.OVERLAPPING_ATOMIC_EVIDENCE,
            MappingRejectionReason.OVERLAPPING_ATOMIC_EVIDENCE,
        ),
        (
            _MappingValidationCode.CROSS_DIMENSION_EVIDENCE_REUSE,
            MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE,
        ),
        (
            _MappingValidationCode.INVALID_MATCH_STATUS,
            MappingRejectionReason.INVALID_MATCH_STATUS,
        ),
        (
            _MappingValidationCode.INVALID_STATUS_EVIDENCE,
            MappingRejectionReason.INVALID_STATUS_EVIDENCE,
        ),
        (
            _MappingValidationCode.INVALID_INFERENCE_REVIEW,
            MappingRejectionReason.INVALID_INFERENCE_REVIEW,
        ),
        (
            _MappingValidationCode.INVALID_CURRENT_FIT_STRUCTURE,
            MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE,
        ),
        (
            _MappingValidationCode.INVALID_EVIDENCE_STRENGTH,
            MappingRejectionReason.INVALID_EVIDENCE_STRENGTH,
        ),
        (
            _MappingValidationCode.INVALID_PROVIDER_CONFIDENCE,
            MappingRejectionReason.INVALID_PROVIDER_CONFIDENCE,
        ),
        (
            _MappingValidationCode.INVALID_REVIEW_FLAG,
            MappingRejectionReason.INVALID_REVIEW_FLAG,
        ),
        (
            _MappingValidationCode.INVALID_MAPPING_PROVENANCE,
            MappingRejectionReason.INVALID_MAPPING_PROVENANCE,
        ),
        (
            _MappingValidationCode.DUPLICATE_MAPPING,
            MappingRejectionReason.DUPLICATE_MAPPING,
        ),
        (
            _MappingValidationCode.INVALID_CONTRIBUTION,
            MappingRejectionReason.INVALID_CONTRIBUTION,
        ),
    ],
)
def test_coded_atomic_classification_does_not_depend_on_exception_prose(
    code, expected
) -> None:
    error = _CodedMappingValidationError(
        code,
        "This human-readable message was deliberately changed and has no keywords.",
    )

    assert _reason_from_validation_error(
        error, raw={}, kind=MappingCandidateKind.CURRENT_FIT
    ) == expected


def test_unknown_legacy_validation_error_falls_back_safely() -> None:
    error = Phase2ValidationError(
        "A future validation failure with completely unfamiliar wording"
    )

    assert _reason_from_validation_error(
        error, raw={}, kind=MappingCandidateKind.CURRENT_FIT
    ) == MappingRejectionReason.INVALID_CURRENT_FIT


def test_duplicate_atomic_evidence_has_specific_reason_code() -> None:
    payload = clone_payload()
    reference = payload["mappings"][0]["profile_fact_references"][0]
    payload["mappings"][0]["profile_fact_references"].append(deepcopy(reference))

    result = isolated(payload)

    assert result.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.DUPLICATE_ATOMIC_EVIDENCE
    )


def test_overlapping_and_reused_atomic_evidence_have_specific_reason_codes() -> None:
    payload = clone_payload(all_demonstrated=True)
    first = payload["mappings"][0]
    second = next(
        item
        for item in payload["mappings"][1:]
        if item["role_id"] == first["role_id"]
    )
    summary = "Built and evaluated a small software system."
    for item, excerpt in (
        (first, "Built and evaluated"),
        (second, "evaluated a small"),
    ):
        item["profile_fact_references"] = [{
            "path": "experience_overview[0].short_factual_summary",
            "value_snapshot": summary,
            "exact_excerpt": excerpt,
        }]
    result = isolated(payload)
    assert MappingRejectionReason.OVERLAPPING_ATOMIC_EVIDENCE in {
        item.reason_code for item in result.validation_report.rejections
    }

    payload = clone_payload(all_demonstrated=True)
    first = payload["mappings"][0]
    second = next(
        item
        for item in payload["mappings"][1:]
        if item["role_id"] == first["role_id"]
    )
    second["profile_fact_references"] = deepcopy(first["profile_fact_references"])
    result = isolated(payload)
    assert MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE in {
        item.reason_code for item in result.validation_report.rejections
    }


def test_multiple_individually_valid_candidates_fail_joint_cap_with_specific_codes() -> None:
    payload = clone_payload(all_demonstrated=True)
    role_id = payload["mappings"][0]["role_id"]
    selected = [
        item for item in payload["mappings"] if item["role_id"] == role_id
    ][:4]
    assert len(selected) == 4
    summary = "Built and evaluated a small software system."
    for item in selected:
        item["profile_fact_references"] = [{
            "path": "experience_overview[0].short_factual_summary",
            "value_snapshot": summary,
            "exact_excerpt": summary,
        }]
        individual = {**clone_payload(), "mappings": [deepcopy(item)]}
        assert isolated(individual).validation_report is None

    combined = {**clone_payload(), "mappings": selected}
    result = isolated(combined)
    reasons = [item.reason_code for item in result.validation_report.rejections]

    assert reasons == [MappingRejectionReason.CROSS_DIMENSION_EVIDENCE_REUSE] * 3
    assert MappingRejectionReason.INVALID_CURRENT_FIT not in reasons


def test_joint_duplicate_mapping_aggregate_is_coded_not_generic() -> None:
    payload = clone_payload()
    candidate = deepcopy(payload["mappings"][0])
    individual = {**clone_payload(), "mappings": [candidate]}
    assert isolated(individual).validation_report is None
    combined = {**clone_payload(), "mappings": [candidate, deepcopy(candidate)]}

    result = isolated(combined)
    reasons = [item.reason_code for item in result.validation_report.rejections]

    assert reasons == [MappingRejectionReason.DUPLICATE_MAPPING]
    assert MappingRejectionReason.INVALID_CURRENT_FIT not in reasons


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda item: item.update(evidence_strength="extreme"),
            MappingRejectionReason.INVALID_EVIDENCE_STRENGTH,
        ),
        (
            lambda item: item.update(provider_confidence="certain"),
            MappingRejectionReason.INVALID_PROVIDER_CONFIDENCE,
        ),
        (
            lambda item: item.update(review_required="false"),
            MappingRejectionReason.INVALID_REVIEW_FLAG,
        ),
        (
            lambda item: item.update(reasoning=""),
            MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE,
        ),
        (
            lambda item: item.update(profile_fact_references={}),
            MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE,
        ),
    ],
)
def test_known_current_fit_parse_failures_never_use_generic_reason(
    mutation, expected
) -> None:
    payload = clone_payload()
    mutation(payload["mappings"][0])

    result = isolated(payload)
    reason = result.validation_report.rejections[0].reason_code

    assert reason == expected
    assert reason != MappingRejectionReason.INVALID_CURRENT_FIT


def test_non_object_current_fit_candidate_has_structured_reason() -> None:
    payload = clone_payload()
    payload["mappings"][0] = "not an object"

    result = isolated(payload)

    assert result.validation_report.rejections[0].reason_code == (
        MappingRejectionReason.INVALID_CURRENT_FIT_STRUCTURE
    )


def test_recoverable_candidate_reason_codes_cover_cross_candidate_boundaries() -> None:
    payload = clone_payload()
    payload["mappings"].append(json.loads(json.dumps(payload["mappings"][0])))
    payload["mappings"][1]["inference_type"] = "unsupported_inference"
    payload["mappings"][2]["role_id"] = "research_engineer"
    payload["mappings"][3]["relationship"] = "secondary"
    payload["mappings"][3]["evidence_strength"] = "strong"
    payload["mappings"][4]["profile_fact_references"][0]["path"] = "skills[999].skill_name"
    payload["directional_signals"][0]["profile_fact_references"] = []
    payload["constraints"][0]["profile_fact_references"] = []
    result = isolated(payload)
    reasons = {item.reason_code for item in result.validation_report.rejections}

    assert {
        MappingRejectionReason.DUPLICATE_MAPPING,
        MappingRejectionReason.UNSUPPORTED_INFERENCE,
        MappingRejectionReason.ROLE_DIMENSION_MISMATCH,
        MappingRejectionReason.INVALID_CONTRIBUTION,
        MappingRejectionReason.INVALID_PROFILE_REFERENCE,
        MappingRejectionReason.INVALID_DIRECTIONAL,
        MappingRejectionReason.INVALID_CONSTRAINT,
    } <= reasons


def test_candidate_field_injection_is_recoverable_but_top_level_injection_is_hard() -> None:
    payload = clone_payload(); payload["mappings"][0]["score"] = 99
    result = isolated(payload)
    assert result.validation_report.rejections[0].reason_code == MappingRejectionReason.PROVIDER_FIELD_INJECTION

    payload = clone_payload(); payload["score"] = 99
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        isolated(payload)


def test_real_error_shape_is_classified_as_interest_not_a_profile_alias() -> None:
    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"] = [{
        "path": "career_preferences.target_roles[0]",
        "value_snapshot": "AI Engineer",
    }]
    result = isolated(payload)
    rejection = result.validation_report.rejections[0]
    assert rejection.reason_code == MappingRejectionReason.INTEREST_CURRENT_FIT
    assert rejection.canonical_profile_path is None


def test_rejection_report_and_diagnostics_do_not_contain_values_or_secret() -> None:
    payload = clone_payload(); interest_mapping(payload)
    responses = FakeResponses([transport_text(payload), transport_text(payload)])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret-api-key", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )
    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )
    encoded = json.dumps(result.validation_report.warning_tokens())
    diagnostics = json.dumps(mapper.attempt_diagnostics)

    assert "AI Engineer" not in encoded
    assert "Sample User" not in encoded
    assert "secret-api-key" not in encoded + diagnostics
    assert mapper.attempt_diagnostics[-1]["candidate_rejection_count"] == 1
    assert mapper.attempt_diagnostics[-1]["candidate_rejection_reason_codes"] == [
        "interest_cannot_support_current_fit"
    ]


def test_retry_diagnostics_keep_both_summaries_and_safe_selection_reason(tmp_path) -> None:
    first = clone_payload()
    reject_current_fit_mapping(first, 0)
    second = clone_payload()
    reject_current_fit_mapping(second, 0)
    diagnostics_dir = tmp_path / "diagnostics"
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret-api-key", "gpt-test"),
        client=SimpleNamespace(
            responses=FakeResponses([transport_text(first), transport_text(second)])
        ),
        diagnostics_dir=diagnostics_dir,
    )

    mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    files = sorted(diagnostics_dir.glob("*.json"))
    assert len(files) == 2
    persisted = [json.loads(path.read_text()) for path in files]
    assert [item["attempt"] for item in persisted] == [1, 2]
    assert persisted[1]["selected_attempt"] == 1
    assert persisted[1]["retry_selection_reason"] == (
        "attempt_1_retained_rejection_count_not_lower"
    )
    assert all(item["raw_response_saved"] is False for item in persisted)
    assert all(item["selected_attempt"] == 1 for item in mapper.attempt_diagnostics)
    assert all(
        item["retry_selection_reason"]
        == "attempt_1_retained_rejection_count_not_lower"
        for item in mapper.attempt_diagnostics
    )
    encoded = "".join(path.read_text() for path in files)
    assert "secret-api-key" not in encoded
    assert "Synthetic Skill" not in encoded
    assert "missing atomic evidence" not in encoded


def test_span_rejection_metadata_and_retry_guidance_are_specific_but_private() -> None:
    invalid = clone_payload()
    invalid["mappings"][0]["profile_fact_references"][0]["exact_excerpt"] = (
        "private invented excerpt"
    )
    responses = FakeResponses([
        transport_text(invalid), transport_text(clone_payload())
    ])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret-api-key", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    result = mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    assert result.validation_report is None
    assert len(responses.calls) == 2
    retry = responses.calls[1]["instructions"]
    assert "invalid_profile_reference" in retry
    assert "select only a span_id from allowed_evidence_spans" in retry
    assert "private invented excerpt" not in retry
    diagnostics = json.dumps(mapper.attempt_diagnostics)
    assert "invalid_profile_reference" in diagnostics
    assert "private invented excerpt" not in diagnostics
    assert "Synthetic Skill 1" not in diagnostics
    assert "secret-api-key" not in diagnostics


def test_atomic_retry_prompt_explains_each_reported_rule_without_evidence_values() -> None:
    invalid = clone_payload()
    invalid["mappings"][0]["profile_fact_references"][0]["exact_excerpt"] = "Synthe"
    invalid["mappings"][1]["profile_fact_references"].append(
        deepcopy(invalid["mappings"][1]["profile_fact_references"][0])
    )
    invalid["mappings"][2]["match_status"] = "not_a_status"
    invalid["mappings"][3].update(
        inference_type="bounded_semantic", review_required=False
    )
    responses = FakeResponses([
        transport_text(invalid), transport_text(clone_payload())
    ])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    mapper.map(
        synthetic_profile(), production_capability_rubric(), production_role_catalog()
    )

    retry = responses.calls[1]["instructions"]
    assert "allowed_evidence_spans" in retry
    assert "only once within a mapping" in retry
    assert "match_status value allowed by the supplied JSON Schema" in retry
    assert "must use review_required=true" in retry
    assert "Synthetic Skill" not in retry


def test_recommendation_round_trip_revalidates_rejection_degradation(tmp_path) -> None:
    payload = clone_payload(all_demonstrated=True); interest_mapping(payload)
    artifact = recommendation(isolated(payload))
    path = save_role_recommendation(
        artifact,
        tmp_path / "recommendation.json",
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    assert load_role_recommendation(
        path,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    ) == artifact

    data = json.loads(path.read_text())
    confidence = data["role_results"][0]["recommendation_confidence"]
    confidence["result"] = "high"
    for component in confidence["component_results"]:
        if component["component"] == "provider_candidate_rejection":
            component["confidence"] = "high"
    path.write_text(json.dumps(data))
    with pytest.raises(Phase2ValidationError, match="confidence"):
        load_role_recommendation(
            path,
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


def test_recommendation_schema_three_validates_atomic_rejection_reason_codes(
    tmp_path,
) -> None:
    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["exact_excerpt"] = (
        "not present"
    )
    artifact = recommendation(isolated(payload))
    assert artifact.schema_version == 3
    path = save_role_recommendation(
        artifact,
        tmp_path / "recommendation.json",
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    loaded = load_role_recommendation(
        path,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    report = mapping_validation_report_from_warnings(loaded.profile_conflict_warnings)
    assert report is not None
    assert report.rejections[0].reason_code == (
        MappingRejectionReason.INVALID_EXACT_EXCERPT
    )

    data = json.loads(path.read_text())
    warning_index = next(
        index
        for index, warning in enumerate(data["profile_conflict_warnings"])
        if warning.startswith(MAPPING_REJECTION_WARNING_PREFIX)
    )
    warning = data["profile_conflict_warnings"][warning_index]
    detail = json.loads(warning.removeprefix(MAPPING_REJECTION_WARNING_PREFIX))
    detail["reason_code"] = "provider_invented_reason"
    data["profile_conflict_warnings"][warning_index] = (
        MAPPING_REJECTION_WARNING_PREFIX
        + json.dumps(detail, sort_keys=True, separators=(",", ":"))
    )
    path.write_text(json.dumps(data))

    with pytest.raises(Phase2ValidationError, match="reason_code"):
        load_role_recommendation(
            path,
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


def test_mapping_warning_round_trip_reconstructs_report_without_wire_change(tmp_path) -> None:
    from aarvia.profile_dimension_mapping import load_mapping_candidates, save_mapping_candidates

    payload = clone_payload(); interest_mapping(payload)
    candidates = isolated(payload)
    path = save_mapping_candidates(
        candidates,
        tmp_path / "mapping.json",
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    loaded = load_mapping_candidates(
        path,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    report = mapping_validation_report_from_warnings(loaded.conflict_warnings)
    assert report is not None and report.rejected_count == 1
    assert "validation_report" not in json.loads(path.read_text())

    tampered = replace(loaded, mappings=loaded.mappings[:-1])
    with pytest.raises(Phase2ValidationError, match="counts do not match"):
        tampered.validate(
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


def test_all_rejected_metadata_cannot_coexist_with_assessed_evidence() -> None:
    payload = clone_payload()
    for index in range(len(payload["mappings"])):
        interest_mapping(payload, index)
    for item in payload["directional_signals"]:
        item["role_id"] = "unknown_role"
    for item in payload["constraints"]:
        item["role_id"] = "unknown_role"
    artifact = recommendation(isolated(payload))
    role = artifact.role_results[0]
    assessment = replace(
        role.extended_current_fit.dimension_assessments[0],
        status=CurrentMatchStatus.DEMONSTRATED,
    )
    axis = replace(
        role.extended_current_fit,
        dimension_assessments=(
            assessment,
            *role.extended_current_fit.dimension_assessments[1:],
        ),
    )
    tampered = replace(
        artifact,
        role_results=(replace(role, extended_current_fit=axis), *artifact.role_results[1:]),
    )
    with pytest.raises(Phase2ValidationError, match="all-rejected"):
        tampered.validate(
            profile=synthetic_profile(),
            rubric=production_capability_rubric(),
            catalog=production_role_catalog(),
        )


def test_profile_fingerprint_injection_remains_a_hard_failure() -> None:
    payload = clone_payload(); payload["profile_fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        isolated(payload)
