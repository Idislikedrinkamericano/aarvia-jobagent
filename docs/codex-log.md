# Aarvia Development Log

Development records are appended to this file as the project evolves.

## 2026-08-28 - Phase 0 Initialization

- **Date:** 2026-08-28
- **Phase:** Phase 0 - Project Foundation
- **User Request:** Create the minimal Python project skeleton for Aarvia without implementing any agent functionality.
- **What Codex Changed:** Created the `src/aarvia` package, empty `data` and `tests` directories, project metadata, a focused Python `.gitignore`, the product README, and this development log.
- **Important Decisions:** Used a `src` layout and Python 3.10 minimum; kept runtime dependencies empty; made pytest an optional development dependency; did not add LLM libraries, agent frameworks, data schemas, frontend code, speculative modules, or placeholder tests.
- **Validation / Test Results:** `PYTHONPATH=src python3 -c 'import aarvia'` passed and reported version `0.1.0`. The directory structure, README pipeline and principles, TOML syntax, and empty runtime dependency list were verified. Pytest is declared in the `dev` optional dependency group, but the current Python environment does not have pytest installed; an attempted development dependency installation could not be completed, so pytest startup was not validated in this environment. No tests exist in Phase 0 by design.
- **Next Step:** Stop after validating the foundation. Phase 1 should begin only when explicitly requested.

## 2026-08-28 - Phase 0 Final Environment Validation

- **Date:** 2026-08-28
- **Phase:** Phase 0 - Project Foundation
- **User Request:** Run `.venv/bin/python -m pytest` and `.venv/bin/python -c "import aarvia"` using the manually prepared virtual environment, without installing anything or accessing the network.
- **What Codex Changed:** Ran only the two requested local validation commands and appended their results to this log. No dependencies or project modules were added.
- **Important Decisions:** Preserved the empty Phase 0 test directory and reported command exit statuses as observed. Did not attempt package installation, dependency installation, or network access.
- **Validation / Test Results:** Pytest 9.1.1 started successfully, loaded `pyproject.toml`, searched `tests`, and collected 0 items; because there are intentionally no tests, pytest exited with code 5. Direct `import aarvia` exited with code 1 and `ModuleNotFoundError`, indicating that the `src` layout package is not installed in the current `.venv`.
- **Next Step:** Keep Phase 0 unchanged. A future explicit setup step can install the local project into the virtual environment before repeating the direct import check.

## 2026-08-28 - Conversation Log Creation

- **Date:** 2026-08-28
- **Phase:** Phase 0 - Project Foundation
- **User Request:** Create `docs/conversation-log.md` and record the conversation turn by turn with `User` and `Assistant` entries.
- **What Codex Changed:** Created a dedicated conversation log, backfilled the four conversations to date, and documented an append-only chronological convention.
- **Important Decisions:** Kept user-facing conversation history separate from the structured development log. Excluded tool output and internal execution details from the conversation log.
- **Validation / Test Results:** Confirmed the new Markdown file exists, contains alternating `User` and `Assistant` sections, and preserves the earlier project discussion in chronological order.
- **Next Step:** Append future Aarvia conversations to `docs/conversation-log.md` while continuing to record development steps separately in this file.

## 2026-08-28 - Phase 1 Career Profile and Discovery

- **Date:** 2026-08-28
- **Phase:** Phase 1 - User Profile / Career Discovery
- **User Request:** Implement a structured, validated, persistent Career Profile; generate deterministic questions for missing information; add tests and bilingual README documentation; do not implement role recommendation or later pipeline stages.
- **What Codex Changed:** Added strict dataclass models for basic profile, education, experience overview, skills, career preferences, constraints, and open questions; added rule-based discovery and atomic JSON storage; exported the Phase 1 API; added focused pytest coverage using `tmp_path`; added `data/.gitkeep`; updated the English README and added `README.zh-CN.md`.
- **Important Decisions:** Incomplete profiles are valid because discovery must represent unanswered information, while every supplied education, experience, and skill entry is strictly validated. Unknown fields are rejected. Open questions are generated in a fixed order by ordinary Python rules and persisted only when consistent with profile data. Runtime dependencies remain empty, and no LLM, database, frontend, recommendation, gap analysis, Evidence Bank, or resume code was introduced.
- **Validation / Test Results:** `PYTHONPATH=src .venv/bin/python -c "import aarvia"` passed and reported `0.2.0`. A standard-library behavioral check passed for valid creation, deterministic questions, invalid and unknown field rejection, temporary-directory JSON saving/loading, and save/load equality. `git diff --check` passed. `PYTHONPATH=src .venv/bin/python -m pytest` could not start because the current `.venv` reports `No module named pytest`; no installation or network access was attempted. The pytest suite is present but could not be executed in this environment state.
- **Next Step:** Restore pytest in the existing development environment and run the committed suite. Begin Phase 2 only after an explicit request and a decision on the next product stage.

## 2026-08-28 - Phase 1B-v1 Interactive Career Discovery CLI

- **Date:** 2026-08-28
- **Phase:** Phase 1B-v1 - Interactive Career Discovery CLI
- **User Request:** Add an `aarvia discover` terminal questionnaire that creates or resumes a Career Profile, validates and saves each answer, supports `:skip` and `:quit`, handles multiple education, experience, and skill records, and remains fully deterministic.
- **What Codex Changed:** Added the `aarvia.cli:main` console entry point and `discover` subcommand; implemented an injectable interview workflow; added stable comma-list parsing, validation retries, progress-preserving interview drafts, custom profile paths, completion reporting, and Git ignores for private profile JSON; added 12 CLI/interview tests; updated the version to `0.3.0` and both README languages.
- **Important Decisions:** Reused all Phase 1A profile construction, validation, open-question generation, and JSON storage. Formal Profile JSON is always schema-valid. Partial multi-field records are stored atomically in an adjacent `*.interview.json` draft until they can be validated as a complete Phase 1A record. Existing Profile fields are never overwritten by the default discovery flow, and no input is inferred or parsed by an LLM.
- **Validation / Test Results:** `.venv/bin/python -m pytest` passed all 25 tests (13 existing and 12 new). `PYTHONPATH=src .venv/bin/python -c "import aarvia; print(aarvia.__version__)"` reported `0.3.0`. Root and discover help passed through `PYTHONPATH=src .venv/bin/python -m aarvia.cli --help` and `... discover --help`. A CLI smoke test used `TemporaryDirectory`, saved and reloaded a partial Profile, preserved `:quit` progress, and wrote no real repository profile. `git diff --check`, console-script metadata, `.gitkeep`, and private JSON ignore checks passed. The literal `.venv/bin/aarvia` command was not available because the environment's existing editable install still has old metadata and lacks the local build backend needed to refresh it without installation; no installation or network access was attempted.
- **Next Step:** Refresh the local editable installation, then run `aarvia discover`. Do not begin Phase 1B-v2 or Phase 2 without an explicit request.

## 2026-08-28 - Phase 1B-v2.1 Candidate Profile and Confirmation

- **Date:** 2026-08-28
- **Phase:** Phase 1B-v2.1 - Candidate Profile and Confirmation Workflow
- **User Request:** Accept a natural-language career narrative, extract a separate Candidate Profile through the official OpenAI SDK, validate it with Phase 1A, review each topic, and merge only explicitly accepted information into the confirmed CareerProfile.
- **What Codex Changed:** Added Candidate Profile and session state models, centralized OpenAI environment configuration, a replaceable Structured Outputs narrative extractor, topic-based review and correction, deterministic merging and conflict resolution, `discover --narrative`, `.env.example`, OpenAI as the sole new runtime dependency, 21 focused tests, version `0.4.0`, and bilingual documentation.
- **Important Decisions:** Candidate data remains in memory and separate from formal Profile storage. Every candidate passes existing Phase 1A validation. Accepted topics are saved independently; rejected and pending topics are not merged. Existing scalar values and matching records with different content require an explicit existing/candidate choice. Record and list deduplication is deterministic. The original questionnaire remains the default and is explicitly available with `--manual`. No Resume file import or adaptive follow-up was introduced.
- **Validation / Test Results:** `.venv/bin/python -m pytest` passed all 46 tests (25 existing and 21 new). `PYTHONPATH=src .venv/bin/python -c "import aarvia; print(aarvia.__version__)"` reported `0.4.0`. Root and discover help passed through the module entry point and displayed `--manual` and `--narrative`. A full fake-extractor CLI smoke test used `TemporaryDirectory`, accepted a skills topic, saved and reloaded the confirmed Profile, and made no API call. `git diff --check` passed. The installed console script was not refreshed because no dependency installation or network access was performed.
- **Next Step:** Install the updated local package and configure `OPENAI_API_KEY` and `AARVIA_OPENAI_MODEL` for personal testing. Do not begin Resume Import (Phase 1B-v2.2), adaptive questions, or Phase 2 without an explicit request.

## 2026-08-29 - OpenAI-Compatible Provider Configuration

- **Date:** 2026-08-29
- **Phase:** Phase 1B-v2.1 - Provider Configuration Update
- **User Request:** Make narrative extraction work with Alibaba Cloud Bailian and other OpenAI-compatible Responses API providers through generic environment variables, while retaining legacy OpenAI configuration compatibility and protecting secrets in errors and documentation.
- **What Codex Changed:** Replaced provider-specific settings with `LLMSettings`, added generic environment variable precedence and optional `base_url`, passed `base_url` into the official OpenAI client, added Bailian/custom-provider endpoint validation, mapped authentication/connection/model/schema failures to safe messages, updated `.env.example`, bumped the version to `0.4.1`, expanded both README languages, and added provider configuration tests.
- **Important Decisions:** OpenAI models may use the SDK default endpoint; Bailian and other compatible providers require an explicit endpoint. Legacy `OPENAI_API_KEY` and `AARVIA_OPENAI_MODEL` remain fallbacks. Raw provider exceptions are never displayed because they may contain credentials or private endpoints. Candidate, Confirmation, CareerProfile, and merge logic were not modified.
- **Validation / Test Results:** `.venv/bin/python -m pytest` passed all 59 tests (46 existing and 13 provider-focused tests). Tests used injected factories and fake Responses clients only; no real OpenAI or Bailian request was made. `PYTHONPATH=src .venv/bin/python -c "import aarvia; print(aarvia.__version__)"` reported `0.4.1`, and `git diff --check` passed. The environment's previously installed editable metadata still reports `0.4.0`; it was not refreshed because the environment lacks local setuptools/wheel and no dependency download was attempted.
- **Next Step:** Configure a region-matched Bailian key, workspace endpoint, and Responses-capable model for manual testing. Do not begin Resume Import or Phase 2 without an explicit request.

## 2026-08-29 - Candidate Alias Normalization

- **Date:** 2026-08-29
- **Phase:** Phase 1B-v2.1 - Candidate Schema Compatibility Fix
- **User Request:** Fix real Bailian output that used semantically correct top-level aliases (`projects`, `target_roles`, `target_locations`, and `target_employment_type`) instead of the strict Phase 1A nested schema, without weakening validation or changing the Candidate confirmation boundary.
- **What Codex Changed:** Added a deterministic Candidate normalization adapter before Candidate validation; mapped the four supported aliases into the existing Experience Overview, Career Preferences, and Constraints fields; strengthened extraction instructions; verified the Responses JSON Schema contains only canonical nested fields with `additionalProperties: false`; updated the version to `0.4.2`; rewrote both README files for clearer setup and product explanations; and added fixture-driven regression tests.
- **Important Decisions:** The adapter converts only documented aliases. Project records must otherwise use the existing Experience Overview fields, and the adapter adds only the semantically implied `experience_type=project`. Unsupported top-level or nested fields remain intact until strict Phase 1A validation rejects them. Ambiguous employment values and canonical/alias conflicts fail instead of being discarded or guessed. Candidate validation still occurs before confirmation or formal Profile writes.
- **Validation / Test Results:** `.venv/bin/python -m pytest` passed all 68 tests (59 existing and 9 new). Tests cover all four mappings, information preservation, absence of invented fields, canonical list merging, unknown-field rejection, failure isolation, schema structure, and a full fake-extractor confirmation into a temporary formal Profile. No real Bailian API was called. Source import reported `0.4.2`, and `git diff --check` passed in final validation.
- **Next Step:** Reinstall the local editable package and repeat the personal Bailian narrative test. Do not begin Resume Import or Phase 2 without an explicit request.

## 2026-08-29 - Empty Provider Field Cleanup

- **Date:** 2026-08-29
- **Phase:** Phase 1B-v2.1 - Candidate Schema Compatibility Fix
- **User Request:** Handle empty unknown provider fields such as `contact_info` without adding them to CareerProfile, while continuing to reject any unknown field that contains real information.
- **What Codex Changed:** Added deterministic recursive empty-value detection before alias normalization; removed only unknown top-level fields whose values are null, blank strings, empty lists/objects, or recursively empty collections; strengthened extraction instructions to prohibit `contact_info` and other out-of-schema fields; updated the version to `0.4.3`; clarified both README files and rewrote the Python API section as an optional developer example; and added regression tests.
- **Important Decisions:** Known CareerProfile fields and supported aliases are never removed by cleanup. Non-empty unknown fields remain untouched so Phase 1A strict validation rejects them. Cleanup runs before the existing four alias mappings, and Candidate validation still occurs before confirmation or formal Profile writes. CareerProfile and other phases were not modified.
- **Validation / Test Results:** `.venv/bin/python -m pytest` passed all 79 tests (68 existing plus 11 parameterized cleanup cases). Tests cover all safe empty shapes, nested empty values, non-empty `contact_info`, other unknown fields, alias normalization after cleanup, and formal Profile preservation on failure. The Responses schema contains no `contact_info` and retains `additionalProperties: false`. No real API was called; source import and `git diff --check` passed in final validation.
- **Next Step:** Reinstall the editable package and repeat the personal Bailian narrative test. Do not modify other phases or begin Resume Import without an explicit request.
