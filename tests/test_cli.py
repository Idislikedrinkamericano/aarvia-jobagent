import pytest

from aarvia import create_profile, save_profile
from aarvia.cli import MAX_NARRATIVE_FILE_BYTES, main
from aarvia.llm_client import LLMRequestError, LLMSettings
from aarvia.narrative_extraction import OpenAINarrativeExtractor
from aarvia.storage import load_profile

from test_confirmation import FakeExtractor
from test_interview import scripted_input
from test_llm_client import FakeBailianClient


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


class RawOutputExtractor(OpenAINarrativeExtractor):
    def __init__(self, output_text: str, *, api_key: str = "secret-api-key") -> None:
        class Responses:
            def create(self, **_kwargs):
                return type("Response", (), {"output_text": output_text})()

        client = type("Client", (), {"responses": Responses()})()
        super().__init__(
            settings=LLMSettings(api_key, "qwen-test", "https://workspace.example/v1"),
            client=client,
        )


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert "aarvia 0.4.7" in capsys.readouterr().out


def test_debug_mode_reports_raw_parsing_details(tmp_path) -> None:
    output = []
    raw = "```json\n{invalid}\n```"

    exit_code = main(
        ["discover", "--narrative", "--debug-extraction", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input(["My private background"]),
        output_fn=output.append,
        extractor=RawOutputExtractor(raw),
    )

    joined = "\n".join(output)
    assert exit_code == 1
    assert "may contain personal information" in joined
    assert "Aarvia version: 0.4.7" in joined
    assert "Provider model: qwen-test" in joined
    assert "Provider base URL host: workspace.example" in joined
    assert "Extraction protocol: responses" in joined
    assert "Structured output mode: json_schema" in joined
    assert "Extraction stage: raw parsing" in joined
    assert "JSON decode succeeded: no" in joined
    assert raw in joined


def test_debug_mode_reports_candidate_validation_path_and_preserves_profile(tmp_path) -> None:
    path = tmp_path / "profile.json"
    original = create_profile({"basic_profile": {"current_location": "Shanghai"}})
    save_profile(original, path)
    raw = '{"education":[{"unexpected":"value"}]}'
    output = []

    exit_code = main(
        ["discover", "--narrative", "--debug-extraction", "--profile", str(path)],
        input_fn=scripted_input(["My education"]),
        output_fn=output.append,
        extractor=RawOutputExtractor(raw),
    )

    joined = "\n".join(output)
    assert exit_code == 1
    assert "Extraction stage: candidate validation" in joined
    assert "JSON decode succeeded: yes" in joined
    assert "education[0] contains unknown fields: unexpected" in joined
    assert load_profile(path) == original


def test_normal_mode_hides_raw_provider_output(tmp_path) -> None:
    raw = "private-provider-output {invalid"
    output = []

    exit_code = main(
        ["discover", "--narrative", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input(["My background"]),
        output_fn=output.append,
        extractor=RawOutputExtractor(raw),
    )

    assert exit_code == 1
    assert raw not in "\n".join(output)


def test_debug_output_redacts_api_key(tmp_path) -> None:
    api_key = "secret-api-key"
    output = []

    main(
        ["discover", "--narrative", "--debug-extraction", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input(["My background"]),
        output_fn=output.append,
        extractor=RawOutputExtractor(f"provider echoed {api_key}", api_key=api_key),
    )

    joined = "\n".join(output)
    assert api_key not in joined
    assert "[REDACTED]" in joined


def test_debug_mode_reports_bailian_chat_completions_protocol(tmp_path) -> None:
    extractor = OpenAINarrativeExtractor(
        settings=LLMSettings(
            "provider-key",
            "qwen-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client=FakeBailianClient(result="not-json"),
    )
    output = []

    exit_code = main(
        ["discover", "--narrative", "--debug-extraction", "--profile", str(tmp_path / "profile.json")],
        input_fn=scripted_input(["My background"]),
        output_fn=output.append,
        extractor=extractor,
    )

    joined = "\n".join(output)
    assert exit_code == 1
    assert "Extraction protocol: chat_completions" in joined
    assert "Structured output mode: json_schema" in joined


def test_narrative_file_is_read_as_utf8_and_skips_narrative_prompt(tmp_path) -> None:
    narrative_path = tmp_path / "background.txt"
    narrative_path.write_text("I use Python.\n我也使用 SQL。", encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    extractor = FakeExtractor({"skills": [{"skill_name": "Python", "category": "language"}]})

    exit_code = main(
        [
            "discover",
            "--narrative-file",
            str(narrative_path),
            "--profile",
            str(profile_path),
        ],
        input_fn=scripted_input(["y"]),
        output_fn=lambda _message: None,
        extractor=extractor,
    )

    assert exit_code == 0
    assert extractor.calls == [("I use Python.\n我也使用 SQL。", None)]
    assert load_profile(profile_path).skills[0].skill_name == "Python"


@pytest.mark.parametrize("kind", ["missing", "empty", "too_large"])
def test_invalid_narrative_file_has_friendly_error(tmp_path, kind) -> None:
    path = tmp_path / "background.txt"
    if kind == "empty":
        path.write_text("  \n", encoding="utf-8")
    elif kind == "too_large":
        path.write_bytes(b"x" * (MAX_NARRATIVE_FILE_BYTES + 1))
    output = []

    exit_code = main(
        ["discover", "--narrative-file", str(path), "--profile", str(tmp_path / "profile.json")],
        output_fn=output.append,
    )

    assert exit_code == 1
    assert output[0].startswith("Error: Narrative file")


@pytest.mark.parametrize(
    "modes",
    [
        ["--manual", "--narrative"],
        ["--manual", "--narrative-file", "background.txt"],
        ["--narrative", "--narrative-file", "background.txt"],
    ],
)
def test_discovery_input_modes_are_mutually_exclusive(modes) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["discover", *modes])

    assert exit_info.value.code == 2
