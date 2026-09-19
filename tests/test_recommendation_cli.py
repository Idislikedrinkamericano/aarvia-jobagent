import json

import pytest

from aarvia.cli import main
from aarvia.profile_dimension_mapping import save_mapping_candidates
from aarvia.storage import save_profile
from aarvia.capability_rubric import production_capability_rubric
from aarvia.role_catalog import production_role_catalog
from recommendation_fixtures import mapping_set, synthetic_profile


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


def test_recommend_offline_writes_artifact_without_mutating_profile(tmp_path) -> None:
    profile_path, mapping_path = setup_inputs(tmp_path)
    before = profile_path.read_bytes()
    output_path = tmp_path / "recommendation.json"
    messages = []
    assert main(["recommend", "--profile", str(profile_path), "--mapping-candidates", str(mapping_path), "--output", str(output_path)], output_fn=messages.append) == 0
    assert profile_path.read_bytes() == before
    data = json.loads(output_path.read_text())
    assert data["schema_version"] == 2
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
    assert json.loads(output_path.read_text())["schema_version"] == 2
