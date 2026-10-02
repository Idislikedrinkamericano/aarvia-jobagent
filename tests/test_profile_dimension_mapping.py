from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import production_capability_rubric
from aarvia.career_direction import ProfileReference
from aarvia.llm_client import LLMRequestError, LLMSettings
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    MappingProviderOutputError,
    OpenAIProfileDimensionMapper,
    ProviderCapabilities,
    ProfileDimensionMappingCandidateSet,
    allowed_profile_reference_paths,
    canonical_evidence_span_inventory,
    load_mapping_candidates,
    parse_mapping_provider_output,
    provider_capabilities,
    provider_mapping_schema,
    save_mapping_candidates,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from aarvia.role_recommendation import build_role_recommendation
from recommendation_fixtures import (
    clone_payload,
    mapping_payload,
    mapping_set,
    mapping_transport_payload,
    synthetic_profile,
)


def parse_payload(payload: dict) -> ProfileDimensionMappingCandidateSet:
    return ProfileDimensionMappingCandidateSet.from_provider_payload(
        payload,
        profile=synthetic_profile(),
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
    )


def test_mapping_candidates_capture_exact_profile_references_and_fingerprint() -> None:
    value = mapping_set()
    reference = value.mappings[0].profile_fact_references[0]
    reference.validate(synthetic_profile())
    assert reference.profile_fingerprint == value.profile_fingerprint
    assert value.mappings[0].mapping_id.startswith("mapping_")


def test_allowed_reference_inventory_is_schema_valid_and_deterministic() -> None:
    profile = synthetic_profile()
    first = allowed_profile_reference_paths(profile)
    second = allowed_profile_reference_paths(profile)
    assert first == second == tuple(sorted(first))
    assert "education[0].field_of_study" in first
    assert "skills[0].skill_name" in first
    assert "experience_overview[0].short_factual_summary" in first
    assert "career_preferences.currently_considered_roles[0]" in first
    assert not any(path.startswith("career_profile.") for path in first)
    assert not any(path.startswith("open_questions") for path in first)
    for path in first:
        ProfileReference.capture(profile, path).validate(profile)


def test_single_transport_prefix_is_canonicalized_in_mapping_and_recommendation() -> None:
    profile=synthetic_profile();rubric=production_capability_rubric();catalog=production_role_catalog()
    payload=clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["path"] = (
        "career_profile.education[0].field_of_study"
    )
    payload["mappings"][0]["profile_fact_references"][0]["value_snapshot"] = "Computing"
    payload["mappings"][0]["profile_fact_references"][0]["exact_excerpt"] = "Computing"
    candidates=ProfileDimensionMappingCandidateSet.from_provider_payload(
        payload,profile=profile,rubric=rubric,catalog=catalog,
        provider_name="fixture",provider_model="fixture-model",
    )
    assert candidates.mappings[0].profile_fact_references[0].path == "education[0].field_of_study"
    assert "career_profile." not in json.dumps(candidates.to_dict())
    recommendation=build_role_recommendation(
        profile=profile,mapping_candidates=candidates,rubric=rubric,catalog=catalog,
        created_at="2026-09-19T12:00:00+00:00",
    )
    assert "career_profile." not in json.dumps(recommendation.to_dict())


@pytest.mark.parametrize(
    "path",
    [
        "career_profile",
        "career_profile.",
        "career_profile.career_profile.education[0].field_of_study",
        "profile.education[0].field_of_study",
        "payload.education[0].field_of_study",
        "education[99].field_of_study",
        "education[0].invented_field",
    ],
)
def test_transport_prefix_canonicalization_rejects_unsafe_paths(path) -> None:
    payload=clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["path"]=path
    with pytest.raises(Phase2ValidationError):
        parse_payload(payload)


def test_prefixed_reference_still_rejects_value_and_fingerprint_mismatches() -> None:
    payload=clone_payload()
    reference=payload["mappings"][0]["profile_fact_references"][0]
    reference["path"]="career_profile."+reference["path"]
    reference["value_snapshot"]="Invented value"
    with pytest.raises(Phase2ValidationError,match="does not match"):
        parse_payload(payload)

    payload=clone_payload();payload["mappings"][0]["profile_fact_references"][0]["path"]="career_profile."+payload["mappings"][0]["profile_fact_references"][0]["path"]
    candidates=parse_payload(payload)
    changed=synthetic_profile();changed.skills[0].skill_name="Changed Skill"
    with pytest.raises(Phase2ValidationError,match="fingerprint"):
        candidates.validate(profile=changed,rubric=production_capability_rubric(),catalog=production_role_catalog())


def test_prefixed_reference_cannot_bypass_duplicate_validation() -> None:
    payload = clone_payload()
    reference = deepcopy(payload["mappings"][0]["profile_fact_references"][0])
    reference["path"] = "career_profile." + reference["path"]
    payload["mappings"][0]["profile_fact_references"].append(reference)

    with pytest.raises(Phase2ValidationError, match="duplicate atomic evidence"):
        parse_payload(payload)


def test_phase_one_profile_reference_does_not_accept_transport_alias() -> None:
    with pytest.raises(Phase2ValidationError,match="unsupported path"):
        ProfileReference.capture(synthetic_profile(),"career_profile.education[0].field_of_study")


@pytest.mark.parametrize("field", ["mapping_id", "profile_fingerprint", "score", "rank", "reviewer_reference"])
def test_provider_cannot_inject_local_or_final_fields(field) -> None:
    payload = clone_payload()
    payload["mappings"][0][field] = "injected"
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        parse_payload(payload)


def test_provider_rejects_nonexistent_path_and_mismatched_snapshot() -> None:
    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["path"] = "skills[999].skill_name"
    with pytest.raises(Phase2ValidationError, match="does not exist"):
        parse_payload(payload)

    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"][0]["value_snapshot"] = "Hallucinated Skill"
    with pytest.raises(Phase2ValidationError, match="does not match"):
        parse_payload(payload)


def test_mapping_rejects_invalid_role_or_dimension() -> None:
    payload = clone_payload()
    payload["mappings"][0]["role_id"] = "research_engineer"
    with pytest.raises(Phase2ValidationError, match="another Role"):
        parse_payload(payload)
    payload = clone_payload()
    payload["mappings"][0]["dimension_id"] = "dimension_missing"
    with pytest.raises(Phase2ValidationError, match="unknown capability"):
        parse_payload(payload)


def test_mapping_rejects_duplicate_fact_dimension_and_multiple_primary_use() -> None:
    payload = clone_payload()
    payload["mappings"].append(deepcopy(payload["mappings"][0]))
    with pytest.raises(Phase2ValidationError, match="duplicate mapping IDs"):
        parse_payload(payload)

    payload = clone_payload(all_demonstrated=True)
    first, second = payload["mappings"][0], payload["mappings"][1]
    second["role_id"] = first["role_id"]
    second["profile_fact_references"] = deepcopy(first["profile_fact_references"])
    with pytest.raises(Phase2ValidationError, match="cannot be primary"):
        parse_payload(payload)


def test_secondary_mapping_cannot_claim_strong_evidence() -> None:
    payload = clone_payload()
    payload["mappings"][0]["relationship"] = "secondary"
    with pytest.raises(Phase2ValidationError, match="secondary mapping"):
        parse_payload(payload)


def test_interest_cannot_support_current_fit() -> None:
    payload = clone_payload()
    payload["mappings"][0]["inference_type"] = "interest_only_directional"
    with pytest.raises(Phase2ValidationError, match="interest cannot"):
        parse_payload(payload)


def test_unknown_and_not_demonstrated_remain_distinct() -> None:
    payload = clone_payload()
    payload["mappings"][0]["match_status"] = "unknown"
    payload["mappings"][0]["profile_fact_references"] = []
    unknown = parse_payload(payload)
    payload["mappings"][0]["match_status"] = "not_demonstrated"
    missing = parse_payload(payload)
    assert unknown.mappings[0].match_status.value == "unknown"
    assert missing.mappings[0].match_status.value == "not_demonstrated"


def test_mapping_typed_round_trip_and_tampering(tmp_path) -> None:
    profile = synthetic_profile(); rubric = production_capability_rubric(); catalog = production_role_catalog()
    value = mapping_set()
    path = save_mapping_candidates(value, tmp_path / "mapping.json", profile=profile, rubric=rubric, catalog=catalog)
    assert load_mapping_candidates(path, profile=profile, rubric=rubric, catalog=catalog) == value
    data = value.to_dict(); data["mappings"][0]["mapping_id"] = "mapping_tampered"
    path.write_text(__import__("json").dumps(data))
    with pytest.raises(Phase2ValidationError, match="deterministic"):
        load_mapping_candidates(path, profile=profile, rubric=rubric, catalog=catalog)


class FakeResponses:
    def __init__(self, outputs): self.outputs = list(outputs); self.calls = []
    def create(self, **kwargs): self.calls.append(kwargs); return SimpleNamespace(output_text=self.outputs.pop(0))


class FakeChatCompletions:
    def __init__(self, *outputs): self.outputs = list(outputs); self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=output))])


def test_openai_compatible_mapper_uses_structured_response_and_retries() -> None:
    responses = FakeResponses(["not json", json.dumps(mapping_transport_payload())])
    client = SimpleNamespace(responses=responses)
    mapper = OpenAIProfileDimensionMapper(settings=LLMSettings("secret", "gpt-test"), client=client)
    result = mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(responses.calls) == 2
    assert responses.calls[0]["text"]["format"]["strict"] is True
    assert result.provider_model == "gpt-test"


def test_provider_schema_constrains_every_role_id_to_current_rubric() -> None:
    rubric = production_capability_rubric()
    schema = provider_mapping_schema(rubric, synthetic_profile())
    expected = list(rubric.supported_role_ids)

    for collection in ("mappings", "directional_signals", "constraints"):
        role_schema = schema["properties"][collection]["items"]["properties"]["role_id"]
        assert role_schema == {"type": "string", "enum": expected}
    for collection in ("mappings", "directional_signals", "constraints"):
        reference_schema = schema["properties"][collection]["items"]["properties"][
            "profile_fact_references"
        ]["items"]
        assert set(reference_schema["properties"]) == {"span_id"}
        assert reference_schema["required"] == ["span_id"]
        assert reference_schema["properties"]["span_id"]["enum"] == [
            item.span_id
            for item in canonical_evidence_span_inventory(synthetic_profile())
        ]


def test_current_fit_provider_schema_exposes_only_legal_inference_types() -> None:
    schema = provider_mapping_schema(
        production_capability_rubric(), synthetic_profile()
    )
    inference_schema = schema["properties"]["mappings"]["items"]["properties"][
        "inference_type"
    ]

    assert inference_schema == {
        "type": "string",
        "enum": ["direct", "bounded_semantic", "adjacent_transfer"],
    }
    assert "interest_only_directional" not in json.dumps(
        schema, sort_keys=True
    )


@pytest.mark.parametrize(
    ("inference_type", "review_required"),
    [
        ("direct", False),
        ("direct", True),
        ("bounded_semantic", True),
        ("adjacent_transfer", True),
    ],
)
def test_current_fit_inference_review_legal_combinations_remain_valid(
    inference_type: str, review_required: bool
) -> None:
    payload = clone_payload()
    payload["mappings"][0].update(
        inference_type=inference_type,
        review_required=review_required,
    )

    result = parse_payload(payload)

    assert result.mappings[0].inference_type.value == inference_type
    assert result.mappings[0].review_required is review_required


@pytest.mark.parametrize("inference_type", ["bounded_semantic", "adjacent_transfer"])
def test_semantic_current_fit_inference_requires_review(inference_type: str) -> None:
    payload = clone_payload()
    payload["mappings"][0].update(
        inference_type=inference_type,
        review_required=False,
    )

    with pytest.raises(Phase2ValidationError, match="requires review"):
        parse_payload(payload)


def test_openai_and_bailian_receive_the_same_canonical_span_inventory() -> None:
    profile = synthetic_profile()
    encoded = json.dumps(mapping_transport_payload(profile=profile))
    responses = FakeResponses([encoded])
    openai_mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )
    openai_mapper.map(
        profile, production_capability_rubric(), production_role_catalog()
    )

    chat = FakeChatCompletions(encoded)
    bailian_mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings(
            "secret",
            "qwen-test",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    bailian_mapper.map(
        profile, production_capability_rubric(), production_role_catalog()
    )

    openai_payload = json.loads(responses.calls[0]["input"])
    bailian_payload = json.loads(chat.calls[0]["messages"][1]["content"])
    expected = [
        item.to_transport_dict()
        for item in canonical_evidence_span_inventory(profile)
    ]
    assert openai_payload["allowed_evidence_spans"] == expected
    assert bailian_payload["allowed_evidence_spans"] == expected
    openai_instructions = responses.calls[0]["instructions"]
    bailian_instructions = chat.calls[0]["messages"][0]["content"]
    rule = (
        "direct with review_required false or true; bounded_semantic with "
        "review_required true; adjacent_transfer with review_required true"
    )
    assert rule in openai_instructions
    assert rule in bailian_instructions
    assert '"enum":["direct","bounded_semantic","adjacent_transfer"]' in (
        openai_instructions
    )
    assert '"enum":["direct","bounded_semantic","adjacent_transfer"]' in (
        bailian_instructions
    )


def test_provider_request_distinguishes_free_text_roles_from_canonical_ids() -> None:
    profile_data = synthetic_profile().to_dict()
    profile_data["career_preferences"]["currently_considered_roles"] = [
        "AI Agent Engineer",
        "AI/ML Software Engineer internship",
    ]
    profile = CareerProfile.from_dict(profile_data)
    payload = mapping_payload()
    for signal in payload["directional_signals"]:
        for reference in signal["profile_fact_references"]:
            if reference["path"] == "career_preferences.currently_considered_roles[0]":
                reference["value_snapshot"] = "AI Agent Engineer"
    responses = FakeResponses([
        json.dumps(mapping_transport_payload(payload, profile=profile))
    ])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )

    mapper.map(profile, production_capability_rubric(), production_role_catalog())

    request = responses.calls[0]
    sent = json.loads(request["input"])
    allowed = list(production_capability_rubric().supported_role_ids)
    assert sent["career_profile"]["career_preferences"]["currently_considered_roles"] == [
        "AI Agent Engineer",
        "AI/ML Software Engineer internship",
    ]
    assert sent["allowed_canonical_role_ids"] == allowed
    assert "user-entered job names and preferences, not canonical role IDs" in request["instructions"]
    assert request["text"]["format"]["schema"] == provider_mapping_schema(
        production_capability_rubric(), profile
    )


def test_mapper_accepts_one_complete_json_fence() -> None:
    raw = "```json\n" + json.dumps(mapping_transport_payload()) + "\n```"
    mapper = OpenAIProfileDimensionMapper(settings=LLMSettings("secret", "gpt-test"), client=SimpleNamespace(responses=FakeResponses([raw])))
    assert mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog()).mappings


def test_bailian_mapper_uses_chat_completions_and_disables_thinking() -> None:
    chat = FakeChatCompletions(json.dumps(mapping_transport_payload()))
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "qwen-test", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        client=client,
    )
    mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert chat.calls[0]["response_format"] == {"type": "json_object"}
    assert chat.calls[0]["extra_body"] == {"enable_thinking": False}
    instructions = chat.calls[0]["messages"][0]["content"]
    for role_id in production_capability_rubric().supported_role_ids:
        assert role_id in instructions
    assert '"role_id":{"type":"string","enum":' in instructions


def test_provider_capabilities_route_official_bailian_and_custom_endpoints() -> None:
    assert provider_capabilities(LLMSettings("key", "gpt-test")).protocol == "responses"
    bailian = provider_capabilities(
        LLMSettings("key", "qwen-test", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    )
    assert bailian == ProviderCapabilities("chat_completions", "json_object", True, True)
    custom = provider_capabilities(
        LLMSettings("key", "custom-model", "https://provider.example.test/v1")
    )
    assert custom == ProviderCapabilities("chat_completions", "json_object", True, False)


@pytest.mark.parametrize(
    "raw",
    [
        "{unquoted: 1}",
        '{"mappings": [],}',
        '{"mappings": [',
    ],
)
def test_mapping_parser_rejects_malformed_qwen_style_json(raw) -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_mapping_provider_output(raw)


def test_mapping_parser_accepts_plain_fenced_and_uniquely_wrapped_json() -> None:
    encoded = json.dumps(mapping_payload())
    assert parse_mapping_provider_output(encoded) == mapping_payload()
    assert parse_mapping_provider_output(f"```json\n{encoded}\n```") == mapping_payload()
    assert parse_mapping_provider_output(f"Here is the object:\n{encoded}\nEnd.") == mapping_payload()


def test_mapping_parser_rejects_two_objects_empty_and_non_object_content() -> None:
    with pytest.raises(MappingProviderOutputError, match="multiple"):
        parse_mapping_provider_output("{}\n{}")
    with pytest.raises(MappingProviderOutputError, match="empty"):
        parse_mapping_provider_output("   ")
    with pytest.raises(MappingProviderOutputError, match="top level"):
        parse_mapping_provider_output("[]")


def test_mapper_reports_malformed_output_after_retry() -> None:
    mapper = OpenAIProfileDimensionMapper(settings=LLMSettings("secret", "gpt-test"), client=SimpleNamespace(responses=FakeResponses(["bad", "still bad"])))
    with pytest.raises(LLMRequestError, match="after 2 attempts"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())


def test_mapper_reports_provider_unavailable_without_exposing_credentials() -> None:
    class UnavailableResponses:
        def create(self, **kwargs):
            raise ConnectionError("request failed with secret-api-key")

    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret-api-key", "gpt-test"),
        client=SimpleNamespace(responses=UnavailableResponses()),
    )

    with pytest.raises(LLMRequestError) as captured:
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())

    assert "Could not connect" in str(captured.value)
    assert "secret-api-key" not in str(captured.value)


def test_retry_keeps_original_task_and_adds_parse_repair_context() -> None:
    responses = FakeResponses(["{unquoted: true}", json.dumps(mapping_transport_payload())])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )
    mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())

    assert len(responses.calls) == 2
    assert responses.calls[0]["input"] == responses.calls[1]["input"]
    assert "previous attempt failed" in responses.calls[1]["instructions"]
    assert "JSONDecodeError" in responses.calls[1]["instructions"]
    assert "exact allowed JSON Schema" in responses.calls[1]["instructions"]
    assert "select only a span_id from allowed_evidence_spans" in responses.calls[1]["instructions"]
    assert "Do not output score, rank, Decision, Gap" in responses.calls[1]["instructions"]
    request=json.loads(responses.calls[0]["input"])
    assert request["allowed_evidence_spans"] == [
        item.to_transport_dict()
        for item in canonical_evidence_span_inventory(synthetic_profile())
    ]


def test_provider_path_injection_retry_requires_canonical_span_selection() -> None:
    invalid=mapping_transport_payload()
    invalid["mappings"][0]["profile_fact_references"][0] = {
        "path": "career_profile.skills[0].skill_name",
        "value_snapshot": "Synthetic Skill 1",
        "exact_excerpt": "Synthetic Skill 1",
    }
    responses=FakeResponses([
        json.dumps(invalid), json.dumps(mapping_transport_payload())
    ])
    mapper=OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret","gpt-test"),client=SimpleNamespace(responses=responses)
    )
    mapper.map(synthetic_profile(),production_capability_rubric(),production_role_catalog())
    repair=responses.calls[1]["instructions"]
    assert "provider_field_injection" in repair
    assert "select only a span_id from allowed_evidence_spans" in repair
    assert "Do not output a path, value_snapshot, excerpt, offset, fingerprint" in repair


def test_retry_revalidates_forbidden_fields_and_typed_contract() -> None:
    injected = clone_payload(); injected["rank"] = 1
    second_injected = clone_payload(); second_injected["score"] = 99
    responses = FakeResponses([
        json.dumps(mapping_transport_payload(injected)),
        json.dumps(mapping_transport_payload(second_injected)),
    ])
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )
    with pytest.raises(LLMRequestError, match="after 2 attempts"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(responses.calls) == 2


class ResponseFormatRejected(Exception):
    status_code = 400


def test_chat_json_mode_has_explicit_prompt_only_fallback() -> None:
    chat = FakeChatCompletions(
        ResponseFormatRejected("response_format json_object is unsupported"),
        json.dumps(mapping_transport_payload()),
    )
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert chat.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in chat.calls[1]
    assert chat.calls[0]["messages"] == chat.calls[1]["messages"]
    assert mapper.attempt_diagnostics[0]["fallback_reason"] == "provider_rejected_json_object_mode"
    assert mapper.attempt_diagnostics[0]["response_format_mode"] == "prompt_only"


def test_failed_prompt_only_fallback_retains_structured_reason() -> None:
    chat = FakeChatCompletions(
        ResponseFormatRejected("response_format json_object is unsupported"),
        ConnectionError("fallback endpoint unavailable"),
    )
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    with pytest.raises(LLMRequestError, match="Could not connect"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert mapper.attempt_diagnostics == [{
        "attempt": 1,
        "provider_path": "chat_completions",
        "model": "custom",
        "response_format_mode": "prompt_only",
        "parser_error": "Could not connect to the LLM endpoint. Check AARVIA_LLM_BASE_URL and network connectivity.",
        "response_sha256": None,
        "response_length": 0,
        "fallback_reason": "provider_rejected_json_object_mode",
        "raw_response_saved": False,
        "rejected_evidence_group_count": 0,
        "unresolved_candidate_count": 0,
        "unresolved_evidence_group_count": 0,
    }]


def test_authentication_error_does_not_trigger_prompt_only_fallback() -> None:
    error = ResponseFormatRejected("authentication failed")
    error.status_code = 401
    chat = FakeChatCompletions(error, json.dumps(mapping_transport_payload()))
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    with pytest.raises(LLMRequestError, match="authentication"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(chat.calls) == 1


def test_quota_error_is_not_misreported_as_json_or_fallback() -> None:
    error = ResponseFormatRejected("quota exceeded")
    error.status_code = 429
    chat = FakeChatCompletions(error, json.dumps(mapping_transport_payload()))
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    with pytest.raises(LLMRequestError, match="could not complete"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(chat.calls) == 1
    assert mapper.attempt_diagnostics[0]["parser_error"] == "The LLM provider could not complete the extraction request."


def test_multiple_content_blocks_are_rejected_and_bounded_retry_applies() -> None:
    blocks = [{"text": "{}"}, {"text": "{}"}]
    chat = FakeChatCompletions(blocks, blocks)
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )
    with pytest.raises(LLMRequestError, match="after 2 attempts"):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(chat.calls) == 2


def test_opt_in_diagnostics_are_metadata_only_and_redacted(tmp_path) -> None:
    diagnostics = tmp_path / "diagnostics"
    chat = FakeChatCompletions(
        ResponseFormatRejected("response_format rejected for secret-api-key"),
        "{unquoted: secret-api-key}",
        json.dumps(mapping_transport_payload()),
    )
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret-api-key", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
        diagnostics_dir=diagnostics,
    )
    mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())

    files = sorted(diagnostics.glob("*.json"))
    assert len(files) == 2
    first, second = [json.loads(path.read_text()) for path in files]
    assert first["attempt"] == 1 and first["response_format_mode"] == "prompt_only"
    assert first["fallback_reason"] == "provider_rejected_json_object_mode"
    assert first["response_sha256"] and first["response_length"] > 0
    assert second["attempt"] == 2 and second["parser_error"] is None
    assert second["response_format_mode"] == "prompt_only"
    assert "response_format" not in chat.calls[2]
    assert all(item["raw_response_saved"] is False for item in (first, second))
    combined = "".join(path.read_text() for path in files)
    assert "secret-api-key" not in combined
    assert "unquoted" not in combined
    assert not (diagnostics.stat().st_mode & 0o077)
    assert all(not (path.stat().st_mode & 0o077) for path in files)


def test_diagnostics_are_not_created_without_explicit_opt_in(tmp_path) -> None:
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=FakeResponses(["bad", "still bad"])),
    )
    with pytest.raises(LLMRequestError):
        mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert list(tmp_path.iterdir()) == []
