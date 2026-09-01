from types import SimpleNamespace

import pytest

from aarvia.llm_client import (
    LLMConfigurationError,
    LLMRequestError,
    LLMSettings,
    create_openai_client,
    is_bailian_endpoint,
)
from aarvia.narrative_extraction import OpenAINarrativeExtractor, PROFILE_JSON_SCHEMA


ENVIRONMENT_KEYS = (
    "AARVIA_LLM_API_KEY",
    "AARVIA_LLM_BASE_URL",
    "AARVIA_LLM_MODEL",
    "OPENAI_API_KEY",
    "AARVIA_OPENAI_MODEL",
)


@pytest.fixture
def clean_llm_environment(monkeypatch):
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_generic_environment_variables_create_settings(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_API_KEY", "generic-key")
    clean_llm_environment.setenv("AARVIA_LLM_BASE_URL", "https://provider.example/v1")
    clean_llm_environment.setenv("AARVIA_LLM_MODEL", "provider-model")

    settings = LLMSettings.from_environment()

    assert settings == LLMSettings(
        api_key="generic-key",
        base_url="https://provider.example/v1",
        model="provider-model",
    )


def test_generic_variables_take_priority_over_legacy(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_API_KEY", "generic-key")
    clean_llm_environment.setenv("AARVIA_LLM_MODEL", "gpt-4o")
    clean_llm_environment.setenv("OPENAI_API_KEY", "legacy-key")
    clean_llm_environment.setenv("AARVIA_OPENAI_MODEL", "legacy-model")

    settings = LLMSettings.from_environment()

    assert settings.api_key == "generic-key"
    assert settings.model == "gpt-4o"
    assert settings.base_url is None


def test_legacy_openai_environment_variables_remain_supported(clean_llm_environment) -> None:
    clean_llm_environment.setenv("OPENAI_API_KEY", "legacy-key")
    clean_llm_environment.setenv("AARVIA_OPENAI_MODEL", "gpt-4o")

    settings = LLMSettings.from_environment()

    assert settings == LLMSettings(api_key="legacy-key", model="gpt-4o")


def test_custom_base_url_is_passed_to_openai_client() -> None:
    captured = {}

    def client_factory(**kwargs):
        captured.update(kwargs)
        return object()

    settings = LLMSettings("provider-key", "provider-model", "https://provider.example/v1")
    client = create_openai_client(settings, client_factory=client_factory)

    assert client is not None
    assert captured == {
        "api_key": "provider-key",
        "base_url": "https://provider.example/v1",
    }


def test_missing_api_key_fails(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_MODEL", "provider-model")

    with pytest.raises(LLMConfigurationError, match="LLM API key is not set"):
        LLMSettings.from_environment()


def test_missing_model_fails(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_API_KEY", "provider-key")

    with pytest.raises(LLMConfigurationError, match="LLM model is not set"):
        LLMSettings.from_environment()


def test_bailian_model_requires_base_url(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_API_KEY", "bailian-key")
    clean_llm_environment.setenv("AARVIA_LLM_MODEL", "qwen-plus")

    with pytest.raises(LLMConfigurationError, match="requires AARVIA_LLM_BASE_URL"):
        LLMSettings.from_environment()


def test_other_compatible_provider_requires_base_url(clean_llm_environment) -> None:
    clean_llm_environment.setenv("AARVIA_LLM_API_KEY", "provider-key")
    clean_llm_environment.setenv("AARVIA_LLM_MODEL", "provider-model")

    with pytest.raises(LLMConfigurationError, match="custom OpenAI-compatible provider"):
        LLMSettings.from_environment()


class FakeResponses:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=self.result)


class FakeClient:
    def __init__(self, result=None, error=None):
        self.responses = FakeResponses(result, error)


def test_correction_request_includes_evidence_current_topic_and_user_change() -> None:
    client = FakeClient(
        result='{"education":[{"institution":"UIUC","degree":"BS","field_of_study":"Economics","start_date":null,"expected_graduation_date":null,"gpa":"3.7"}]}'
    )
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=client,
    )
    current = [{
        "institution": "UIUC", "degree": "BS", "field_of_study": "Economics",
        "start_date": None, "expected_graduation_date": None, "gpa": None,
    }]

    result = extractor.extract_correction(
        "Set the UIUC GPA to 3.7.",
        topic="education",
        current_topic=current,
        original_narrative="I studied Economics at UIUC.",
    )

    assert result["education"][0]["gpa"] == "3.7"
    request = client.responses.calls[0]
    payload = request["input"]
    assert "I studied Economics at UIUC." in payload
    assert '"current_candidate_topic"' in payload
    assert "Set the UIUC GPA to 3.7." in payload
    assert "preserving every field" in request["instructions"]


def test_follow_up_request_contains_question_answer_profile_and_draft() -> None:
    client = FakeClient(
        result='{"basic_profile":{"name":null,"current_location":"Shanghai","current_status":"Student"}}'
    )
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=client,
    )

    result = extractor.extract_follow_up(
        "I am a student in Shanghai.",
        question="Where are you based and what are you doing?",
        topic="basic_profile",
        formal_profile={"education": [{"institution": "Example University"}]},
        session_draft={"skills": [{"skill_name": "Python"}]},
        field_path="basic_profile.current_location",
    )

    assert result["basic_profile"]["current_location"] == "Shanghai"
    request = client.responses.calls[0]
    assert "Where are you based" in request["input"]
    assert "I am a student in Shanghai." in request["input"]
    assert '"formal_profile"' in request["input"]
    assert '"session_draft"' in request["input"]
    assert '"selected_field_path": "basic_profile.current_location"' in request["input"]
    assert "Populate only that path inside basic_profile" in request["instructions"]
    assert "must not be rewritten" in request["instructions"]


def test_bailian_follow_up_uses_the_same_strict_candidate_schema() -> None:
    client = FakeBailianClient(
        result='{"constraints":{"target_locations":["United States","China"],'
        '"work_authorization_or_visa_constraints":null,'
        '"work_arrangement_preference":"flexible",'
        '"employment_type_preference":"internship",'
        '"target_start_date":"2027-06","other_constraints":[]}}'
    )
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings(
            "provider-key",
            "qwen-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client=client,
    )

    result = extractor.extract_follow_up(
        "Target locations are United States and China.",
        question="What constraints should I consider?",
        topic="constraints",
        formal_profile={},
        session_draft={},
    )

    assert result["constraints"]["target_locations"] == ["United States", "China"]
    request = client.chat_completions.calls[0]
    schema_config = request["response_format"]["json_schema"]
    assert schema_config["strict"] is True
    assert schema_config["schema"] is PROFILE_JSON_SCHEMA
    assert schema_config["schema"]["properties"]["constraints"]["additionalProperties"] is False


class FakeChatCompletions:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.result)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeBailianClient:
    def __init__(self, result=None, error=None):
        self.chat_completions = FakeChatCompletions(result, error)
        self.chat = SimpleNamespace(completions=self.chat_completions)


class AuthenticationError(Exception):
    status_code = 401


class APIConnectionError(Exception):
    pass


class ModelNotFoundError(Exception):
    status_code = 404


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            AuthenticationError("request used secret-key-value"),
            "LLM authentication failed. Check that the API key belongs to the same provider region as the endpoint.",
        ),
        (
            APIConnectionError("https://private-endpoint.example failed"),
            "Could not connect to the LLM endpoint. Check AARVIA_LLM_BASE_URL and network connectivity.",
        ),
        (
            ModelNotFoundError("model unavailable"),
            "The configured LLM model was not found or is not enabled for this provider region.",
        ),
    ],
)
def test_provider_errors_are_mapped_without_sensitive_details(error, expected) -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("secret-key-value", "provider-model", "https://provider.example/v1"),
        client=FakeClient(error=error),
    )

    with pytest.raises(LLMRequestError) as error_info:
        extractor.extract("My background")

    assert str(error_info.value) == expected
    assert "secret-key-value" not in str(error_info.value)
    assert "private-endpoint.example" not in str(error_info.value)


def test_non_json_provider_output_has_schema_error() -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=FakeClient(result="not-json"),
    )

    with pytest.raises(LLMRequestError, match="does not match the required candidate schema"):
        extractor.extract("My background")


def test_non_object_provider_output_has_schema_error() -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=FakeClient(result="[]"),
    )

    with pytest.raises(LLMRequestError, match="does not match the required candidate schema"):
        extractor.extract("My background")
    assert extractor.diagnostics.json_decode_succeeded is True
    assert extractor.diagnostics.detail == "candidate top level must be a JSON object"


def test_plain_json_provider_output_is_parsed() -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=FakeClient(result='  {"skills": []}  '),
    )

    assert extractor.extract("My background") == {}
    assert extractor.diagnostics.json_decode_succeeded is True


def test_complete_json_code_fence_is_parsed() -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=FakeClient(result='```json\n{"skills": []}\n```'),
    )

    assert extractor.extract("My background") == {}


@pytest.mark.parametrize(
    "output",
    [
        'Here is the result: {"skills": []}',
        '{"skills": []}\nThis is the result.',
        '```json\n{"skills": []}\n```\nExtra explanation.',
        '{"skills": [}',
    ],
)
def test_mixed_or_invalid_provider_output_is_rejected(output) -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=FakeClient(result=output),
    )

    with pytest.raises(LLMRequestError, match="does not match the required candidate schema"):
        extractor.extract("My background")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", True),
        ("https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", True),
        ("https://maas.aliyuncs.com.example/v1", False),
        ("https://provider.example/v1", False),
        (None, False),
    ],
)
def test_bailian_endpoint_detection_uses_parsed_host(url, expected) -> None:
    assert is_bailian_endpoint(url) is expected


def test_bailian_uses_chat_completions_strict_schema_and_disables_thinking() -> None:
    client = FakeBailianClient(result='{"skills": []}')
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings(
            "provider-key",
            "qwen-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client=client,
    )

    assert extractor.extract("My background") == {}
    request = client.chat_completions.calls[0]
    assert request["messages"][0]["role"] == "system"
    assert "strictly follows" in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "My background"}
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "career_profile_candidate",
            "strict": True,
            "schema": PROFILE_JSON_SCHEMA,
        },
    }
    assert request["response_format"]["json_schema"]["schema"] is PROFILE_JSON_SCHEMA
    assert request["extra_body"] == {"enable_thinking": False}
    assert extractor.diagnostics.protocol == "chat_completions"


def test_non_bailian_endpoint_uses_responses_without_thinking_parameter() -> None:
    client = FakeClient(result='{"skills": []}')
    captured = []
    client.responses.create = lambda **kwargs: (
        captured.append(kwargs) or SimpleNamespace(output_text='{"skills": []}')
    )
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings("provider-key", "provider-model", "https://provider.example/v1"),
        client=client,
    )

    extractor.extract("My background")

    assert "extra_body" not in captured[0]
    assert extractor.diagnostics.protocol == "responses"


@pytest.mark.parametrize("result", [None, "", "not-json"])
def test_bailian_empty_or_non_json_content_is_rejected(result) -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings(
            "provider-key",
            "qwen-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client=FakeBailianClient(result=result),
    )

    with pytest.raises(LLMRequestError, match="does not match the required candidate schema"):
        extractor.extract("My background")
