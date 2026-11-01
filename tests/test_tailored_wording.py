from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aarvia.resume_wording import WordingReviewDecision
from aarvia.llm_client import LLMSettings
from aarvia.role_catalog import Phase2ValidationError
from aarvia.tailored_resume import build_tailored_resume, materialize_tailored_resume
from aarvia.tailored_wording import (
    TAILORED_WORDING_INSTRUCTIONS,
    OpenAITailoredWordingProvider,
    TailoredWordingProposalArtifact,
    WordingTailoredResumeArtifact,
    build_tailored_wording_proposal,
    build_wording_tailored_resume,
    create_tailored_wording_review,
    generate_tailored_wording_proposal,
    load_tailored_wording_proposal,
    load_tailored_wording_review,
    load_wording_tailored_resume,
    render_wording_tailored_resume_markdown,
    save_tailored_wording_proposal,
    save_tailored_wording_review,
    save_wording_tailored_resume,
    tailored_wording_provider_schema,
    _eligible_inventory,
)
from aarvia.tailoring_proposal import (
    TailoringReviewDecision,
    create_tailoring_proposal_review,
)
from test_tailored_resume import JD, NOW, _fixture


class MockProvider:
    provider_name = "fixture"
    provider_model = "fixture-wording-model"

    def __init__(self, count=2):
        self.count = count
        self.calls = []

    def propose(self, *, jd_content, bullet_inventory, requirement_inventory):
        self.calls.append((jd_content, bullet_inventory, requirement_inventory))
        result = []
        for item in bullet_inventory[: self.count]:
            result.append({
                "bullet_id": item["bullet_id"],
                "candidate_text": "JD-focused: " + item["original_text"],
                "requirement_ids": [item["allowed_requirement_ids"][0]],
                "adjustment_reason": "Bring the verified capability forward.",
            })
        return {"candidates": result}


def _context(tmp_path):
    context, analysis, match_report, proposal = _fixture(tmp_path)
    ordering_review = create_tailoring_proposal_review(
        proposal=proposal,
        decisions={
            item.suggestion_id: TailoringReviewDecision.ACCEPTED
            for item in proposal.suggestions
        },
        reviewed_at=NOW,
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
    )
    tailored = build_tailored_resume(
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
        proposal=proposal,
        proposal_review=ordering_review,
        created_at=NOW,
    )
    validation = dict(
        context=context,
        jd_analysis=analysis,
        match_report=match_report,
        tailoring_proposal=proposal,
        tailoring_review=ordering_review,
        tailored_resume=tailored,
    )
    return validation


def _proposal(tmp_path, count=2):
    validation = _context(tmp_path)
    provider = MockProvider(count=count)
    proposal = generate_tailored_wording_proposal(
        provider=provider, jd_content=JD, created_at=NOW, **validation,
    )
    return validation, provider, proposal


def test_provider_inventory_and_schema_exclude_contact_and_authority_fields(tmp_path):
    validation, provider, proposal = _proposal(tmp_path)
    assert proposal.candidates
    jd, bullets, requirements = provider.calls[0]
    assert jd == JD
    encoded = json.dumps((bullets, requirements), sort_keys=True)
    assert "contact" not in encoded.lower()
    schema = tailored_wording_provider_schema(bullets, requirements)
    fields = schema["properties"]["candidates"]["items"]["properties"]
    assert set(fields) == {"bullet_id", "candidate_text", "requirement_ids", "adjustment_reason"}
    assert not ({"fact_ids", "review", "status", "score"} & set(fields))
    proposal.validate(**validation)


def test_mixed_review_applies_only_confirmed_and_preserves_other_content(tmp_path):
    validation, _, proposal = _proposal(tmp_path)
    first, second = proposal.candidates
    review = create_tailored_wording_review(
        proposal=proposal,
        decisions={
            first.wording_candidate_id: WordingReviewDecision.CONFIRMED,
            second.wording_candidate_id: WordingReviewDecision.REJECTED,
        },
        reviewed_at=NOW,
        **validation,
    )
    result = build_wording_tailored_resume(
        proposal=proposal, review=review, created_at=NOW, **validation,
    )
    assert [item.wording_candidate_id for item in result.changes] == [first.wording_candidate_id]
    markdown = render_wording_tailored_resume_markdown(
        result, proposal=proposal, review=review, **validation,
    )
    assert first.candidate_text in markdown
    assert second.original_text in markdown
    assert second.candidate_text not in markdown
    ordered = materialize_tailored_resume(
        validation["tailored_resume"],
        context=validation["context"],
        jd_analysis=validation["jd_analysis"],
        match_report=validation["match_report"],
        proposal=validation["tailoring_proposal"],
        proposal_review=validation["tailoring_review"],
    )
    assert result.materialized_base_resume_id == ordered.base_resume_id
    assert len(ordered.bullets) == len(validation["context"].base_resume.bullets)


def test_deferred_or_rejected_review_changes_nothing(tmp_path):
    validation, _, proposal = _proposal(tmp_path)
    review = create_tailored_wording_review(
        proposal=proposal,
        decisions={
            item.wording_candidate_id: (
                WordingReviewDecision.DEFERRED if index == 0 else WordingReviewDecision.REJECTED
            )
            for index, item in enumerate(proposal.candidates)
        },
        reviewed_at=NOW,
        **validation,
    )
    result = build_wording_tailored_resume(
        proposal=proposal, review=review, created_at=NOW, **validation,
    )
    assert result.changes == ()


def test_no_op_candidates_are_counted_after_full_validation(tmp_path):
    validation = _context(tmp_path)
    bullets, _, _ = _eligible_inventory(**validation)
    first, second = bullets[:2]
    mixed = build_tailored_wording_proposal(
        provider_payload={"candidates": [
            {
                "bullet_id": first["bullet_id"],
                "candidate_text": first["original_text"],
                "requirement_ids": [first["allowed_requirement_ids"][0]],
                "adjustment_reason": "No change needed.",
            },
            {
                "bullet_id": second["bullet_id"],
                "candidate_text": "JD-focused: " + second["original_text"],
                "requirement_ids": [second["allowed_requirement_ids"][0]],
                "adjustment_reason": "Clarify relevance.",
            },
        ]},
        provider_name="fixture", provider_model="fixture",
        created_at=NOW, **validation,
    )
    assert mixed.received_candidate_count == 2
    assert mixed.no_op_candidate_count == 1
    assert len(mixed.candidates) == 1

    all_no_op = build_tailored_wording_proposal(
        provider_payload={"candidates": [{
            "bullet_id": first["bullet_id"],
            "candidate_text": first["original_text"],
            "requirement_ids": [first["allowed_requirement_ids"][0]],
            "adjustment_reason": "No change needed.",
        }]},
        provider_name="fixture", provider_model="fixture",
        created_at=NOW, **validation,
    )
    assert (all_no_op.received_candidate_count, all_no_op.no_op_candidate_count) == (1, 1)
    assert all_no_op.candidates == ()

    empty = build_tailored_wording_proposal(
        provider_payload={"candidates": []},
        provider_name="fixture", provider_model="fixture",
        created_at=NOW, **validation,
    )
    assert (empty.received_candidate_count, empty.no_op_candidate_count) == (0, 0)
    assert empty.candidates == ()


def test_duplicate_candidate_and_provider_injection_are_rejected(tmp_path):
    validation, provider, proposal = _proposal(tmp_path, count=1)
    raw = provider.propose(
        jd_content=JD,
        bullet_inventory=provider.calls[0][1],
        requirement_inventory=provider.calls[0][2],
    )
    raw["candidates"][0]["candidate_text"] = provider.calls[0][1][0]["original_text"]
    raw["candidates"].append(dict(raw["candidates"][0]))
    with pytest.raises(Phase2ValidationError, match="multiple candidates"):
        build_tailored_wording_proposal(
            provider_payload=raw, provider_name="fixture", provider_model="fixture",
            created_at=NOW, **validation,
        )
    injected = {"candidates": [{**raw["candidates"][0], "fact_ids": [proposal.candidates[0].fact_ids[0]]}]}
    with pytest.raises(Phase2ValidationError, match="unknown fields"):
        build_tailored_wording_proposal(
            provider_payload=injected, provider_name="fixture", provider_model="fixture",
            created_at=NOW, **validation,
        )
    unknown_bullet = {"candidates": [{
        **raw["candidates"][0], "bullet_id": "base_resume_bullet_unknown",
    }]}
    with pytest.raises(Phase2ValidationError, match="unknown or ineligible bullet"):
        build_tailored_wording_proposal(
            provider_payload=unknown_bullet, provider_name="fixture", provider_model="fixture",
            created_at=NOW, **validation,
        )
    unknown_requirement = {"candidates": [{
        **raw["candidates"][0], "requirement_ids": ["jd_requirement_unknown"],
    }]}
    with pytest.raises(Phase2ValidationError, match="requirement not supported"):
        build_tailored_wording_proposal(
            provider_payload=unknown_requirement, provider_name="fixture", provider_model="fixture",
            created_at=NOW, **validation,
        )
    multiline = {"candidates": [{
        **raw["candidates"][0],
        "candidate_text": "Supported first line.\nInjected second bullet.",
    }]}
    with pytest.raises(Phase2ValidationError, match="one trimmed bullet line"):
        build_tailored_wording_proposal(
            provider_payload=multiline, provider_name="fixture", provider_model="fixture",
            created_at=NOW, **validation,
        )


def test_stale_and_tampered_artifacts_are_rejected(tmp_path):
    validation, _, proposal = _proposal(tmp_path, count=1)
    with pytest.raises(Phase2ValidationError):
        replace(proposal, tailored_resume_id="tailored_resume_wrong").validate(**validation)
    candidate = proposal.candidates[0]
    tampered = proposal.to_dict()
    tampered["candidates"][0]["fact_ids"] = ["resume_fact_unknown"]
    with pytest.raises(Phase2ValidationError):
        TailoredWordingProposalArtifact.from_dict(tampered).validate(**validation)
    bad_counts = proposal.to_dict()
    bad_counts["received_candidate_count"] += 1
    with pytest.raises(Phase2ValidationError, match="counts do not reconcile"):
        TailoredWordingProposalArtifact.from_dict(bad_counts)
    review = create_tailored_wording_review(
        proposal=proposal,
        decisions={candidate.wording_candidate_id: WordingReviewDecision.CONFIRMED},
        reviewed_at=NOW,
        **validation,
    )
    result = build_wording_tailored_resume(
        proposal=proposal, review=review, created_at=NOW, **validation,
    )
    forged = result.to_dict()
    forged["changes"][0]["final_text"] = "Forged text"
    with pytest.raises(Phase2ValidationError):
        WordingTailoredResumeArtifact.from_dict(forged).validate(
            proposal=proposal, review=review, **validation,
        )


def test_typed_save_load_round_trip(tmp_path):
    validation, _, proposal = _proposal(tmp_path, count=1)
    review = create_tailored_wording_review(
        proposal=proposal,
        decisions={proposal.candidates[0].wording_candidate_id: WordingReviewDecision.CONFIRMED},
        reviewed_at=NOW,
        **validation,
    )
    final = build_wording_tailored_resume(
        proposal=proposal, review=review, created_at=NOW, **validation,
    )
    proposal_path, review_path, final_path = (
        tmp_path / "wording-proposal.json",
        tmp_path / "wording-review.json",
        tmp_path / "wording-final.json",
    )
    save_tailored_wording_proposal(proposal, proposal_path, **validation)
    loaded_proposal = load_tailored_wording_proposal(proposal_path, **validation)
    save_tailored_wording_review(
        review, review_path, proposal=loaded_proposal, **validation,
    )
    loaded_review = load_tailored_wording_review(
        review_path, proposal=loaded_proposal, **validation,
    )
    save_wording_tailored_resume(
        final, final_path, proposal=loaded_proposal, review=loaded_review, **validation,
    )
    assert load_wording_tailored_resume(
        final_path, proposal=loaded_proposal, review=loaded_review, **validation,
    ) == final


def test_semantic_review_cases_are_local_and_never_enter_provider_instructions():
    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "tailored_wording_semantic_cases.json")
        .read_text(encoding="utf-8")
    )
    assert {item["case_id"] for item in cases} == {
        "ownership_expansion", "invented_technology",
        "prototype_to_production", "team_boundary_lost",
    }
    assert all(item["review_decision"] == "reject" for item in cases)
    assert all(item["unsafe_candidate"] not in TAILORED_WORDING_INSTRUCTIONS for item in cases)


def test_openai_and_compatible_paths_share_the_same_transport_contract():
    bullets = ({
        "bullet_id": "base_resume_bullet_example",
        "original_text": "Built a fictional prototype.",
        "allowed_requirement_ids": ["jd_requirement_example"],
        "verified_evidence": [{"fact_id": "resume_fact_example", "text": "Built a fictional prototype.", "source_context": None}],
    },)
    requirements = ({
        "requirement_id": "jd_requirement_example", "requirement_type": "capability",
        "importance": "required", "exact_quote": "Build prototypes.",
        "match_status": "satisfied",
    },)
    response = json.dumps({"candidates": []})

    class Responses:
        def __init__(self): self.kwargs = None
        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(output_text=response)

    class Completions:
        def __init__(self): self.kwargs = None
        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))])

    responses = Responses()
    openai = OpenAITailoredWordingProvider(
        settings=LLMSettings("secret", "gpt-test"),
        client=SimpleNamespace(responses=responses),
    )
    completions = Completions()
    compatible = OpenAITailoredWordingProvider(
        settings=LLMSettings("secret", "custom", "https://provider.example.test/v1"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    assert openai.propose(jd_content="Fictional JD", bullet_inventory=bullets, requirement_inventory=requirements) == {"candidates": []}
    assert compatible.propose(jd_content="Fictional JD", bullet_inventory=bullets, requirement_inventory=requirements) == {"candidates": []}
    assert responses.kwargs["instructions"].startswith(TAILORED_WORDING_INSTRUCTIONS)
    assert completions.kwargs["messages"][0]["content"].startswith(TAILORED_WORDING_INSTRUCTIONS)
    assert responses.kwargs["input"] == completions.kwargs["messages"][1]["content"]
    assert completions.kwargs["response_format"] == {"type": "json_object"}
