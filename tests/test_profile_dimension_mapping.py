from copy import deepcopy
from types import SimpleNamespace

import pytest

from aarvia.capability_rubric import production_capability_rubric
from aarvia.llm_client import LLMRequestError, LLMSettings
from aarvia.profile_dimension_mapping import (
    OpenAIProfileDimensionMapper,
    ProfileDimensionMappingCandidateSet,
    load_mapping_candidates,
    save_mapping_candidates,
)
from aarvia.role_catalog import Phase2ValidationError, production_role_catalog
from recommendation_fixtures import clone_payload, mapping_payload, mapping_set, synthetic_profile


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
    def __init__(self, output): self.output = output; self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.output))])


def test_openai_compatible_mapper_uses_structured_response_and_retries() -> None:
    responses = FakeResponses(["not json", __import__("json").dumps(mapping_payload())])
    client = SimpleNamespace(responses=responses)
    mapper = OpenAIProfileDimensionMapper(settings=LLMSettings("secret", "gpt-test"), client=client)
    result = mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert len(responses.calls) == 2
    assert responses.calls[0]["text"]["format"]["strict"] is True
    assert result.provider_model == "gpt-test"


def test_mapper_accepts_one_complete_json_fence() -> None:
    raw = "```json\n" + __import__("json").dumps(mapping_payload()) + "\n```"
    mapper = OpenAIProfileDimensionMapper(settings=LLMSettings("secret", "gpt-test"), client=SimpleNamespace(responses=FakeResponses([raw])))
    assert mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog()).mappings


def test_bailian_mapper_uses_chat_completions_and_disables_thinking() -> None:
    chat = FakeChatCompletions(__import__("json").dumps(mapping_payload()))
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    mapper = OpenAIProfileDimensionMapper(
        settings=LLMSettings("secret", "qwen-test", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        client=client,
    )
    mapper.map(synthetic_profile(), production_capability_rubric(), production_role_catalog())
    assert chat.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert chat.calls[0]["extra_body"] == {"enable_thinking": False}


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
