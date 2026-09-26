import json
import hashlib

import pytest

from aarvia.cli import main
from aarvia.llm_client import LLMRequestError
from aarvia.profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    save_mapping_candidates,
)
from aarvia.storage import save_profile
from aarvia.capability_rubric import production_capability_rubric
from aarvia.role_catalog import production_role_catalog
from recommendation_fixtures import clone_payload, mapping_set, synthetic_profile


def setup_inputs(tmp_path):
    profile = synthetic_profile()
    profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping_path = save_mapping_candidates(
        mapping_set(),
        tmp_path / "mapping.json",
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
    )
    return profile_path, mapping_path


def test_recommend_help(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["recommend", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--mapping-candidates" in output
    assert "--overwrite" in output
    assert "--provider-diagnostics-dir" in output


def test_recommend_offline_writes_artifact_without_mutating_profile(tmp_path) -> None:
    profile_path, mapping_path = setup_inputs(tmp_path)
    before = profile_path.read_bytes()
    output_path = tmp_path / "recommendation.json"
    messages = []
    assert main(["recommend", "--profile", str(profile_path), "--mapping-candidates", str(mapping_path), "--output", str(output_path)], output_fn=messages.append) == 0
    assert profile_path.read_bytes() == before
    data = json.loads(output_path.read_text())
    assert data["schema_version"] == 3
    assert len(data["role_results"]) == 3
    assert "decision" not in data
    assert any("Current Fit" in line for line in messages)
    assert messages[-1] == "No career direction was selected. User Decision remains separate."


def test_recommend_refuses_existing_output_without_overwrite(tmp_path) -> None:
    profile_path, mapping_path = setup_inputs(tmp_path)
    output_path = tmp_path / "recommendation.json"
    output_path.write_text("keep")
    messages = []
    assert main(["recommend", "--profile", str(profile_path), "--mapping-candidates", str(mapping_path), "--output", str(output_path)], output_fn=messages.append) == 1
    assert output_path.read_text() == "keep"
    assert "--overwrite" in messages[0]


class FakeMapper:
    def __init__(self): self.called = False
    def map(self, profile, rubric, catalog):
        self.called = True
        return mapping_set()


def test_recommend_supports_injected_mock_provider(tmp_path) -> None:
    profile = synthetic_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapper = FakeMapper(); output_path = tmp_path / "recommendation.json"
    assert main(["recommend", "--profile", str(profile_path), "--output", str(output_path)], mapper=mapper, output_fn=lambda _: None) == 0
    assert mapper.called is True
    assert output_path.exists()


def test_recommend_overwrite_is_explicit_and_atomic(tmp_path) -> None:
    profile_path, mapping_path = setup_inputs(tmp_path)
    output_path = tmp_path / "recommendation.json"; output_path.write_text("old")
    assert main(["recommend", "--profile", str(profile_path), "--mapping-candidates", str(mapping_path), "--output", str(output_path), "--overwrite"], output_fn=lambda _: None) == 0
    assert json.loads(output_path.read_text())["schema_version"] == 3


class FailingMapper:
    def map(self, profile, rubric, catalog):
        raise LLMRequestError("Provider output remained invalid after 2 attempts.")


def test_provider_failure_preserves_profile_and_existing_output(tmp_path) -> None:
    profile = synthetic_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    output_path = tmp_path / "recommendation.json"; output_path.write_bytes(b"existing")
    before_profile = profile_path.read_bytes()
    messages = []
    result = main(
        ["recommend", "--profile", str(profile_path), "--output", str(output_path), "--overwrite"],
        mapper=FailingMapper(),
        output_fn=messages.append,
    )
    assert result == 1
    assert profile_path.read_bytes() == before_profile
    assert output_path.read_bytes() == b"existing"
    assert not list(tmp_path.glob("*decision*"))
    assert messages == ["Error: Provider output remained invalid after 2 attempts."]
    assert "raw-private-response" not in "".join(messages)


def test_provider_failure_without_existing_output_leaves_no_artifact(tmp_path) -> None:
    profile_path = save_profile(synthetic_profile(), tmp_path / "profile.json")
    output_path = tmp_path / "recommendation.json"
    messages = []
    assert main(
        ["recommend", "--profile", str(profile_path), "--output", str(output_path)],
        mapper=FailingMapper(),
        output_fn=messages.append,
    ) == 1
    assert not output_path.exists()


def test_cli_passes_explicit_diagnostics_directory_to_builtin_mapper(tmp_path, monkeypatch) -> None:
    profile_path = save_profile(synthetic_profile(), tmp_path / "profile.json")
    diagnostics_path = tmp_path / "diagnostics"
    received = []

    class CapturingMapper:
        def __init__(self, *, diagnostics_dir=None):
            received.append(diagnostics_dir)

        def map(self, profile, rubric, catalog):
            return mapping_set()

    monkeypatch.setattr("aarvia.cli.OpenAIProfileDimensionMapper", CapturingMapper)
    assert main(
        [
            "recommend", "--profile", str(profile_path),
            "--output", str(tmp_path / "result.json"),
            "--provider-diagnostics-dir", str(diagnostics_path),
        ],
        output_fn=lambda _: None,
    ) == 0
    assert received == [diagnostics_path]


def test_cli_warns_when_provider_candidates_are_isolated(tmp_path) -> None:
    profile = synthetic_profile()
    profile_path = save_profile(profile, tmp_path / "profile.json")
    payload = clone_payload()
    payload["mappings"][0]["profile_fact_references"] = [{
        "path": "career_preferences.currently_considered_roles[0]",
        "value_snapshot": "AI Engineer",
    }]
    candidates = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload,
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=2,
        response_hash_reference="sha256:" + hashlib.sha256(b"fixture").hexdigest(),
    )

    class IsolatingMapper:
        def map(self, profile, rubric, catalog):
            return candidates

    messages = []
    assert main(
        ["recommend", "--profile", str(profile_path), "--output", str(tmp_path / "result.json")],
        mapper=IsolatingMapper(),
        output_fn=messages.append,
    ) == 0
    assert any(
        "1 Provider mapping candidate(s) were rejected" in message
        for message in messages
    )
