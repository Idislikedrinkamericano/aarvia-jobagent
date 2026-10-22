from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_jd_instructions.py"


def _module():
    spec = importlib.util.spec_from_file_location("compare_jd_instructions_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_offline_check_proves_the_controlled_request_boundary(capsys):
    module = _module()
    module._offline_check()
    output = capsys.readouterr().out
    assert "A/B differ only in instructions" in output
    assert "six compact cases share SDK configuration" in output
    assert "eight candidate cases each share SDK configuration" in output
    assert "max_retries=0" in output


def test_compact_instructions_are_shorter_and_contain_no_case_answer_or_rubric():
    module = _module()
    case = module._case()
    schema = module.jd_provider_schema(case.facts)
    compact = module.compact_instructions(schema)

    assert "e.g. or such as are non-mandatory examples" in compact
    assert "Use partial only when verified evidence supports an identifiable part" in compact
    assert "Use none when no requested capability or condition has support" in compact
    assert "requirements as the only top-level key" in compact
    assert "SEMANTIC GUIDANCE EXAMPLES" not in compact
    assert "jd-semantic-human-rubric" not in compact
    assert case.case_id not in compact
    assert "Cedar Agents" not in compact
    assert "Elm Graph" not in compact
    assert "PineFlow" not in compact


def test_existing_output_directory_is_rejected_before_environment_or_network(tmp_path):
    module = _module()
    output_dir = tmp_path / "already-exists"
    output_dir.mkdir()
    try:
        module._run(str(output_dir))
    except FileExistsError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("existing output directory was not rejected")


def test_compact_suite_rejects_existing_output_before_environment_or_network(tmp_path):
    module = _module()
    output_dir = tmp_path / "already-exists"
    output_dir.mkdir()
    try:
        module._run_compact_suite(str(output_dir))
    except FileExistsError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("existing suite output directory was not rejected")


def test_compact_suite_cli_is_fixed_to_six_cases_and_one_model():
    module = _module()
    args = module._parser().parse_args([
        "run-compact-suite", "--output-dir", "/tmp/new-private-result",
    ])
    assert args.command == "run-compact-suite"
    assert module.MODEL_ID == "qwen3.8-max-0902"
    assert len(module.evaluation_cases()) == 6


def test_compact_suite_changes_only_the_case_generated_schema():
    module = _module()
    marker = "JSON SCHEMA FOR THE DATA INSTANCE:\n"
    instructions = [
        module.compact_instructions(module.jd_provider_schema(case.facts))
        for case in module.evaluation_cases()
    ]
    bodies = [value.split(marker, 1)[0] for value in instructions]
    schemas = [value.split(marker, 1)[1] for value in instructions]
    assert len(set(bodies)) == 1
    assert len(set(schemas)) == 6


def test_candidate_instructions_cover_rules_without_examples_or_case_answers():
    module = _module()
    cases = module.candidate_evaluation_cases()
    marker = "JSON SCHEMA FOR THE DATA INSTANCE:\n"
    instructions = [
        module.candidate_instructions(module.jd_provider_schema(case.facts))
        for case in cases
    ]
    bodies = [value.split(marker, 1)[0] for value in instructions]
    schemas = [value.split(marker, 1)[1] for value in instructions]
    assert len(cases) == 8
    assert len(set(bodies)) == 1
    assert len(set(schemas)) == 8
    body = bodies[0]
    for heading in (
        "TASK AND SOURCE TEXT",
        "TYPE AND APPLICATION PROCEDURES",
        "SUPPORT SCOPE",
        "FACTS, ATTRIBUTION, AND INFERENCE",
        "TIME",
        "OUTPUT",
    ):
        assert heading in body
    assert "An action verb in a job responsibility does not make it" in body
    assert "cannot by itself confirm the person's intention to return" in body
    assert "topical, learning-stage, or otherwise related background alone" in body
    assert "SEMANTIC GUIDANCE EXAMPLES" not in body
    for case, instructions_for_case in zip(cases, instructions, strict=True):
        assert case.case_id not in instructions_for_case
        assert case.jd_content not in instructions_for_case
        assert "human_criteria" not in instructions_for_case
        assert "expected_support" not in instructions_for_case


def test_candidate_suite_adds_only_the_two_scoped_local_rubric_cases():
    module = _module()
    original_ids = {case.case_id for case in module.evaluation_cases()}
    candidate_cases = module.candidate_evaluation_cases()
    added = [case for case in candidate_cases if case.case_id not in original_ids]
    assert [case.case_id for case in added] == [
        "application_deadline_and_materials_are_procedural",
        "active_masters_satisfies_education_or",
    ]
    assert added[0].human_criteria["expected_evaluation_mode"] == "procedural_action"
    assert added[1].human_criteria["expected_support"] == ["complete"]


def test_candidate_suite_rejects_existing_output_and_has_fixed_cli(tmp_path):
    module = _module()
    output_dir = tmp_path / "already-exists"
    output_dir.mkdir()
    try:
        module._run_candidate_suite(str(output_dir))
    except FileExistsError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("existing candidate output directory was not rejected")
    args = module._parser().parse_args([
        "run-candidate-suite", "--output-dir", "/tmp/new-private-result",
    ])
    assert args.command == "run-candidate-suite"
    assert module.MODEL_ID == "qwen3.8-max-0902"
