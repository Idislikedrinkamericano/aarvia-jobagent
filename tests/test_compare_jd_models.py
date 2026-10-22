from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_jd_models.py"


def _module():
    spec = importlib.util.spec_from_file_location("compare_jd_models_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_response(case):
    return {
        "requirements": [{
            "requirement_type": "qualification",
            "normalized_text": "Fictional requirement",
            "exact_quote": case.jd_content,
            "condition_clauses": [{
                "normalized_text": "Fictional requirement",
                "exact_quote": case.jd_content,
            }],
            "evaluation_mode": "evidence_match",
            "logic_operator": "atomic",
            "support_scope": "partial",
            "fact_ids": [case.facts[0].fact_id],
            "reasoning": "The cited fictional fact provides partial support.",
        }]
    }


def test_eval_contract_is_the_production_contract():
    module = _module()
    case = module.evaluation_cases()[0]
    assert module.check_provider_response(_valid_response(case), case=case) == {
        "valid": True, "errors": [],
    }

    envelope = {
        "type": "object",
        "properties": {"requirements": _valid_response(case)["requirements"]},
        "required": ["requirements"],
        "additionalProperties": False,
    }
    assert not module.check_provider_response(envelope, case=case)["valid"]
    assert not module.check_provider_response(
        {**_valid_response(case), "application_conditions": []}, case=case,
    )["valid"]

    overlapping = _valid_response(case)
    overlapping["requirements"][0]["logic_operator"] = "all_of"
    overlapping["requirements"][0]["condition_clauses"] = [
        {
            "normalized_text": "Return after internship",
            "exact_quote": "You must return to your degree program after the internship",
        },
        {
            "normalized_text": "Internship end date",
            "exact_quote": "the internship, which ends August 15, 2027",
        },
    ]
    result = module.check_provider_response(overlapping, case=case)
    assert result == {
        "valid": False,
        "errors": [{
            "code": "overlapping_condition_clause",
            "location": "requirements[0].condition_clauses[1]",
        }],
    }

    duplicate = _valid_response(case)
    duplicate["requirements"][0]["condition_clauses"] *= 2
    assert not module.check_provider_response(duplicate, case=case)["valid"]

    invented = _valid_response(case)
    invented["requirements"][0]["condition_clauses"][0]["exact_quote"] = (
        "This text is not in the fictional JD."
    )
    assert not module.check_provider_response(invented, case=case)["valid"]


def test_rubric_v3_is_local_and_semantic_review_starts_unreviewed():
    module = _module()
    cases = {case.case_id: case for case in module.evaluation_cases()}
    examples = cases["examples_are_not_exhaustive_requirements"].human_criteria
    assert examples["expected_support"] == ["complete"]
    assert "non-mandatory" in examples["adjudication_rule"]
    multi = cases["learning_and_single_agent_are_not_multi_agent"].human_criteria
    assert multi["expected_support"] == ["none"]
    assert "support none" in multi["adjudication_rule"]
    controls = cases["integrity_controls_are_not_deployment_or_safety"].human_criteria
    assert controls["counts_as_independent_generalization_evidence"] is False

    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = type("Message", (), {"content": '{"requirements": []}'})()
            return type("Completion", (), {
                "model": kwargs["model"],
                "choices": [type("Choice", (), {"message": message})()],
            })()

    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})(),
    })()
    provider = module.OpenAIJDMatchingProvider(
        settings=module.LLMSettings(
            "offline", "qwen-plus", "https://provider.example.test/v1",
        ),
        client=client,
    )
    case = cases["examples_are_not_exhaustive_requirements"]
    provider.analyze(
        case.jd_content, case.facts, analysis_date=module.FIXED_ANALYSIS_DATE,
    )
    request_text = json.dumps(captured, ensure_ascii=False)
    assert module.HUMAN_RUBRIC_VERSION not in request_text
    assert case.case_id not in request_text
    assert "adjudication_rule" not in request_text


def test_contract_valid_does_not_claim_semantically_wrong_scopes_are_validated():
    module = _module()
    cases = {case.case_id: case for case in module.evaluation_cases()}

    examples = cases["examples_are_not_exhaustive_requirements"]
    examples_response = _valid_response(examples)
    assert examples_response["requirements"][0]["support_scope"] == "partial"
    assert module.check_provider_response(examples_response, case=examples)["valid"]
    assert examples.human_criteria["expected_support"] == ["complete"]

    multi = cases["learning_and_single_agent_are_not_multi_agent"]
    multi_response = _valid_response(multi)
    assert multi_response["requirements"][0]["support_scope"] == "partial"
    assert module.check_provider_response(multi_response, case=multi)["valid"]
    assert multi.human_criteria["expected_support"] == ["none"]


def test_single_case_selection_plans_exactly_two_requests(tmp_path, monkeypatch):
    module = _module()
    calls = []
    written = {}

    def fake_run_once(**kwargs):
        calls.append((kwargs["label"], kwargs["case"].case_id))
        return {
            "case_id": kwargs["case"].case_id,
            "model_label": kwargs["label"],
            "request": {
                "instructions_sha256": "same",
                "payload_sha256": "same",
                "request_options_sha256": kwargs["label"],
                "protocol": "chat_completions",
                "returned_model": None,
            },
            "structured_response": None,
            "request_failure": {"error_type": "offline", "safe_message": "offline"},
            "contract_check": {"valid": False, "errors": []},
            "human_semantic_review": {
                "status": "unreviewed",
                "rubric_version": module.HUMAN_RUBRIC_VERSION,
            },
        }

    monkeypatch.setattr(module, "_run_once", fake_run_once)
    monkeypatch.setattr(
        module, "_atomic_write_new",
        lambda path, payload: written.update(path=path, payload=payload),
    )
    monkeypatch.setenv("AARVIA_LLM_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("AARVIA_LLM_API_KEY", "offline-placeholder")
    args = argparse.Namespace(
        baseline_model="qwen-plus",
        candidate_model="qwen3.8-max-0902",
        case=["similar_projects_keep_attribution"],
        output_dir=str(tmp_path / "new-output"),
    )
    module._run(args)
    assert calls == [
        ("baseline", "similar_projects_keep_attribution"),
        ("candidate", "similar_projects_keep_attribution"),
    ]
    assert written["payload"]["case_count"] == 1
    assert written["payload"]["result_count"] == 2
    assert written["payload"]["attempt_policy"]["maximum_requests"] == 2
    assert written["payload"]["human_rubric"]["version"] == module.HUMAN_RUBRIC_VERSION
