"""OpenAI-compatible provider configuration for narrative extraction."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable
from urllib.parse import urlparse


class LLMConfigurationError(RuntimeError):
    """Raised when required LLM configuration is missing."""


class LLMRequestError(RuntimeError):
    """Raised when an LLM request cannot produce usable output."""


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_environment(cls) -> LLMSettings:
        api_key = (os.getenv("AARVIA_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        base_url = (os.getenv("AARVIA_LLM_BASE_URL") or "").strip() or None
        model = (os.getenv("AARVIA_LLM_MODEL") or os.getenv("AARVIA_OPENAI_MODEL") or "").strip()
        if not api_key:
            raise LLMConfigurationError(
                "LLM API key is not set. Set AARVIA_LLM_API_KEY or the legacy OPENAI_API_KEY."
            )
        if not model:
            raise LLMConfigurationError(
                "LLM model is not set. Set AARVIA_LLM_MODEL or the legacy AARVIA_OPENAI_MODEL."
            )
        if base_url is None and not _uses_default_openai_endpoint(model):
            provider = "Bailian" if _looks_like_bailian_model(model) else "A custom OpenAI-compatible provider"
            raise LLMConfigurationError(
                f"{provider} configuration requires AARVIA_LLM_BASE_URL for the selected model."
            )
        return cls(api_key=api_key, model=model, base_url=base_url)


def _looks_like_bailian_model(model: str) -> bool:
    return model.casefold().startswith(("qwen", "qwq"))


def _uses_default_openai_endpoint(model: str) -> bool:
    return model.casefold().startswith(("gpt-", "chatgpt-", "o1", "o3", "o4", "ft:"))


def is_bailian_endpoint(base_url: str | None) -> bool:
    """Identify Alibaba Cloud endpoints by parsed host, not model name or URL presence."""
    if not base_url:
        return False
    host = (urlparse(base_url).hostname or "").casefold()
    return host == "dashscope.aliyuncs.com" or host.endswith(".maas.aliyuncs.com")


def create_openai_client(
    settings: LLMSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise LLMConfigurationError(
                "The openai package is not installed. Install the project dependencies before using --narrative."
            ) from error
        client_factory = OpenAI
    return client_factory(api_key=settings.api_key, base_url=settings.base_url)


def safe_llm_error(error: Exception) -> LLMRequestError:
    """Map provider errors to stable messages without exposing request secrets."""
    error_name = type(error).__name__.casefold()
    status_code = getattr(error, "status_code", None)
    error_code = str(getattr(error, "code", "") or "").casefold()
    message = str(error).casefold()

    if status_code in {401, 403} or "authentication" in error_name:
        return LLMRequestError(
            "LLM authentication failed. Check that the API key belongs to the same provider region as the endpoint."
        )
    if "connection" in error_name or "timeout" in error_name:
        return LLMRequestError(
            "Could not connect to the LLM endpoint. Check AARVIA_LLM_BASE_URL and network connectivity."
        )
    if status_code == 404 or error_code in {"model_not_found", "modelnotfound"}:
        return LLMRequestError(
            "The configured LLM model was not found or is not enabled for this provider region."
        )
    if status_code == 400 and ("model" in error_code or "model" in message):
        return LLMRequestError(
            "The configured LLM model is invalid or does not support this structured-output request."
        )
    return LLMRequestError("The LLM provider could not complete the extraction request.")


# Compatibility for code that imported the Phase 1B-v2.1 name.
OpenAISettings = LLMSettings
