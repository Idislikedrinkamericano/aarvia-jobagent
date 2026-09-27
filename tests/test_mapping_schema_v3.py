from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import (
    CapabilityRubric,
    EvidenceClass,
    EvidenceStatusCap,
    production_capability_rubric,
)
from aarvia.llm_client import LLMSettings
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    BindingDerivationReason,
    CurrentMatchStatus,
    EvidenceStrength,
    EvidenceTrustLevel,
    OpenAIProfileDimensionMapper,
    ProfileCriterionEvidenceBinding,
    ProfileDimensionMappingCandidateSet,
    ProposedBindingType,
    ProviderCapabilities,
    canonical_evidence_span_inventory,
    evidence_class_for_locator,
    generate_evidence_binding_id,
    load_mapping_candidates,
    provider_mapping_schema,
    save_mapping_candidates,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from recommendation_fixtures import synthetic_profile


def _profile() -> CareerProfile:
    data = synthetic_profile().to_dict()
    data["skills"][0]["self_reported_proficiency"] = "intermediate"
    data["experience_overview"].append(
        {
            "experience_type": "internship",
            "organization_or_project_name": "Example Organization",
            "title_or_role": "Engineering Intern",
            "short_factual_summary": "Implemented a service endpoint. Validated system behavior.",
            "start_date": "2025-06",
            "end_date": "2025-08",
        }
    )
    return CareerProfile.from_dict(data)


def _span(profile: CareerProfile, path: str, text: str | None = None):
    matches = [
        item
        for item in canonical_evidence_span_inventory(profile)
        if item.path == path and (text is None or item.text == text)
    ]
    assert matches
    if text is not None:
        assert len(matches) == 1
    return matches[0]


def _dimension(
    rubric: CapabilityRubric,
    *,
    evidence_class: EvidenceClass,
    provisional_cap: EvidenceStatusCap | None = None,
    forbidden: bool = False,
):
    for dimension in rubric.dimensions:
        policy = dimension.evidence_support_policy
        assert policy is not None
        class_matches = (
            evidence_class in policy.forbidden_evidence_classes
            if forbidden
            else evidence_class in policy.allowed_evidence_classes
        )
        if class_matches and (
            provisional_cap is None or policy.provisional_status_cap == provisional_cap
        ):
            return dimension
    raise AssertionError("no matching policy fixture")


def _raw_binding(dimension, span, **overrides):
    value = {
        "role_id": dimension.role_id,
        "dimension_id": dimension.dimension_id,
        "criterion_id": dimension.criterion_ids[0],
        "span_id": span.span_id,
        "proposed_binding_type": "direct",
        "provider_confidence": "high",
    }
    value.update(overrides)
    return value


def _payload(*bindings):
    return {
        "mappings": list(bindings),
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }


def _parse(profile: CareerProfile, *bindings):
    return ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
        _payload(*bindings),
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        evidence_spans=canonical_evidence_span_inventory(profile),
    )


@pytest.mark.parametrize(
    "field",
    [
        "binding_id", "path", "exact_excerpt", "start_offset", "fingerprint",
        "evidence_class", "trust_level", "match_status", "evidence_strength",
        "review_required", "inference_type", "score", "rank", "recommendation",
        "decision", "review_reference",
    ],
)
def test_v3_provider_cannot_inject_local_derived_or_final_fields(field: str) -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    raw = _raw_binding(dimension, _span(profile, "skills[0].skill_name"))
    raw[field] = "injected"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        _parse(profile, raw)


def test_v3_provider_contract_contains_exactly_six_current_fit_fields() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    item = provider_mapping_schema(
        rubric, profile, mapping_schema_version=3
    )["properties"]["mappings"]["items"]
    expected = {
        "role_id", "dimension_id", "criterion_id", "span_id",
        "proposed_binding_type", "provider_confidence",
    }
    assert set(item["properties"]) == expected
    assert set(item["required"]) == expected
    assert item["additionalProperties"] is False


def test_v3_rejects_unknown_criterion_wrong_owner_and_fake_span() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    other = next(item for item in rubric.dimensions if item.dimension_id != dimension.dimension_id)
    span = _span(profile, "skills[0].skill_name")

    with pytest.raises(Phase2ValidationError, match="not present"):
        _parse(profile, _raw_binding(dimension, span, criterion_id="criterion_not_real"))
    with pytest.raises(Phase2ValidationError, match="another Dimension"):
        _parse(profile, _raw_binding(dimension, span, criterion_id=other.criterion_ids[0]))
    with pytest.raises(Phase2ValidationError, match="allowed_evidence_spans"):
        _parse(profile, _raw_binding(dimension, span, span_id="span_forged"))


def test_evidence_class_is_derived_from_profile_object_type() -> None:
    profile = _profile()
    cases = {
        "skills[0].skill_name": EvidenceClass.SKILL_NAME,
        "skills[0].self_reported_proficiency": EvidenceClass.SKILL_PROFICIENCY,
        "education[0].field_of_study": EvidenceClass.EDUCATION_FIELD,
        "experience_overview[0].short_factual_summary": EvidenceClass.PROJECT_SUMMARY,
        "experience_overview[1].short_factual_summary": EvidenceClass.EXPERIENCE_SUMMARY,
    }
    for path, expected in cases.items():
        locator = _span(profile, path).materialize(profile)
        assert evidence_class_for_locator(profile, locator) == expected


def test_structural_caps_are_derived_and_cannot_accumulate_to_partial() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    bindings = [
        _raw_binding(dimension, _span(profile, f"skills[{index}].skill_name"))
        for index in range(3)
    ]
    result = _parse(profile, *bindings)
    expected_cap = dimension.evidence_support_policy.structural_caps[EvidenceClass.SKILL_NAME]
    expected_status = {
        EvidenceStatusCap.UNKNOWN: CurrentMatchStatus.UNKNOWN,
        EvidenceStatusCap.ADJACENT_TRANSFERABLE: CurrentMatchStatus.ADJACENT,
    }[expected_cap]
    assert {item.derived_match_status for item in result.mappings} == {expected_status}
    assert all(item.trust_level == EvidenceTrustLevel.STRUCTURAL_ONLY for item in result.mappings)
    assert all(item.derived_evidence_strength == EvidenceStrength.WEAK for item in result.mappings)
    assert CurrentMatchStatus.PARTIAL not in {item.derived_match_status for item in result.mappings}


def test_skill_proficiency_requires_matching_skill_name_binding() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_PROFICIENCY)
    proficiency = _raw_binding(
        dimension, _span(profile, "skills[0].self_reported_proficiency")
    )
    with pytest.raises(Phase2ValidationError, match="skill proficiency requires"):
        _parse(profile, proficiency)

    skill_name = _raw_binding(dimension, _span(profile, "skills[0].skill_name"))
    result = _parse(profile, proficiency, skill_name)
    assert len(result.mappings) == 2
    prof = next(item for item in result.mappings if item.evidence_class == EvidenceClass.SKILL_PROFICIENCY)
    assert BindingDerivationReason.SKILL_PROFICIENCY_PAIRED in prof.derivation_reason_codes


def test_forbidden_education_evidence_is_rejected_by_dimension_policy() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(
        rubric, evidence_class=EvidenceClass.EDUCATION_FIELD, forbidden=True
    )
    with pytest.raises(Phase2ValidationError, match="forbids"):
        _parse(
            profile,
            _raw_binding(dimension, _span(profile, "education[0].field_of_study")),
        )


@pytest.mark.parametrize(
    ("evidence_class", "path"),
    [
        (EvidenceClass.PROJECT_SUMMARY, "experience_overview[0].short_factual_summary"),
        (EvidenceClass.EXPERIENCE_SUMMARY, "experience_overview[1].short_factual_summary"),
    ],
)
def test_experience_and_project_bindings_are_provisional_and_policy_capped(
    evidence_class: EvidenceClass, path: str
) -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(
        rubric,
        evidence_class=evidence_class,
        provisional_cap=EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
    )
    result = _parse(profile, _raw_binding(dimension, _span(profile, path)))
    binding = result.mappings[0]
    assert binding.trust_level == EvidenceTrustLevel.PROVISIONAL_SEMANTIC
    assert binding.derived_match_status == CurrentMatchStatus.PARTIAL
    assert binding.derived_evidence_strength == EvidenceStrength.SUPPORTING
    assert binding.derived_review_required is True
    assert binding.derived_match_status != CurrentMatchStatus.DEMONSTRATED


def test_adjacent_only_policies_keep_provisional_evidence_weak_and_adjacent() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimensions = [
        item
        for item in rubric.dimensions
        if item.evidence_support_policy is not None
        and item.evidence_support_policy.provisional_status_cap
        == EvidenceStatusCap.ADJACENT_TRANSFERABLE
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
    ]
    assert len(dimensions) >= 4
    span = _span(profile, "experience_overview[0].short_factual_summary")
    for dimension in dimensions[:4]:
        binding = _parse(profile, _raw_binding(dimension, span)).mappings[0]
        assert binding.derived_match_status == CurrentMatchStatus.ADJACENT
        assert binding.derived_evidence_strength == EvidenceStrength.WEAK
        assert binding.derived_review_required is True


def test_provider_adjacent_proposal_can_lower_but_never_raise_policy_result() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(
        rubric,
        evidence_class=EvidenceClass.PROJECT_SUMMARY,
        provisional_cap=EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
    )
    binding = _parse(
        profile,
        _raw_binding(
            dimension,
            _span(profile, "experience_overview[0].short_factual_summary"),
            proposed_binding_type="adjacent_transfer",
        ),
    ).mappings[0]
    assert binding.derived_match_status == CurrentMatchStatus.ADJACENT
    assert BindingDerivationReason.PROVIDER_ADJACENT_LIMIT in binding.derivation_reason_codes


def test_duplicate_overlap_and_cross_dimension_reuse_remain_blocked() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.PROJECT_SUMMARY)
    span = _span(profile, "experience_overview[0].short_factual_summary")
    duplicate = _raw_binding(dimension, span)
    with pytest.raises(Phase2ValidationError, match="bound twice|duplicate mapping IDs"):
        _parse(profile, duplicate, deepcopy(duplicate))

    other = next(
        item
        for item in rubric.dimensions
        if item.role_id == dimension.role_id
        and item.dimension_id != dimension.dimension_id
        and item.evidence_support_policy is not None
        and EvidenceClass.PROJECT_SUMMARY in item.evidence_support_policy.allowed_evidence_classes
    )
    with pytest.raises(Phase2ValidationError, match="across Dimensions"):
        _parse(profile, duplicate, _raw_binding(other, span))


def test_binding_id_is_stable_and_typed_load_recomputes_all_derived_fields(tmp_path: Path) -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.PROJECT_SUMMARY)
    result = _parse(
        profile,
        _raw_binding(dimension, _span(profile, "experience_overview[0].short_factual_summary")),
    )
    binding = result.mappings[0]
    assert binding.binding_id == generate_evidence_binding_id(
        role_id=binding.role_id,
        dimension_id=binding.dimension_id,
        criterion_id=binding.criterion_id,
        locator=binding.atomic_evidence,
        proposed_binding_type=binding.proposed_binding_type,
    )
    path = save_mapping_candidates(
        result, tmp_path / "mapping-v3.json", profile=profile, rubric=rubric, catalog=catalog
    )
    assert load_mapping_candidates(path, profile=profile, rubric=rubric, catalog=catalog) == result

    tamper_fields = {
        "binding_id": "binding_forged",
        "evidence_class": "skill_name",
        "trust_level": "confirmed_semantic_binding",
        "derived_match_status": "demonstrated",
        "derived_evidence_strength": "strong",
        "derived_review_required": False,
    }
    for field, value in tamper_fields.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["mappings"][0][field] = value
        altered = tmp_path / f"tampered-{field}.json"
        altered.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises((Phase2ValidationError, ValueError)):
            load_mapping_candidates(
                altered, profile=profile, rubric=rubric, catalog=catalog
            )

    forged = replace(binding, trust_level="provisional_semantic_binding")
    with pytest.raises(Phase2ValidationError, match="must use EvidenceTrustLevel"):
        forged.validate(profile=profile, rubric=rubric)
    with pytest.raises(Phase2ValidationError, match="unsupported Profile mapping schema"):
        replace(result, schema_version=True).validate(
            profile=profile, rubric=rubric, catalog=catalog
        )


def test_mapping_v3_requires_rubric_v2_while_legacy_wire_stays_explicit() -> None:
    profile = _profile(); rubric = production_capability_rubric()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    result = _parse(profile, _raw_binding(dimension, _span(profile, "skills[0].skill_name")))
    payload = result.to_dict()
    assert payload["schema_version"] == 3
    assert ProfileDimensionMappingCandidateSet.from_dict(payload).to_dict() == payload

    rubric_v1 = CapabilityRubric.from_dict(
        json.loads(
            Path("src/aarvia/catalog_data/role-capability-rubric-1.0.0.json").read_text(
                encoding="utf-8"
            )
        )
    )
    with pytest.raises(Phase2ValidationError, match="requires Capability Rubric schema 2"):
        result.validate(
            profile=profile, rubric=rubric_v1, catalog=production_role_catalog()
        )


class _ResponsesClient:
    def __init__(self, content: str):
        self.calls = []
        self.responses = SimpleNamespace(create=self.create)
        self.content = content

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.content)


class _SequentialResponsesClient:
    def __init__(self, contents: list[str]):
        self.calls = []
        self.responses = SimpleNamespace(create=self.create)
        self.contents = iter(contents)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=next(self.contents))


class _ChatClient:
    def __init__(self, content: str):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.content = content

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_openai_and_bailian_use_the_same_v3_transport_contract() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    content = json.dumps(
        _payload(_raw_binding(dimension, _span(profile, "skills[0].skill_name")))
    )
    openai = _ResponsesClient(content)
    bailian = _ChatClient(content)
    OpenAIProfileDimensionMapper(
        settings=LLMSettings(api_key="key", model="model", base_url=None),
        client=openai,
        max_attempts=1,
        mapping_schema_version=3,
    ).map(profile, rubric, catalog)
    OpenAIProfileDimensionMapper(
        settings=LLMSettings(
            api_key="key",
            model="model",
            base_url="https://example.maas.aliyuncs.com/v1",
        ),
        client=bailian,
        max_attempts=1,
        capabilities=ProviderCapabilities("chat_completions", "json_object", True, True),
        mapping_schema_version=3,
    ).map(profile, rubric, catalog)

    openai_schema = openai.calls[0]["text"]["format"]["schema"]
    openai_instructions = openai.calls[0]["instructions"]
    chat_instructions = bailian.calls[0]["messages"][0]["content"]
    assert openai_schema == provider_mapping_schema(rubric, profile, mapping_schema_version=3)
    for forbidden in ("match_status", "evidence_strength", "review_required", "binding_id"):
        assert forbidden not in openai_schema["properties"]["mappings"]["items"]["properties"]
    assert "output only role_id, dimension_id, criterion_id" in openai_instructions
    assert "output only role_id, dimension_id, criterion_id" in chat_instructions


def test_v3_isolation_uses_stable_policy_reason_codes_without_sensitive_evidence() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    dimension = _dimension(
        rubric, evidence_class=EvidenceClass.EDUCATION_FIELD, forbidden=True
    )
    result = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        _payload(
            _raw_binding(dimension, _span(profile, "education[0].field_of_study"))
        ),
        profile=profile,
        rubric=rubric,
        catalog=catalog,
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=1,
        response_hash_reference="sha256:" + "a" * 64,
        evidence_spans=canonical_evidence_span_inventory(profile),
        mapping_schema_version=3,
    )
    assert result.mappings == ()
    assert result.validation_report is not None
    assert result.validation_report.reason_codes == ("forbidden_evidence_class",)
    serialized = json.dumps(result.to_dict())
    assert "Computing" not in serialized
    assert "Built and evaluated" not in serialized


def _retry_bindings(profile: CareerProfile, rubric: CapabilityRubric):
    accepted_dimension = _dimension(rubric, evidence_class=EvidenceClass.SKILL_NAME)
    rejected_dimension = _dimension(
        rubric, evidence_class=EvidenceClass.EDUCATION_FIELD, forbidden=True
    )
    corrected_dimension = _dimension(
        rubric,
        evidence_class=EvidenceClass.EXPERIENCE_SUMMARY,
        provisional_cap=EvidenceStatusCap.PARTIALLY_DEMONSTRATED,
    )
    accepted = _raw_binding(
        accepted_dimension, _span(profile, "skills[0].skill_name")
    )
    rejected = _raw_binding(
        rejected_dimension, _span(profile, "education[0].field_of_study")
    )
    corrected = _raw_binding(
        corrected_dimension,
        _span(
            profile,
            "experience_overview[1].short_factual_summary",
            "Implemented a service endpoint.",
        ),
    )
    return accepted, rejected, corrected


def test_v3_retry_preserves_accepted_bindings_and_selects_strict_improvement() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    accepted, rejected, corrected = _retry_bindings(profile, rubric)
    client = _SequentialResponsesClient(
        [json.dumps(_payload(accepted, rejected)), json.dumps(_payload(accepted, corrected))]
    )
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings(api_key="key", model="model", base_url=None),
        client=client,
        max_attempts=2,
        mapping_schema_version=3,
    )
    result = mapper.map(profile, rubric, catalog)
    assert len(client.calls) == 2
    assert len(result.mappings) == 2
    assert {item.criterion_id for item in result.mappings} == {
        accepted["criterion_id"], corrected["criterion_id"]
    }
    retry_prompt = client.calls[1]["instructions"]
    assert accepted["span_id"] in retry_prompt
    assert "complete retry response must include every one" in retry_prompt
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 2
    safe_diagnostics = json.dumps(mapper.attempt_diagnostics)
    assert "Synthetic Skill 1" not in safe_diagnostics
    assert "Implemented a service endpoint" not in safe_diagnostics


def test_v3_retry_that_deletes_accepted_binding_keeps_first_attempt() -> None:
    profile = _profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    accepted, rejected, corrected = _retry_bindings(profile, rubric)
    client = _SequentialResponsesClient(
        [json.dumps(_payload(accepted, rejected)), json.dumps(_payload(corrected))]
    )
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings(api_key="key", model="model", base_url=None),
        client=client,
        max_attempts=2,
        mapping_schema_version=3,
    )
    result = mapper.map(profile, rubric, catalog)
    assert len(client.calls) == 2
    assert len(result.mappings) == 1
    assert result.mappings[0].criterion_id == accepted["criterion_id"]
    assert mapper.attempt_diagnostics[-1]["selected_attempt"] == 1
    assert "retained" in mapper.attempt_diagnostics[-1]["retry_selection_reason"]
