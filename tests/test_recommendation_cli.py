import json
import hashlib
from dataclasses import replace

import pytest

from aarvia.cli import main
from aarvia.evidence_binding_review import (
    BindingReviewDecision,
    BindingReviewerType,
    create_evidence_binding_review_artifact,
    load_evidence_binding_reviews,
    save_evidence_binding_reviews,
)
from aarvia.llm_client import LLMRequestError
from aarvia.profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
    save_mapping_candidates,
)
from aarvia.storage import save_profile
from aarvia.capability_rubric import production_capability_rubric
from aarvia.role_catalog import production_role_catalog
from recommendation_fixtures import mapping_set, mapping_set_v3, mapping_set_v4, synthetic_profile


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


def review_profile():
    data = synthetic_profile().to_dict()
    for index in range(2):
        data["experience_overview"].append(
            {
                "experience_type": "project",
                "organization_or_project_name": f"Review Project {index}",
                "title_or_role": "Developer",
                "short_factual_summary": f"Implemented independent review behavior {index}.",
                "start_date": "2025-01",
                "end_date": "2025-02",
            }
        )
    return type(synthetic_profile()).from_dict(data)


def test_recommend_help(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["recommend", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--mapping-candidates" in output
    assert "--mapping-artifact" in output
    assert "--mapping-output" in output
    assert "--review-artifact" in output
    assert "--overwrite" in output
    assert "--provider-diagnostics-dir" in output


def test_review_evidence_help(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["review-evidence", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--profile" in output
    assert "--mapping" in output
    assert "--output" in output
    assert "--overwrite" in output


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
        return mapping_set_v4(profile)


def test_recommend_supports_injected_mock_provider(tmp_path) -> None:
    profile = synthetic_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapper = FakeMapper(); output_path = tmp_path / "recommendation.json"
    assert main(["recommend", "--profile", str(profile_path), "--output", str(output_path)], mapper=mapper, output_fn=lambda _: None) == 0
    assert mapper.called is True
    assert output_path.exists()
    assert json.loads(output_path.read_text())["schema_version"] == 5
    assert json.loads(profile_path.with_suffix(".mapping.json").read_text())["schema_version"] == 4


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
        def __init__(self, *, diagnostics_dir=None, mapping_schema_version=None):
            received.append((diagnostics_dir, mapping_schema_version))

        def map(self, profile, rubric, catalog):
            return mapping_set_v4(profile)

    monkeypatch.setattr("aarvia.cli.OpenAIProfileDimensionMapper", CapturingMapper)
    assert main(
        [
            "recommend", "--profile", str(profile_path),
            "--output", str(tmp_path / "result.json"),
            "--provider-diagnostics-dir", str(diagnostics_path),
        ],
        output_fn=lambda _: None,
    ) == 0
    assert received == [(diagnostics_path, 4)]


def test_cli_warns_when_provider_candidates_are_isolated(tmp_path) -> None:
    profile = synthetic_profile()
    profile_path = save_profile(profile, tmp_path / "profile.json")
    valid = mapping_set_v3(profile)
    binding = valid.mappings[0]
    preference_span = next(
        item for item in canonical_evidence_span_inventory(profile)
        if item.path == "career_preferences.currently_considered_roles[0]"
    )
    payload = {
        "mappings": [{
            "role_id": binding.role_id,
            "dimension_id": binding.dimension_id,
            "criterion_id": binding.criterion_id,
            "span_id": preference_span.span_id,
            "proposed_binding_type": "direct",
            "provider_confidence": "high",
        }],
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }
    candidates = ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload,
        profile=profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=2,
        response_hash_reference="sha256:" + hashlib.sha256(b"fixture").hexdigest(),
        evidence_spans=canonical_evidence_span_inventory(profile),
        mapping_schema_version=4,
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


def test_default_recommend_saves_mapping_four_and_recommendation_five(tmp_path) -> None:
    profile = review_profile()
    profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping_path = tmp_path / "mapping-v3.json"
    recommendation_path = tmp_path / "recommendation-v4.json"
    messages = []
    mapper = FakeMapper()
    assert main(
        [
            "recommend", "--profile", str(profile_path),
            "--mapping-output", str(mapping_path),
            "--output", str(recommendation_path),
        ],
        mapper=mapper,
        output_fn=messages.append,
    ) == 0
    assert mapper.called
    assert json.loads(mapping_path.read_text())["schema_version"] == 4
    assert json.loads(recommendation_path.read_text())["schema_version"] == 5
    assert any(f"Mapping saved to: {mapping_path}" == item for item in messages)
    assert any("review-evidence" in item for item in messages)


def test_review_evidence_records_all_three_decisions_and_skips_structural(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile, binding_count=3)
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review_path = tmp_path / "review.json"
    answers = iter(["c", "r", "d", "y"])
    messages = []
    assert main(
        [
            "review-evidence", "--profile", str(profile_path),
            "--mapping", str(mapping_path), "--output", str(review_path),
        ],
        input_fn=lambda _: next(answers),
        output_fn=messages.append,
    ) == 0
    review = load_evidence_binding_reviews(
        review_path, profile=profile, rubric=production_capability_rubric(),
        mapping=mapping, catalog=production_role_catalog(),
    )
    assert {item.decision for item in review.reviews} == set(BindingReviewDecision)
    assert all(item.reviewer_type == BindingReviewerType.PROFILE_OWNER for item in review.reviews)
    assert all("api" not in item.casefold() for item in messages)


def test_review_evidence_cancel_writes_nothing(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile, binding_count=3)
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review_path = tmp_path / "review.json"
    assert main(
        [
            "review-evidence", "--profile", str(profile_path),
            "--mapping", str(mapping_path), "--output", str(review_path),
        ],
        input_fn=lambda _: "q",
        output_fn=lambda _: None,
    ) == 0
    assert not review_path.exists()


def test_review_evidence_keyboard_interrupt_writes_nothing(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile)
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review_path = tmp_path / "review.json"
    messages = []
    assert main(
        [
            "review-evidence", "--profile", str(profile_path),
            "--mapping", str(mapping_path), "--output", str(review_path),
        ],
        input_fn=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
        output_fn=messages.append,
    ) == 130
    assert not review_path.exists()
    assert messages[-1] == "Evidence review cancelled. No files were changed."


def test_review_evidence_does_not_ask_about_structural_bindings(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    rubric = production_capability_rubric()
    spans = canonical_evidence_span_inventory(profile)
    project_span = next(
        item for item in spans
        if item.path == "experience_overview[0].short_factual_summary"
    )
    skill_span = next(item for item in spans if item.path == "skills[0].skill_name")
    project_dimension = next(
        item for item in rubric.dimensions
        if "project_summary" in {
            value.value for value in item.evidence_support_policy.allowed_evidence_classes
        }
    )
    skill_dimension = next(
        item for item in rubric.dimensions
        if item.dimension_id != project_dimension.dimension_id
        and "skill_name" in {
            value.value for value in item.evidence_support_policy.allowed_evidence_classes
        }
    )
    mapping = ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
        {
            "mappings": [
                {
                    "role_id": project_dimension.role_id,
                    "dimension_id": project_dimension.dimension_id,
                    "criterion_id": project_dimension.criterion_ids[0],
                    "span_id": project_span.span_id,
                    "proposed_binding_type": "direct",
                    "provider_confidence": "high",
                },
                {
                    "role_id": skill_dimension.role_id,
                    "dimension_id": skill_dimension.dimension_id,
                    "criterion_id": skill_dimension.criterion_ids[0],
                    "span_id": skill_span.span_id,
                    "proposed_binding_type": "direct",
                    "provider_confidence": "high",
                },
            ],
            "directional_signals": [], "constraints": [], "conflict_warnings": [],
        },
        profile=profile, rubric=rubric, catalog=production_role_catalog(),
        provider_name="fixture", provider_model="fixture", evidence_spans=spans,
    )
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile, rubric=rubric,
        catalog=production_role_catalog(),
    )
    prompts = []
    answers = iter(["c", "y"])
    review_path = tmp_path / "review.json"
    assert main(
        [
            "review-evidence", "--profile", str(profile_path),
            "--mapping", str(mapping_path), "--output", str(review_path),
        ],
        input_fn=lambda prompt: prompts.append(prompt) or next(answers),
        output_fn=lambda _: None,
    ) == 0
    review = load_evidence_binding_reviews(
        review_path, profile=profile, rubric=rubric, mapping=mapping,
        catalog=production_role_catalog(),
    )
    assert len(review.reviews) == 1
    assert len(prompts) == 2


def test_review_existing_output_is_preserved(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile)
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review_path = tmp_path / "review.json"; review_path.write_bytes(b"keep")
    messages = []
    assert main(
        [
            "review-evidence", "--profile", str(profile_path),
            "--mapping", str(mapping_path), "--output", str(review_path),
        ],
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("must not prompt")),
        output_fn=messages.append,
    ) == 1
    assert review_path.read_bytes() == b"keep"
    assert any("--overwrite" in item for item in messages)


class NoCallMapper:
    def map(self, *_):
        raise AssertionError("Provider must not be called when reusing a Mapping artifact")


def test_reusing_mapping_and_review_never_calls_provider(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile, binding_count=3)
    mapping_path = save_mapping_candidates(
        mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review = create_evidence_binding_review_artifact(
        profile=profile, rubric=production_capability_rubric(), mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            item.binding_id: (
                BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                "2026-09-28T12:00:00+00:00",
            )
            for item in mapping.mappings
        },
    )
    review_path = save_evidence_binding_reviews(
        review, tmp_path / "review.json", profile=profile,
        rubric=production_capability_rubric(), mapping=mapping,
        catalog=production_role_catalog(),
    )
    output_path = tmp_path / "reviewed-recommendation.json"
    messages = []
    assert main(
        [
            "recommend", "--profile", str(profile_path),
            "--mapping-artifact", str(mapping_path),
            "--review-artifact", str(review_path),
            "--output", str(output_path),
        ],
        mapper=NoCallMapper(),
        output_fn=messages.append,
    ) == 0
    data = json.loads(output_path.read_text())
    assert data["schema_version"] == 4
    assert data["evidence_review_artifact_id"] == review.review_artifact_id
    assert any("3 confirmed, 0 rejected, 0 deferred" in item for item in messages)
    assert any(item == "Recommendation changes" for item in messages)


def test_stale_review_fails_before_recommendation_write(tmp_path) -> None:
    profile = review_profile(); profile_path = save_profile(profile, tmp_path / "profile.json")
    mapping = mapping_set_v3(profile, binding_count=1)
    review = create_evidence_binding_review_artifact(
        profile=profile, rubric=production_capability_rubric(), mapping=mapping,
        catalog=production_role_catalog(),
        decisions={
            mapping.mappings[0].binding_id: (
                BindingReviewDecision.CONFIRMED,
                BindingReviewerType.PROFILE_OWNER,
                "2026-09-28T12:00:00+00:00",
            )
        },
    )
    changed_mapping = replace(mapping, provider_model="changed-model")
    mapping_path = save_mapping_candidates(
        changed_mapping, tmp_path / "mapping.json", profile=profile,
        rubric=production_capability_rubric(), catalog=production_role_catalog(),
    )
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review.to_dict()), encoding="utf-8")
    output_path = tmp_path / "recommendation.json"
    messages = []
    assert main(
        [
            "recommend", "--profile", str(profile_path),
            "--mapping-artifact", str(mapping_path),
            "--review-artifact", str(review_path),
            "--output", str(output_path),
        ],
        mapper=NoCallMapper(), output_fn=messages.append,
    ) == 1
    assert not output_path.exists()
    assert any("stale" in item.casefold() for item in messages)


def test_existing_default_mapping_output_prevents_provider_call_and_preserves_files(tmp_path) -> None:
    profile_path = save_profile(review_profile(), tmp_path / "profile.json")
    mapping_path = profile_path.with_suffix(".mapping.json")
    mapping_path.write_bytes(b"existing mapping")
    output_path = tmp_path / "recommendation.json"
    messages = []
    assert main(
        ["recommend", "--profile", str(profile_path), "--output", str(output_path)],
        mapper=NoCallMapper(), output_fn=messages.append,
    ) == 1
    assert mapping_path.read_bytes() == b"existing mapping"
    assert not output_path.exists()
    assert any("--overwrite" in item for item in messages)
