# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a Career Navigation + Job Application Agent. It starts with the person, not the job description: what you have actually done, what kind of work you want, and what constraints shape your search.

It is **not** a resume slot machine. A new JD should not produce a newly invented person. 🎰🚫

## What Works Today

Version `0.4.7` covers the Career Profile and discovery foundation:

- 🧱 Strict CareerProfile models for education, experience, skills, preferences, and constraints
- 💾 Validated JSON save/load
- 💬 Manual and natural-language terminal discovery
- ✅ Candidate review before anything enters the formal Profile
- 🔍 Safe extraction diagnostics for compatible providers
- ☁️ OpenAI Responses and Bailian Chat Completions Structured Outputs
- 📄 UTF-8 narrative files for longer backgrounds

Resume import, adaptive follow-up questions, role recommendations, gap analysis, and resume tailoring are **not implemented yet**.

## Quick Start 🚀

```bash
python -m pip install .
aarvia --version
```

Configure an OpenAI-compatible provider:

```bash
export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-structured-output-model"
```

For a custom provider, also set its endpoint:

```bash
export AARVIA_LLM_BASE_URL="https://your-provider.example/compatible-mode/v1"
```

Then choose how to tell Aarvia about yourself.

### Talk in the terminal

```bash
aarvia discover --narrative
```

### Use a longer text file

```bash
aarvia discover \
  --narrative-file background.txt \
  --profile data/profiles/example.json
```

Narrative files must be UTF-8 plain text, non-empty, and no larger than 2 MiB. PDF and DOCX import are not part of this phase.

### Use the manual fallback

```bash
aarvia discover --manual
```

Use `:skip` to leave a question unanswered and `:quit` to save your progress.

## Bailian Setup ☁️

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

Replace `{WorkspaceId}` with your own workspace ID. Keys, endpoints, and models must belong to the same region. The selected model must support Chat Completions JSON Schema Structured Outputs; Aarvia does not assume every Bailian model does.

Aarvia detects `dashscope.aliyuncs.com` and `*.maas.aliyuncs.com` by parsed host, uses Chat Completions with strict `json_schema`, and disables thinking. Other endpoints retain the Responses protocol. Both paths use the same Candidate schema with `additionalProperties: false`.

## What Happens to Your Answer? 🔐

```text
Your narrative
-> Provider extraction
-> Deterministic cleanup and normalization
-> Strict Candidate validation
-> Your confirmation
-> Confirmed Career Profile
```

Unknown dates stay `null`. Aarvia never guesses them. Exact provider placeholders are removed only from the five formal date fields; other invalid values still fail validation.

Profiles under `data/profiles/` are ignored by Git. Never commit API keys, workspace IDs, private endpoints, or personal Profile JSON.

## Debug a Provider 🔦

```bash
aarvia discover --narrative --debug-extraction
```

On failure, debug mode shows the version, model, endpoint host, protocol, schema mode, pipeline stage, JSON status, validation reason, and raw provider text. It redacts the configured API key and saves none of that raw output.

Raw output may contain personal information, so use debug mode only in a private terminal.

## Product Direction

```text
User Profile -> Career Discovery -> Role Recommendation -> User Decision
-> Gap Analysis -> Evidence Bank -> Base Resume -> JD Matching
-> Minimal Tailoring -> Fact Checking -> Final Resume
```

Current status:

- ✅ Phase 1A — Career Profile Foundation
- ✅ Phase 1B-v1 — Manual Career Discovery CLI
- ✅ Phase 1B-v2.1 — Natural-language Candidate and Confirmation workflow
- ⏳ Resume import, adaptive discovery, and Phase 2

## Design Principles

- The current resume does not determine the user's direction.
- The user keeps final control over career decisions.
- Profile and resume claims must be grounded in real evidence.
- Extracted information remains a Candidate until confirmed.
- Tailoring should be minimal and traceable.
- Agent frameworks should appear only when genuinely needed.
- Core logic must remain testable without an LLM.

## Development 🧪

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
