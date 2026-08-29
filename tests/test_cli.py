import pytest

from aarvia.cli import main
from aarvia.llm_client import LLMRequestError
from aarvia.storage import load_profile

from test_confirmation import FakeExtractor
from test_interview import scripted_input


def test_discover_accepts_custom_profile_path(tmp_path) -> None:
    path = tmp_path / "custom.json"
    output = []

    exit_code = main(
        ["discover", "--profile", str(path)],
        input_fn=scripted_input([":quit"]),
        output_fn=output.append,
    )

    assert exit_code == 0
    assert load_profile(path).basic_profile.current_location is None
    assert output[-1] == f"Saved to: {path}"


def test_root_help_starts_without_interview(capsys) -> None:
    assert main([]) == 0
    assert "discover" in capsys.readouterr().out


def test_discover_help_lists_profile_option(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["discover", "--help"])

    assert exit_info.value.code == 0
    assert "--profile" in capsys.readouterr().out


def test_narrative_mode_uses_injected_extractor(tmp_path) -> None:
    path = tmp_path / "narrative.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    exit_code = main(
        ["discover", "--narrative", "--profile", str(path)],
        input_fn=scripted_input(["I use Python.", "y"]),
        output_fn=lambda _message: None,
        extractor=extractor,
    )

    assert exit_code == 0
    assert load_profile(path).skills[0].skill_name == "Python"


def test_missing_api_key_has_friendly_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AARVIA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AARVIA_LLM_MODEL", "test-model")
    output = []

    exit_code = main(
        ["discover", "--narrative", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input([]),
        output_fn=output.append,
    )

    assert exit_code == 1
    assert output == [
        "Error: LLM API key is not set. Set AARVIA_LLM_API_KEY or the legacy OPENAI_API_KEY."
    ]


def test_missing_model_has_friendly_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AARVIA_LLM_API_KEY", "test-key")
    monkeypatch.delenv("AARVIA_LLM_MODEL", raising=False)
    monkeypatch.delenv("AARVIA_OPENAI_MODEL", raising=False)
    output = []

    exit_code = main(
        ["discover", "--narrative", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input([]),
        output_fn=output.append,
    )

    assert exit_code == 1
    assert output == [
        "Error: LLM model is not set. Set AARVIA_LLM_MODEL or the legacy AARVIA_OPENAI_MODEL."
    ]


def test_llm_error_is_reported_without_traceback(tmp_path) -> None:
    path = tmp_path / "profile.json"
    output = []
    extractor = FakeExtractor(LLMRequestError("API unavailable"))

    exit_code = main(
        ["discover", "--narrative", "--profile", str(path)],
        input_fn=scripted_input(["My background"]),
        output_fn=output.append,
        extractor=extractor,
    )

    assert exit_code == 1
    assert output[-1] == "Error: API unavailable"
    assert not path.exists()
