from types import SimpleNamespace

import pytest

from aarvia.llm_client import (
    LLMConfigurationError,
    LLMRequestError,
    LLMSettings,
    create_openai_client,
)
from aarvia.narrative_extraction import OpenAINarrativeExtractor


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

    def create(self, **_kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=self.result)


class FakeClient:
    def __init__(self, result=None, error=None):
        self.responses = FakeResponses(result, error)


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
