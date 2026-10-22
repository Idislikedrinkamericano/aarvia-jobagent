#!/usr/bin/env python3
"""Run a small, private JD-semantic comparison through the production provider.

This is deliberately not an Aarvia artifact, CLI command, or automated semantic
grader.  ``check`` is fully offline.  ``run`` makes exactly one request per
model/case pair, performs transport-contract checks, and leaves semantic review
as ``unreviewed``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from aarvia.jd_matching import (
    JDProviderContractError,
    JD_SEMANTIC_ACCEPTANCE_CASES,
    OpenAIJDMatchingProvider,
    build_jd_provider_payload,
    jd_provider_schema,
    validate_jd_provider_output,
)
from aarvia.llm_client import LLMSettings
from aarvia.profile_dimension_mapping import provider_capabilities
from aarvia.resume_context import (
    ResumeFact,
    ResumeFactKind,
    ResumeFactSourceContext,
    ResumeFactVisibility,
)


EVALUATION_FORMAT = "aarvia.non_production_jd_model_comparison"
EVALUATION_FORMAT_VERSION = 2
HUMAN_RUBRIC_VERSION = "jd-semantic-human-rubric-v3"
FIXED_ANALYSIS_DATE = "2027-01-15"
ANALYSIS_DATE_MEANING = "fixed fictional-case test time; not the actual run date"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts)
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _fact(
    case_id: str,
    label: str,
    text: str,
    *,
    record_type: str,
    subject: str,
    title: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    contribution_scope: str | None = None,
    fact_kind: ResumeFactKind = ResumeFactKind.ENRICHMENT_CLAIM,
) -> ResumeFact:
    source_id = _stable_id("eval_source_", case_id, subject)
    return ResumeFact(
        fact_id=_stable_id("eval_fact_", case_id, label, text),
        fact_kind=fact_kind,
        source_record_id=source_id,
        text=text,
        evidence_item_ids=(),
        enrichment_claim_ids=(),
        visibility=ResumeFactVisibility.ELIGIBLE_NOT_SHOWN,
        source_context=ResumeFactSourceContext(
            source_reference=source_id,
            record_type=record_type,
            organization_or_project_name=subject,
            title_or_role=title,
            start_date=start_date,
            end_date=end_date,
            contribution_scope=contribution_scope,
        ),
    )


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    jd_content: str
    facts: tuple[ResumeFact, ...]
    human_criteria: Mapping[str, Any]


def evaluation_cases() -> tuple[EvaluationCase, ...]:
    """Return six fictional cases; criteria are never passed to the provider."""
    timeline_id = "internship_before_expected_graduation"
    attribution_id = "similar_projects_keep_attribution"
    examples_id = "examples_are_not_exhaustive_requirements"
    multi_agent_id = "learning_and_single_agent_are_not_multi_agent"
    controls_id = "integrity_controls_are_not_deployment_or_safety"
    transfer_id = "employer_context_preserves_transferable_support"

    timeline_fact = _fact(
        timeline_id,
        "active_degree",
        "The degree began in September 2026 and has an expected graduation date in December 2027.",
        record_type="education",
        subject="Fictional Institute of Technology",
        title="Master of Science candidate",
        start_date="2026-09",
        end_date="2027-12",
        fact_kind=ResumeFactKind.STRUCTURAL_SELECTION,
    )
    aurora_fact = _fact(
        attribution_id,
        "aurora_factuality",
        "Investigated factuality regressions and documented evaluation findings for an AI assistant.",
        record_type="project",
        subject="Project Aurora",
        title="Evaluation contributor",
        start_date="2026-02",
        end_date="2026-05",
        contribution_scope="individual contribution",
    )
    beacon_fact = _fact(
        attribution_id,
        "beacon_integrity",
        "Built schema-validation and atomic-save checks for an assistant workflow.",
        record_type="project",
        subject="Project Beacon",
        title="Software contributor",
        start_date="2026-02",
        end_date="2026-05",
        contribution_scope="team contribution",
    )
    framework_fact = _fact(
        examples_id,
        "pineflow_use",
        "Implemented an agent workflow with the PineFlow framework.",
        record_type="project",
        subject="Project Juniper",
        title="Developer",
        start_date="2026-03",
        end_date="2026-06",
        contribution_scope="individual contribution",
    )
    learning_fact = _fact(
        multi_agent_id,
        "framework_learning",
        "Currently learning the OrbitAgents framework.",
        record_type="skill",
        subject="OrbitAgents",
        fact_kind=ResumeFactKind.STRUCTURAL_SELECTION,
    )
    single_agent_fact = _fact(
        multi_agent_id,
        "single_agent",
        "Built one experimental agent that used a plan-and-act loop.",
        record_type="project",
        subject="Project Solstice",
        title="Developer",
        start_date="2026-01",
        end_date="2026-04",
        contribution_scope="individual contribution",
    )
    controls_fact = _fact(
        controls_id,
        "local_integrity",
        "Implemented input validation, atomic JSON persistence, and human-review boundaries in a local tool.",
        record_type="project",
        subject="Project Harbor",
        title="Software developer",
        start_date="2026-04",
        end_date="2026-07",
        contribution_scope="individual contribution",
    )
    transfer_fact = _fact(
        transfer_id,
        "provider_integration",
        "Integrated model-provider APIs and local tools behind typed interfaces in a verified project.",
        record_type="project",
        subject="Project Meridian",
        title="Software developer",
        start_date="2026-05",
        end_date="2026-08",
        contribution_scope="team contribution",
    )

    return (
        EvaluationCase(
            timeline_id,
            "Minimum qualification: You must return to your degree program after the internship, which ends August 15, 2027.",
            (timeline_fact,),
            {
                "expected_support": ["partial"],
                "must_recognize": ["the expected graduation is after the internship end"],
                "must_not_infer": [
                    "the person must graduate and then re-enroll",
                    "a prior interruption in study",
                    "confirmed intent to return",
                ],
            },
        ),
        EvaluationCase(
            attribution_id,
            "Responsibility: Investigate factuality regressions in an AI assistant and document evaluation findings.",
            (aurora_fact, beacon_fact),
            {
                "expected_support": ["complete"],
                "required_fact_ids": [aurora_fact.fact_id],
                "must_not_attribute_to_fact_ids": [beacon_fact.fact_id],
                "must_not_infer": ["Project Beacon performed factuality evaluation"],
            },
        ),
        EvaluationCase(
            examples_id,
            "Minimum qualification: Experience with agent frameworks, e.g., Cedar Agents and Elm Graph.",
            (framework_fact,),
            {
                "expected_support": ["complete"],
                "required_fact_ids": [framework_fact.fact_id],
                "must_not_infer": [
                    "both named example frameworks are mandatory",
                    "the person used Cedar Agents or Elm Graph",
                ],
                "adjudication_rule": (
                    "e.g. introduces non-mandatory examples; absence of the named "
                    "examples alone cannot reduce support for the general requirement"
                ),
            },
        ),
        EvaluationCase(
            multi_agent_id,
            "Preferred qualification: Implemented multi-agent workflows using OrbitAgents with agent-to-agent delegation.",
            (learning_fact, single_agent_fact),
            {
                "expected_support": ["none"],
                "must_not_infer": [
                    "OrbitAgents was used in the project",
                    "the project contained multiple agents",
                    "agent-to-agent delegation was implemented",
                ],
                "adjudication_rule": (
                    "learning and single-agent work may be discussed as related background, "
                    "but support none of the requested multi-agent, framework-use, or delegation conditions"
                ),
            },
        ),
        EvaluationCase(
            controls_id,
            "Responsibility: Deploy AI agents to production and perform agentic AI safety evaluations.",
            (controls_fact,),
            {
                "expected_support": ["none"],
                "must_not_infer": [
                    "production deployment",
                    "production observability",
                    "agentic AI safety evaluation",
                ],
                "generalization_classification": "near_duplicate_of_provider_guidance",
                "counts_as_independent_generalization_evidence": False,
            },
        ),
        EvaluationCase(
            transfer_id,
            "Responsibility: Integrate Northstar Cloud models into customer production infrastructure and internal APIs.",
            (transfer_fact,),
            {
                "expected_support": ["partial"],
                "required_fact_ids": [transfer_fact.fact_id],
                "must_not_infer": [
                    "Northstar Cloud experience",
                    "customer production deployment",
                    "ownership of production infrastructure",
                ],
            },
        ),
    )


class RequestRecorder:
    """Capture only request hashes/configuration while forwarding the real call."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.requests: list[dict[str, Any]] = []
        self.responses = _ResponsesProxy(client.responses, self)
        self.chat = _ChatProxy(client.chat, self)

    def record(
        self,
        *,
        protocol: str,
        instructions: str,
        payload: str,
        request_options: Mapping[str, Any],
    ) -> dict[str, Any]:
        entry = {
            "protocol": protocol,
            "instructions_sha256": _sha256_text(instructions),
            "payload_sha256": _sha256_text(payload),
            "request_options_sha256": _sha256_text(
                json.dumps(request_options, sort_keys=True, separators=(",", ":"))
            ),
            "returned_model": None,
        }
        self.requests.append(entry)
        return entry


class _ResponsesProxy:
    def __init__(self, responses: Any, recorder: RequestRecorder) -> None:
        self._responses = responses
        self._recorder = recorder

    def create(self, **kwargs: Any) -> Any:
        options = {
            "model": kwargs.get("model"),
            "store": kwargs.get("store"),
            "text": kwargs.get("text"),
        }
        entry = self._recorder.record(
            protocol="responses",
            instructions=kwargs.get("instructions", ""),
            payload=kwargs.get("input", ""),
            request_options=options,
        )
        response = self._responses.create(**kwargs)
        returned = getattr(response, "model", None)
        entry["returned_model"] = returned if isinstance(returned, str) else None
        return response


class _CompletionsProxy:
    def __init__(self, completions: Any, recorder: RequestRecorder) -> None:
        self._completions = completions
        self._recorder = recorder

    def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        instructions = messages[0].get("content", "") if len(messages) > 0 else ""
        payload = messages[1].get("content", "") if len(messages) > 1 else ""
        options = {
            "model": kwargs.get("model"),
            "response_format": kwargs.get("response_format"),
            "extra_body": kwargs.get("extra_body"),
        }
        entry = self._recorder.record(
            protocol="chat_completions",
            instructions=instructions,
            payload=payload,
            request_options=options,
        )
        completion = self._completions.create(**kwargs)
        returned = getattr(completion, "model", None)
        entry["returned_model"] = returned if isinstance(returned, str) else None
        return completion


class _ChatProxy:
    def __init__(self, chat: Any, recorder: RequestRecorder) -> None:
        self.completions = _CompletionsProxy(chat.completions, recorder)


def check_provider_response(
    response: Any,
    *,
    case: EvaluationCase,
) -> dict[str, Any]:
    """Run the same pure Provider contract used by production construction."""
    if not isinstance(response, Mapping):
        return {
            "valid": False,
            "errors": [{"code": "response_not_object", "location": "provider_output"}],
        }
    try:
        validate_jd_provider_output(
            jd_content=case.jd_content,
            provider_payload=response,
            facts=case.facts,
        )
    except JDProviderContractError as error:
        return {
            "valid": False,
            "errors": [{"code": error.code, "location": error.location}],
        }
    return {"valid": True, "errors": []}


def _offline_check() -> None:
    cases = evaluation_cases()
    if len(cases) != 6 or len({case.case_id for case in cases}) != 6:
        raise RuntimeError("the evaluation fixture must contain six unique cases")
    production_case_ids = {item["case_id"] for item in JD_SEMANTIC_ACCEPTANCE_CASES}
    if production_case_ids.intersection(case.case_id for case in cases):
        raise RuntimeError("evaluation cases must not duplicate production instruction cases")

    all_fact_ids: set[str] = set()
    for case in cases:
        fact_ids = [fact.fact_id for fact in case.facts]
        if not fact_ids or len(fact_ids) != len(set(fact_ids)):
            raise RuntimeError(f"{case.case_id} has an invalid fact inventory")
        if all_fact_ids.intersection(fact_ids):
            raise RuntimeError("fact IDs must be unique across cases")
        all_fact_ids.update(fact_ids)
        payload = build_jd_provider_payload(
            case.jd_content, case.facts, analysis_date=FIXED_ANALYSIS_DATE
        )
        if payload != build_jd_provider_payload(
            case.jd_content,
            tuple(reversed(case.facts)),
            analysis_date=FIXED_ANALYSIS_DATE,
        ):
            raise RuntimeError(f"{case.case_id} payload is order-dependent")
        if case.case_id in payload or json.dumps(case.human_criteria, sort_keys=True) in payload:
            raise RuntimeError("human criteria leaked into the provider payload")
        fact_enum = jd_provider_schema(case.facts)["properties"]["requirements"]["items"]["properties"]["fact_ids"]["items"]["enum"]
        if fact_enum != sorted(fact_ids):
            raise RuntimeError(f"{case.case_id} schema fact inventory is inconsistent")
        synthetic_response = {
            "requirements": [{
                "requirement_type": "capability",
                "normalized_text": "Offline contract-only placeholder",
                "exact_quote": case.jd_content,
                "condition_clauses": [{
                    "normalized_text": "Offline contract-only placeholder",
                    "exact_quote": case.jd_content,
                }],
                "evaluation_mode": "evidence_match",
                "logic_operator": "atomic",
                "support_scope": "none",
                "fact_ids": [],
                "reasoning": "Offline contract-only placeholder",
            }]
        }
        if not check_provider_response(synthetic_response, case=case)["valid"]:
            raise RuntimeError(f"{case.case_id} contract checker failed its fixture")

    captured_client_options: dict[str, Any] = {}

    def offline_client_factory(**kwargs: Any) -> object:
        captured_client_options.update(kwargs)
        return object()

    _create_no_retry_client(
        LLMSettings(
            api_key="offline-placeholder",
            model="offline-model",
            base_url="https://provider.example.test/v1",
        ),
        client_factory=offline_client_factory,
    )
    if captured_client_options.get("max_retries") != 0:
        raise RuntimeError("evaluation client must disable SDK retries")
    print(
        "Offline check passed: 6 cases, deterministic inventories, "
        "SDK max_retries=0, no Provider calls."
    )


def _endpoint_metadata(base_url: str | None) -> dict[str, str | None]:
    parsed = urlparse(base_url) if base_url else None
    return {
        "host": None if parsed is None else parsed.hostname,
        "path": None if parsed is None else parsed.path,
    }


def _model_config(label: str, settings: LLMSettings) -> dict[str, Any]:
    capabilities = provider_capabilities(settings)
    return {
        "label": label,
        "configured_model": settings.model,
        "endpoint": _endpoint_metadata(settings.base_url),
        "protocol": capabilities.protocol,
        "response_format_mode": capabilities.response_format_mode,
        "thinking_configuration": (
            "disabled" if capabilities.disable_thinking else "provider_default"
        ),
        "sdk_max_retries": 0,
    }


def _case_record(case: EvaluationCase, *, analysis_date: str) -> dict[str, Any]:
    inventory = [fact.to_provider_dict() for fact in sorted(case.facts, key=lambda item: item.fact_id)]
    payload = build_jd_provider_payload(
        case.jd_content, case.facts, analysis_date=analysis_date
    )
    return {
        "case_id": case.case_id,
        "job_description": case.jd_content,
        "job_description_sha256": _sha256_text(case.jd_content),
        "verified_fact_inventory": inventory,
        "verified_fact_inventory_sha256": _sha256_text(
            json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "production_payload_sha256": _sha256_text(payload),
        "human_acceptance_criteria": case.human_criteria,
        "human_rubric_version": HUMAN_RUBRIC_VERSION,
    }


def _safe_failure(error: Exception) -> dict[str, str]:
    message = str(error).replace("\n", " ").strip()
    if len(message) > 240:
        message = message[:237] + "..."
    return {"error_type": type(error).__name__, "safe_message": message}


def _create_no_retry_client(
    settings: LLMSettings,
    *,
    client_factory: Any | None = None,
) -> Any:
    """Create the evaluation client with transport retries explicitly disabled."""
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    return client_factory(
        api_key=settings.api_key,
        base_url=settings.base_url,
        max_retries=0,
    )


def _run_once(
    *,
    label: str,
    settings: LLMSettings,
    case: EvaluationCase,
    analysis_date: str,
) -> dict[str, Any]:
    client = RequestRecorder(_create_no_retry_client(settings))
    provider = OpenAIJDMatchingProvider(settings=settings, client=client)
    response: Mapping[str, Any] | None = None
    failure: dict[str, str] | None = None
    try:
        response = provider.analyze(
            case.jd_content,
            case.facts,
            analysis_date=analysis_date,
            correction=None,
        )
    except Exception as error:  # Each failed request remains an explicit result.
        failure = _safe_failure(error)
    if len(client.requests) != 1:
        raise RuntimeError(
            f"{label}/{case.case_id} made {len(client.requests)} requests; expected exactly one"
        )
    contract = (
        {"valid": False, "errors": [{"code": "request_failed", "location": "provider"}]}
        if response is None
        else check_provider_response(response, case=case)
    )
    return {
        "case_id": case.case_id,
        "model_label": label,
        "request": client.requests[0],
        "structured_response": response,
        "request_failure": failure,
        "contract_check": contract,
        "human_semantic_review": {
            "status": "unreviewed",
            "rubric_version": HUMAN_RUBRIC_VERSION,
            "judgment": None,
            "reviewed_at": None,
            "notes": None,
            "criteria": case.human_criteria,
        },
    }


def _atomic_write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".comparison-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _run(args: argparse.Namespace) -> None:
    baseline_model = args.baseline_model.strip()
    candidate_model = args.candidate_model.strip()
    if not baseline_model:
        raise ValueError("baseline model ID must be non-empty")
    if not candidate_model:
        raise ValueError("candidate model ID must be explicitly provided")
    if baseline_model.casefold() == candidate_model.casefold():
        raise ValueError("candidate model must differ from the qwen-plus baseline")

    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    base_url = (os.getenv("AARVIA_LLM_BASE_URL") or "").strip()
    if not base_url:
        raise RuntimeError("AARVIA_LLM_BASE_URL must be loaded from the private environment")

    baseline_key = (
        os.getenv("AARVIA_EVAL_BASELINE_API_KEY")
        or os.getenv("AARVIA_LLM_API_KEY")
        or ""
    ).strip()
    candidate_key = (
        os.getenv("AARVIA_EVAL_CANDIDATE_API_KEY") or baseline_key
    ).strip()
    if not baseline_key:
        raise RuntimeError(
            "set AARVIA_EVAL_BASELINE_API_KEY or AARVIA_LLM_API_KEY"
        )
    if not candidate_key:
        raise RuntimeError("set AARVIA_EVAL_CANDIDATE_API_KEY")

    baseline_settings = LLMSettings(
        api_key=baseline_key,
        model=baseline_model,
        base_url=base_url,
    )
    candidate_settings = LLMSettings(
        api_key=candidate_key,
        model=candidate_model,
        base_url=base_url,
    )
    configs = (
        ("baseline", baseline_settings),
        ("candidate", candidate_settings),
    )
    available_cases = {case.case_id: case for case in evaluation_cases()}
    selected_ids = args.case or tuple(available_cases)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("case selection cannot contain duplicates")
    cases = tuple(available_cases[case_id] for case_id in selected_ids)
    results: list[dict[str, Any]] = []
    for case in cases:
        for label, settings in configs:
            results.append(
                _run_once(
                    label=label,
                    settings=settings,
                    case=case,
                    analysis_date=FIXED_ANALYSIS_DATE,
                )
            )
    expected_requests = len(cases) * len(configs)
    if len(results) != expected_requests:
        raise RuntimeError(
            f"comparison must contain exactly {expected_requests} single-attempt results"
        )

    model_configs = [_model_config(label, settings) for label, settings in configs]
    comparison_keys = {
        "endpoint", "protocol", "response_format_mode", "thinking_configuration"
    }
    configuration_equivalent = all(
        model_configs[0][key] == model_configs[1][key]
        for key in comparison_keys
    )
    equivalence: list[dict[str, Any]] = []
    for case in cases:
        pair = [result for result in results if result["case_id"] == case.case_id]
        equivalence.append({
            "case_id": case.case_id,
            "instructions_hash_equal": pair[0]["request"]["instructions_sha256"] == pair[1]["request"]["instructions_sha256"],
            "payload_hash_equal": pair[0]["request"]["payload_sha256"] == pair[1]["request"]["payload_sha256"],
        })

    artifact = {
        "format": EVALUATION_FORMAT,
        "format_version": EVALUATION_FORMAT_VERSION,
        "markers": [
            "non-production analytical output",
            "not accepted by Aarvia typed artifact loaders",
            "contract validity is not semantic correctness",
            "human semantic judgments begin unreviewed",
        ],
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "analysis_date": FIXED_ANALYSIS_DATE,
        "analysis_date_meaning": ANALYSIS_DATE_MEANING,
        "contract_validation": {
            "implementation": "aarvia.jd_matching.validate_jd_provider_output",
            "meaning": "the same pure Provider contract used by production JD Match construction",
        },
        "human_rubric": {
            "version": HUMAN_RUBRIC_VERSION,
            "status_in_expected_range_is_semantic_pass": False,
            "reasoning_requires_human_review": True,
            "near_guidance_cases_are_independent_generalization_evidence": False,
        },
        "attempt_policy": {
            "attempts_per_model_per_case": 1,
            "maximum_requests": expected_requests,
            "correction_or_retry": False,
            "responses_merged": False,
        },
        "model_configurations": model_configs,
        "configuration_equivalent_except_model": configuration_equivalent,
        "comparison_caveat": (
            "Configuration differs beyond model ID; do not describe this as a model-only comparison."
            if not configuration_equivalent
            else "Recorded protocol, endpoint path, response mode, and thinking configuration match; provider-side differences may still exist."
        ),
        "request_equivalence_by_case": equivalence,
        "case_count": len(cases),
        "cases": [
            _case_record(case, analysis_date=FIXED_ANALYSIS_DATE) for case in cases
        ],
        "result_count": len(results),
        "results": results,
    }
    output_path = output_dir / "comparison.json"
    _atomic_write_new(output_path, artifact)
    print(f"Saved non-production comparison: {output_path}")
    print("Human semantic judgments remain unreviewed.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two models on six private, fictional JD-matching cases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate fixtures offline; makes no Provider calls")
    run = subparsers.add_parser(
        "run",
        help="make exactly 12 single-attempt Provider calls",
        description=(
            "Uses AARVIA_LLM_BASE_URL for both models and the fixed fictional-case "
            f"analysis date {FIXED_ANALYSIS_DATE}; this is test time, not run time."
        ),
    )
    run.add_argument("--baseline-model", default="qwen-plus")
    run.add_argument(
        "--candidate-model",
        required=True,
        help="exact non-empty model ID; must differ from the baseline",
    )
    run.add_argument(
        "--case",
        action="append",
        choices=tuple(case.case_id for case in evaluation_cases()),
        help="run only this case; repeat to select more (default: all six)",
    )
    run.add_argument("--output-dir", required=True, help="new private evaluation directory")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "check":
        _offline_check()
    else:
        _run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
