from __future__ import annotations

from dataclasses import replace
import json

import pytest

from aarvia.jd_matching import (
    JDAnalysisArtifact,
    JDMatchReportArtifact,
    JDMatchStatus,
    JDProviderContractError,
    JD_SEMANTIC_ACCEPTANCE_CASES,
    build_jd_match_with_retry,
    build_jd_match,
    build_jd_provider_payload,
    generate_jd_analysis_id,
    generate_requirement_id,
    jd_provider_schema,
    render_jd_match_report,
    OpenAIJDMatchingProvider,
    validate_jd_provider_output,
)
from aarvia.llm_client import LLMRequestError, LLMSettings
from aarvia.resume_context import ResumeFact, ResumeFactKind, ResumeFactVisibility, ValidatedResumeContext
from aarvia.role_catalog import Phase2ValidationError
from test_base_resume import _base


NOW = "2026-10-14T00:00:00+00:00"
ANALYSIS_DATE = "2026-10-14"
JD = "Build reliable Python services. A bachelor's degree is preferred. Must be authorized to work in the US."


def _context(tmp_path):
    _, context, base = _base(tmp_path)
    return ValidatedResumeContext(base_resume=base, **context)


def _payload(context):
    fact = context.fact_index().facts[0]
    return {
        "requirements": [
            {
                "requirement_type": "capability",
                "normalized_text": "Reliable Python services",
                "exact_quote": "Build reliable Python services.",
                "condition_clauses": [{"normalized_text": "Reliable Python services", "exact_quote": "Build reliable Python services."}],
                "evaluation_mode": "evidence_match", "logic_operator": "atomic",
                "support_scope": "partial", "fact_ids": [fact.fact_id],
                "reasoning": "The cited verified fact provides partial support.",
            },
            {
                "requirement_type": "qualification",
                "normalized_text": "Bachelor's degree",
                "exact_quote": "A bachelor's degree is preferred.",
                "condition_clauses": [{"normalized_text": "Bachelor's degree", "exact_quote": "A bachelor's degree is preferred."}],
                "evaluation_mode": "evidence_match", "logic_operator": "atomic",
                "support_scope": "none", "fact_ids": [],
                "reasoning": "Current verified materials do not establish this qualification.",
            },
            {
                "requirement_type": "application_condition",
                "normalized_text": "US work authorization",
                "exact_quote": "Must be authorized to work in the US.",
                "condition_clauses": [{"normalized_text": "US work authorization", "exact_quote": "Must be authorized to work in the US."}],
                "evaluation_mode": "evidence_match", "logic_operator": "atomic",
                "support_scope": "none", "fact_ids": [],
                "reasoning": "Work authorization is not inferred from other facts.",
            },
        ]
    }


def _artifacts(tmp_path):
    context = _context(tmp_path)
    analysis, report = build_jd_match(
        jd_content=JD, source_name="synthetic-jd.txt", provider_payload=_payload(context),
        provider_name="fixture", provider_model="fixture-model", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    return context, analysis, report


def test_context_index_excludes_contact_and_keeps_confirmed_fact_classes(tmp_path):
    context = _context(tmp_path); index = context.fact_index()
    assert index.facts
    assert {item.fact_kind for item in index.facts} <= {
        ResumeFactKind.EVIDENCE_ITEM,
        ResumeFactKind.ENRICHMENT_CLAIM,
        ResumeFactKind.STRUCTURAL_SELECTION,
    }
    payload = build_jd_provider_payload(JD, index.facts, analysis_date=ANALYSIS_DATE)
    assert "owner@example.test" not in payload
    assert not ({item.need_id for item in context.evidence_bank.needs} & {item.fact_id for item in index.facts})


def test_provider_inventory_has_minimal_traceable_source_context(tmp_path):
    context = _context(tmp_path)
    index = context.fact_index()
    inventory = json.loads(
        build_jd_provider_payload(JD, index.facts, analysis_date=ANALYSIS_DATE)
    )["verified_fact_inventory"]
    by_id = {item.fact_id: item for item in index.facts}
    assert inventory
    for item in inventory:
        source = item["source_context"]
        assert source is not None
        assert source["source_reference"]
        assert source["record_type"]
        fact = by_id[item["fact_id"]]
        if fact.source_record_id is not None:
            assert source["source_reference"] == fact.source_record_id
    serialized = json.dumps(inventory)
    assert "owner@example.test" not in serialized
    assert "contact" not in serialized.casefold()
    assert {item["source_context"]["contribution_scope"] for item in inventory} <= {
        None, "personal_contribution", "team_context",
    }


def test_build_round_trip_and_required_preferred_are_preserved(tmp_path):
    context, analysis, report = _artifacts(tmp_path)
    assert JDAnalysisArtifact.from_dict(analysis.to_dict()) == analysis
    assert JDMatchReportArtifact.from_dict(report.to_dict()) == report
    report.validate(jd_analysis=analysis, context=context)
    assert [item.importance.value for item in analysis.requirements] == ["unspecified", "preferred", "required"]
    assert report.results[1].status == JDMatchStatus.EVIDENCE_MISSING
    rendered = render_jd_match_report(analysis, report, context=context)
    assert "hiring probability" in rendered
    assert f"Analysis date: {ANALYSIS_DATE}" in rendered


def test_jd_analysis_schema_one_round_trip_remains_exact(tmp_path):
    _, analysis, _ = _artifacts(tmp_path)
    legacy_requirements = []
    for item in analysis.requirements:
        draft = replace(
            item, requirement_id="pending", section=None, logic_operator=None,
            condition_clauses=(),
        )
        legacy_requirements.append(
            replace(draft, requirement_id=generate_requirement_id(draft))
        )
    legacy_draft = replace(
        analysis, jd_analysis_id="pending", analysis_date=None,
        requirements=tuple(legacy_requirements), schema_version=1,
    )
    legacy = replace(
        legacy_draft, jd_analysis_id=generate_jd_analysis_id(legacy_draft),
    )
    wire = legacy.to_dict()
    assert "analysis_date" not in wire
    assert JDAnalysisArtifact.from_dict(wire).to_dict() == wire


def test_jd_analysis_schema_two_round_trip_remains_exact(tmp_path):
    _, analysis, _ = _artifacts(tmp_path)
    legacy_requirements = []
    for item in analysis.requirements:
        draft = replace(
            item, requirement_id="pending", section=None, logic_operator=None,
            condition_clauses=(),
        )
        legacy_requirements.append(
            replace(draft, requirement_id=generate_requirement_id(draft))
        )
    legacy_draft = replace(
        analysis, jd_analysis_id="pending",
        requirements=tuple(legacy_requirements), schema_version=2,
    )
    legacy = replace(
        legacy_draft, jd_analysis_id=generate_jd_analysis_id(legacy_draft),
    )
    wire = legacy.to_dict()
    assert JDAnalysisArtifact.from_dict(wire).to_dict() == wire


def test_analysis_date_is_explicit_and_tamper_checked(tmp_path):
    _, analysis, _ = _artifacts(tmp_path)
    assert analysis.schema_version == 3
    assert analysis.analysis_date == ANALYSIS_DATE
    bad = analysis.to_dict()
    bad["analysis_date"] = "not-a-date"
    with pytest.raises(Phase2ValidationError):
        JDAnalysisArtifact.from_dict(bad)


def test_jd_content_preserves_utf8_bytes_and_trailing_newline(tmp_path):
    context = _context(tmp_path)
    content = JD + "\n"
    analysis, _ = build_jd_match(
        jd_content=content, source_name="synthetic-jd.txt",
        provider_payload=_payload(context), provider_name="fixture",
        provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    loaded = JDAnalysisArtifact.from_dict(analysis.to_dict())
    assert loaded.content == content
    assert loaded.content_fingerprint == analysis.content_fingerprint


def test_quote_tampering_and_ambiguous_quote_are_rejected(tmp_path):
    context, analysis, _ = _artifacts(tmp_path)
    bad = analysis.to_dict(); bad["requirements"][0]["exact_quote"] = "invented"
    with pytest.raises(Phase2ValidationError):
        JDAnalysisArtifact.from_dict(bad)
    payload = _payload(context); payload["requirements"][0]["exact_quote"] = "work"
    with pytest.raises(JDProviderContractError, match="exactly once"):
        build_jd_match(jd_content="work and work", source_name="x.txt", provider_payload=payload,
                       provider_name="fixture", provider_model="fixture", context=context,
                       analysis_date=ANALYSIS_DATE, created_at=NOW)


def test_unknown_fact_and_status_evidence_relationship_are_rejected(tmp_path):
    context = _context(tmp_path); payload = _payload(context)
    payload["requirements"][0]["fact_ids"] = ["fact_forged"]
    with pytest.raises(Phase2ValidationError, match="unknown or ineligible"):
        build_jd_match(jd_content=JD, source_name="x", provider_payload=payload,
                       provider_name="fixture", provider_model="fixture", context=context,
                       analysis_date=ANALYSIS_DATE, created_at=NOW)
    payload = _payload(context); payload["requirements"][1]["fact_ids"] = [context.fact_index().facts[0].fact_id]
    with pytest.raises(JDProviderContractError, match="evidence_missing"):
        build_jd_match(jd_content=JD, source_name="x", provider_payload=payload,
                       provider_name="fixture", provider_model="fixture", context=context,
                       analysis_date=ANALYSIS_DATE, created_at=NOW)


def test_compound_partial_and_procedural_application_items_are_distinct(tmp_path):
    context = _context(tmp_path)
    fact_id = context.fact_index().facts[0].fact_id
    jd = (
        "At Fictional Systems, build customer integrations using Python and a multi-agent framework. "
        "Submit a resume and transcript. Applications close November 20, 2026."
    )
    payload = {"requirements": [
        {
            "requirement_type": "responsibility",
            "normalized_text": "Build customer integrations using Python and a multi-agent framework",
            "exact_quote": "At Fictional Systems, build customer integrations using Python and a multi-agent framework.",
            "condition_clauses": [
                {"normalized_text": "Build customer integrations using Python", "exact_quote": "build customer integrations using Python"},
                {"normalized_text": "Use a multi-agent framework", "exact_quote": "a multi-agent framework"},
            ],
            "evaluation_mode": "evidence_match", "logic_operator": "all_of",
            "support_scope": "partial", "fact_ids": [fact_id],
            "reasoning": "Verified evidence supports one transferable component; customer and multi-agent context remain unverified.",
        },
        {
            "requirement_type": "application_condition",
            "normalized_text": "Submit a resume and transcript",
            "exact_quote": "Submit a resume and transcript.",
            "condition_clauses": [{"normalized_text": "Submit a resume and transcript", "exact_quote": "Submit a resume and transcript."}],
            "evaluation_mode": "procedural_action", "logic_operator": "all_of",
            "support_scope": "not_applicable", "fact_ids": [],
            "reasoning": "This is an application procedure, not a capability assessment.",
        },
        {
            "requirement_type": "application_condition",
            "normalized_text": "Application deadline: November 20, 2026",
            "exact_quote": "Applications close November 20, 2026.",
            "condition_clauses": [{"normalized_text": "Application deadline", "exact_quote": "Applications close November 20, 2026."}],
            "evaluation_mode": "procedural_action", "logic_operator": "atomic",
            "support_scope": "not_applicable", "fact_ids": [],
            "reasoning": "The deadline is a procedural condition.",
        },
    ]}
    analysis, report = build_jd_match(
        jd_content=jd, source_name="fictional-jd.txt", provider_payload=payload,
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert [item.status for item in report.results] == [
        JDMatchStatus.PARTIALLY_SUPPORTED,
        JDMatchStatus.PROCEDURAL_ACTION_REQUIRED,
        JDMatchStatus.PROCEDURAL_ACTION_REQUIRED,
    ]
    rendered = render_jd_match_report(analysis, report, context=context)
    assert "application procedure to complete, not a capability gap" in rendered


def test_any_of_complete_branch_derives_satisfied_without_penalty_from_other_branch(tmp_path):
    context = _context(tmp_path)
    payload = _payload(context)
    item = payload["requirements"][0]
    item["normalized_text"] = "Currently pursuing a bachelor's or master's degree"
    item["logic_operator"] = "any_of"
    item["support_scope"] = "complete"
    _, report = build_jd_match(
        jd_content=JD, source_name="fictional-jd.txt", provider_payload=payload,
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert report.results[0].status == JDMatchStatus.SATISFIED


def test_semantic_acceptance_cases_are_small_annotated_and_non_personal():
    assert [item["case_id"] for item in JD_SEMANTIC_ACCEPTANCE_CASES] == [
        "deadline_is_procedure",
        "or_branch_satisfied",
        "return_intent_remains_unknown",
        "learning_and_single_agent_are_not_multi_agent_use",
        "employer_context_allows_transfer",
        "engineering_controls_are_not_deployment_or_ai_safety",
    ]
    for item in JD_SEMANTIC_ACCEPTANCE_CASES:
        assert item["expected"]
        assert item["must_not_infer"]
        assert item["allowed_conservative_conclusion"]
        assert "Google" not in json.dumps(item)


def test_schema_three_preserves_section_logic_and_clause_coverage(tmp_path):
    context = _context(tmp_path)
    fact_id = context.fact_index().facts[0].fact_id
    jd = (
        "This internship is intended for candidates in their penultimate year, pursuing a bachelor's or master's degree.\n"
        "Minimum qualifications:\n"
        "- Must know Python and SQL.\n"
        "Preferred qualifications:\n"
        "- Experience with tracing or metrics.\n"
        "Responsibilities:\n"
        "- Build reliable services.\n"
    )
    raw = [
        ("qualification", "This internship is intended for candidates in their penultimate year, pursuing a bachelor's or master's degree.", "any_of", [
            ("Penultimate-year candidate", "in their penultimate year"),
            ("Pursuing a bachelor's or master's degree", "pursuing a bachelor's or master's degree"),
        ]),
        ("capability", "- Must know Python and SQL.", "all_of", [
            ("Python", "Python"), ("SQL", "SQL"),
        ]),
        ("capability", "- Experience with tracing or metrics.", "any_of", [
            ("Tracing", "tracing"), ("Metrics", "metrics"),
        ]),
        ("responsibility", "- Build reliable services.", "atomic", [
            ("Build reliable services", "Build reliable services"),
        ]),
    ]
    payload = {"requirements": []}
    for req_type, quote, operator, clauses in raw:
        payload["requirements"].append({
            "requirement_type": req_type,
            "normalized_text": quote.removeprefix("- ").rstrip("."),
            "exact_quote": quote,
            "condition_clauses": [
                {"normalized_text": normalized, "exact_quote": exact}
                for normalized, exact in clauses
            ],
            "evaluation_mode": "evidence_match",
            "logic_operator": operator,
            "support_scope": "partial",
            "fact_ids": [fact_id],
            "reasoning": "One verified fact offers conservative partial support.",
        })
    analysis, _ = build_jd_match(
        jd_content=jd, source_name="fictional-jd.txt", provider_payload=payload,
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert analysis.schema_version == 3
    assert [item.section.value for item in analysis.requirements] == [
        "body_context", "minimum_qualifications", "preferred_qualifications",
        "responsibilities",
    ]
    assert [item.importance.value for item in analysis.requirements] == [
        "unspecified", "required", "preferred", "unspecified",
    ]
    assert len(analysis.requirements[0].condition_clauses) == 2
    assert analysis.requirements[0].logic_operator.value == "any_of"


def test_condition_clause_quote_and_provider_field_injection_are_rejected(tmp_path):
    context = _context(tmp_path)
    payload = _payload(context)
    payload["requirements"][0]["condition_clauses"][0]["exact_quote"] = "invented"
    with pytest.raises(JDProviderContractError, match="clause quote"):
        build_jd_match(
            jd_content=JD, source_name="fictional-jd.txt", provider_payload=payload,
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )
    payload = _payload(context)
    payload["requirements"][0]["source_context"] = {"invented": True}
    with pytest.raises(JDProviderContractError, match="does not satisfy"):
        build_jd_match(
            jd_content=JD, source_name="fictional-jd.txt", provider_payload=payload,
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )


def test_pure_provider_contract_matches_builder_and_rejects_schema_envelopes(tmp_path):
    context = _context(tmp_path)
    payload = _payload(context)
    requirements, matches = validate_jd_provider_output(
        jd_content=JD,
        provider_payload=payload,
        facts=context.fact_index().facts,
    )
    analysis, report = build_jd_match(
        jd_content=JD, source_name="fictional-jd.txt", provider_payload=payload,
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert requirements == analysis.requirements
    assert matches == report.results

    schema_envelope = {
        "type": "object",
        "properties": {"requirements": payload["requirements"]},
        "required": ["requirements"],
        "additionalProperties": False,
    }
    with pytest.raises(JDProviderContractError):
        validate_jd_provider_output(
            jd_content=JD,
            provider_payload=schema_envelope,
            facts=context.fact_index().facts,
        )
    with pytest.raises(JDProviderContractError):
        validate_jd_provider_output(
            jd_content=JD,
            provider_payload={**payload, "application_conditions": []},
            facts=context.fact_index().facts,
        )


def test_pure_provider_contract_rejects_overlapping_and_duplicate_clauses(tmp_path):
    context = _context(tmp_path)
    fact_id = context.fact_index().facts[0].fact_id
    jd = "You must return to your degree program after the internship, which ends August 15, 2027."
    requirement = {
        "requirement_type": "qualification",
        "normalized_text": "Return after the internship",
        "exact_quote": jd,
        "condition_clauses": [
            {
                "normalized_text": "Return after internship",
                "exact_quote": "You must return to your degree program after the internship",
            },
            {
                "normalized_text": "Internship end date",
                "exact_quote": "the internship, which ends August 15, 2027",
            },
        ],
        "evaluation_mode": "evidence_match",
        "logic_operator": "all_of",
        "support_scope": "partial",
        "fact_ids": [fact_id],
        "reasoning": "The timeline is compatible but intent remains unconfirmed.",
    }
    with pytest.raises(JDProviderContractError) as captured:
        validate_jd_provider_output(
            jd_content=jd,
            provider_payload={"requirements": [requirement]},
            facts=context.fact_index().facts,
        )
    assert captured.value.code == "overlapping_condition_clause"

    duplicate = dict(requirement)
    duplicate["condition_clauses"] = [
        {
            "normalized_text": "Return after internship",
            "exact_quote": "You must return to your degree program after the internship",
        },
        {
            "normalized_text": "Return after internship",
            "exact_quote": "You must return to your degree program after the internship",
        },
    ]
    with pytest.raises(JDProviderContractError) as captured:
        validate_jd_provider_output(
            jd_content=jd,
            provider_payload={"requirements": [duplicate]},
            facts=context.fact_index().facts,
        )
    assert captured.value.code == "duplicate_condition_clause"


def test_procedural_status_is_rejected_for_capability(tmp_path):
    context = _context(tmp_path)
    payload = _payload(context)
    payload["requirements"][0]["evaluation_mode"] = "procedural_action"
    payload["requirements"][0]["support_scope"] = "not_applicable"
    payload["requirements"][0]["fact_ids"] = []
    with pytest.raises(JDProviderContractError, match="application conditions"):
        build_jd_match(
            jd_content=JD, source_name="x", provider_payload=payload,
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )


def test_visibility_marks_verified_material_not_shown_in_resume(tmp_path):
    context = _context(tmp_path)
    hidden = next((item for item in context.fact_index().facts if item.visibility.value == "eligible_not_shown"), None)
    if hidden is None:
        pytest.skip("fixture exposes every eligible fact")
    payload = _payload(context); payload["requirements"][0]["fact_ids"] = [hidden.fact_id]
    _, report = build_jd_match(jd_content=JD, source_name="x", provider_payload=payload,
                               provider_name="fixture", provider_model="fixture", context=context,
                               analysis_date=ANALYSIS_DATE, created_at=NOW)
    assert report.results[0].evidence_references[0].visibility.value == "eligible_not_shown"


def test_stale_context_and_reference_tampering_fail_typed_validation(tmp_path):
    context, analysis, report = _artifacts(tmp_path)
    with pytest.raises(Phase2ValidationError):
        replace(report, base_resume_id="base_resume_stale").validate(jd_analysis=analysis, context=context)
    data = report.to_dict()
    data["results"][0]["evidence_references"][0]["visibility"] = "structural_context"
    with pytest.raises(Phase2ValidationError):
        JDMatchReportArtifact.from_dict({**data, "match_report_id": report.match_report_id})


def test_provider_injection_and_duplicate_requirements_are_rejected(tmp_path):
    context = _context(tmp_path); payload = _payload(context)
    payload["requirements"][0]["score"] = 99
    with pytest.raises(Phase2ValidationError):
        build_jd_match(jd_content=JD, source_name="x", provider_payload=payload,
                       provider_name="fixture", provider_model="fixture", context=context,
                       analysis_date=ANALYSIS_DATE, created_at=NOW)
    payload = _payload(context); payload["requirements"][0]["status"] = "satisfied"
    with pytest.raises(JDProviderContractError):
        build_jd_match(
            jd_content=JD, source_name="x", provider_payload=payload,
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )
    payload = _payload(context); payload["requirements"].append(dict(payload["requirements"][0]))
    with pytest.raises(JDProviderContractError, match="duplicate"):
        build_jd_match(jd_content=JD, source_name="x", provider_payload=payload,
                       provider_name="fixture", provider_model="fixture", context=context,
                       analysis_date=ANALYSIS_DATE, created_at=NOW)


def test_openai_and_bailian_use_the_same_contact_free_inventory():
    fact = ResumeFact(
        "evidence_item_fixture", ResumeFactKind.EVIDENCE_ITEM, "source_record_fixture",
        "Verified Python work", ("evidence_item_fixture",), (),
        ResumeFactVisibility.ELIGIBLE_NOT_SHOWN,
    )
    response_payload = json.dumps({"requirements": []})
    observed = {}

    class Responses:
        def create(self, **kwargs):
            observed["openai"] = kwargs["input"]
            observed["openai_instructions"] = kwargs["instructions"]
            observed["openai_schema"] = kwargs["text"]["format"]["schema"]
            return type("Response", (), {"output_text": response_payload})()

    class Completions:
        def create(self, **kwargs):
            observed["bailian"] = kwargs["messages"][1]["content"]
            observed["bailian_instructions"] = kwargs["messages"][0]["content"]
            message = type("Message", (), {"content": response_payload})()
            return type("Completion", (), {"choices": [type("Choice", (), {"message": message})()]})()

    openai = OpenAIJDMatchingProvider(
        settings=LLMSettings("secret", "gpt-test", None),
        client=type("Client", (), {"responses": Responses()})(),
    )
    bailian = OpenAIJDMatchingProvider(
        settings=LLMSettings("secret", "qwen-test", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        client=type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})(),
    )
    assert openai.analyze(JD, [fact], analysis_date=ANALYSIS_DATE) == bailian.analyze(
        JD, [fact], analysis_date=ANALYSIS_DATE
    )
    assert observed["openai"] == observed["bailian"]
    assert observed["openai_instructions"] == observed["bailian_instructions"]
    assert "secret" not in observed["openai"]
    request = json.loads(observed["openai"])
    assert request["analysis_context"]["analysis_date"] == ANALYSIS_DATE
    instructions = observed["openai_instructions"]
    for rule in (
        "an ended degree is not currently pursued",
        "preserve AND/OR meaning",
        "not a requirement for prior experience at that employer",
        "single-agent work from multi-agent implementation",
        "never prove work authorization, twelve-week availability",
        "Extract every explicit application deadline",
        "Returning to a degree program after an internship",
        "Engineering completeness controls do not establish production deployment",
        "RETURN A DATA INSTANCE, NOT THE JSON SCHEMA",
        "only top-level key is requirements",
        "Never return schema-wrapper keys",
        "JSON SCHEMA FOR THE DATA INSTANCE",
        "SEMANTIC GUIDANCE EXAMPLES (not the output shape)",
        "Names introduced by e.g. or such as are illustrative",
        "Topical, learning-stage, or adjacent background alone is none",
        "including an atomic requirement with genuine partial evidence",
        "Use contradicted only for a directly contradictory verified fact",
    ):
        assert rule in instructions
    enum = observed["openai_schema"]["properties"]["requirements"]["items"]["properties"]["fact_ids"]["items"]["enum"]
    assert enum == [fact.fact_id]
    properties = observed["openai_schema"]["properties"]["requirements"]["items"]["properties"]
    assert "status" not in properties
    assert "importance" not in properties
    assert properties["condition_clauses"]["minItems"] == 1
    assert properties["evaluation_mode"]["enum"] == ["evidence_match", "procedural_action"]
    assert properties["logic_operator"]["enum"] == ["atomic", "all_of", "any_of"]
    assert properties["support_scope"]["enum"] == [
        "complete", "partial", "none", "contradicted", "not_applicable",
    ]
    assert "deadline_is_procedure" in instructions
    assert "employer_context_allows_transfer" in instructions


def test_semantic_guidance_examples_preserve_support_scope_boundaries():
    cases = {item["case_id"]: item for item in JD_SEMANTIC_ACCEPTANCE_CASES}
    assert cases["or_branch_satisfied"]["expected"]["support_scope"] == "complete"
    assert cases["return_intent_remains_unknown"]["expected"]["support_scope"] == "partial"
    assert cases["employer_context_allows_transfer"]["expected"]["support_scope"] == "partial"
    assert cases["learning_and_single_agent_are_not_multi_agent_use"]["expected"]["support_scope"] == "none"
    assert cases["engineering_controls_are_not_deployment_or_ai_safety"]["expected"]["support_scope"] == "none"


class _SequenceProvider:
    provider_name = "fixture"
    provider_model = "fixture-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def analyze(self, jd_content, facts, *, analysis_date, correction=None):
        self.calls.append((tuple(item.fact_id for item in facts), analysis_date, correction))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def test_invalid_reference_is_corrected_once_with_the_same_inventory(tmp_path):
    context = _context(tmp_path)
    invalid = _payload(context)
    invalid["requirements"][0]["fact_ids"] = ["material_not_a_fact_id"]
    provider = _SequenceProvider([invalid, _payload(context)])
    _, report = build_jd_match_with_retry(
        jd_content=JD, source_name="x", provider=provider, context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert len(provider.calls) == 2
    assert provider.calls[0][0] == provider.calls[1][0]
    assert provider.calls[0][1] == provider.calls[1][1] == ANALYSIS_DATE
    assert provider.calls[0][2] is None
    assert "requirements[0].fact_ids" in provider.calls[1][2]
    assert "Copy fact_id values verbatim" in provider.calls[1][2]
    assert report.results[0].evidence_references


def test_two_ineligible_reference_attempts_are_rejected_without_guessing(tmp_path):
    context = _context(tmp_path)
    invalid = _payload(context)
    invalid["requirements"][0]["fact_ids"] = ["wording_candidate_not_eligible"]
    provider = _SequenceProvider([invalid, invalid])
    with pytest.raises(JDProviderContractError, match="after two attempts") as captured:
        build_jd_match_with_retry(
            jd_content=JD, source_name="x", provider=provider, context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )
    assert len(provider.calls) == 2
    assert captured.value.code == "unknown_or_ineligible_fact"


def test_valid_response_is_not_retried_and_auth_failure_is_not_corrected(tmp_path):
    context = _context(tmp_path)
    valid = _SequenceProvider([_payload(context)])
    build_jd_match_with_retry(
        jd_content=JD, source_name="x", provider=valid, context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW,
    )
    assert len(valid.calls) == 1

    denied = _SequenceProvider([LLMRequestError("LLM authentication failed")])
    with pytest.raises(LLMRequestError, match="authentication"):
        build_jd_match_with_retry(
            jd_content=JD, source_name="x", provider=denied, context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )
    assert len(denied.calls) == 1


def test_request_schema_inventory_and_validation_index_are_identical(tmp_path):
    context = _context(tmp_path)
    index = context.fact_index()
    payload = json.loads(build_jd_provider_payload(JD, index.facts, analysis_date=ANALYSIS_DATE))
    inventory_ids = tuple(item["fact_id"] for item in payload["verified_fact_inventory"])
    assert payload["analysis_context"]["analysis_date"] == ANALYSIS_DATE
    enum_ids = tuple(jd_provider_schema(index.facts)["properties"]["requirements"]["items"]["properties"]["fact_ids"]["items"]["enum"])
    assert inventory_ids == enum_ids == tuple(sorted(index.fact_by_id))
    analysis, report = build_jd_match(
        jd_content=JD, source_name="x", provider_payload=_payload(context),
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at=NOW, fact_index=index,
    )
    report.validate(jd_analysis=analysis, context=context)


def test_invalid_identifier_error_is_bounded_and_sanitized(tmp_path):
    context = _context(tmp_path)
    raw_id = "bad\nsecret-" + "x" * 100
    payload = _payload(context)
    payload["requirements"][0]["fact_ids"] = [raw_id]
    with pytest.raises(JDProviderContractError) as captured:
        build_jd_match(
            jd_content=JD, source_name="x", provider_payload=payload,
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
        )
    assert raw_id not in str(captured.value)
    assert "\n" not in str(captured.value)
    assert len(captured.value.detail) < 100


def test_match_revision_is_immutable_and_requires_same_provenance(tmp_path):
    context, _, original = _artifacts(tmp_path)
    _, revised = build_jd_match(
        jd_content=JD, source_name="synthetic-jd.txt", provider_payload=_payload(context),
        provider_name="fixture", provider_model="fixture", context=context,
        analysis_date=ANALYSIS_DATE, created_at="2026-10-14T01:00:00+00:00",
        superseded_match_report=original,
    )
    assert revised.supersedes_match_report_id == original.match_report_id
    with pytest.raises(Phase2ValidationError, match="timestamp"):
        build_jd_match(
            jd_content=JD, source_name="synthetic-jd.txt", provider_payload=_payload(context),
            provider_name="fixture", provider_model="fixture", context=context,
            analysis_date=ANALYSIS_DATE, created_at=NOW,
            superseded_match_report=original,
        )
