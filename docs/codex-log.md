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
