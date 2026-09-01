# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a **Career Navigation + Job Application Agent**. It learns your real experience, interests, goals, and constraints before discussing jobs.

It is not a resume slot machine. A new JD should not generate a newly invented person. 🎰🚫

## Status

**Version 0.5.7 — Phase 1 complete.**

- ✅ Structured Career Profile with validated JSON storage
- ✅ Manual, narrative, and adaptive follow-up CLI
- ✅ Candidate review, correction, and evidence checks
- ✅ Atomic Profile + Discovery State saving
- ⏳ Resume import and Phase 2 are not implemented

## How It Works

```text
Tell Aarvia about yourself
→ Extract candidate facts
→ Review or correct them
→ Save only confirmed information
→ Ask one useful follow-up at a time
```

No guessed dates. No invented accomplishments. No silent career decisions. Aarvia has a pleasantly low tolerance for fiction.

## Quick Start

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
export AARVIA_LLM_BASE_URL="https://your-provider.example/compatible-mode/v1"

aarvia discover --narrative
```

OpenAI users may omit `AARVIA_LLM_BASE_URL`.

Other ways to begin or continue:

```bash
# Longer UTF-8 text, up to 2 MiB
aarvia discover --narrative-file background.txt \
  --profile data/profiles/example.json

# Continue an existing Profile
aarvia discover --follow-up \
  --profile data/profiles/example.json

# No LLM required
aarvia discover --manual
```

Follow-up commands:

- `.done` submits multiline input; `.cancel` restarts the current answer.
- `:none`, `:skip`, and `:decline` record different kinds of “not answered.”
- `:finish` reviews this session; `q` cancels it.

If the Profile is already complete, Aarvia exits without calling the Provider or rewriting files. Knowing when to do nothing is a feature.

## Bailian

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

Replace `{WorkspaceId}`. The key, endpoint, and model must share a region, and the model must support Chat Completions JSON Schema Structured Outputs. Not every Bailian model does.

## Safety First

- Provider output is cleaned deterministically and validated against a strict schema.
- Extracted facts stay temporary until the user confirms them.
- Corrections show a before/after diff and reject unsupported changes.
- Existing records use deterministic identity matching; unmatched records need approval.
- Session drafts are saved atomically only after final confirmation.
- Debug output stays in the terminal and redacts the configured API key.

Personal Profiles under `data/profiles/` and API keys should never be committed.

## Product Pipeline

```text
User Profile → Career Discovery → Role Recommendation → User Decision
→ Gap Analysis → Evidence Bank → Base Resume → JD Matching
→ Minimal Tailoring → Fact Checking → Final Resume
```

Only Profile and Career Discovery exist today. Aarvia is not quietly doing Phase 2 behind the curtains.

## Design Principles

- User direction is not determined only by the current resume.
- The user keeps final control over career direction.
- Resume claims must be grounded in real evidence.
- Tailoring should be minimal and traceable.
- Complex agent frameworks should appear only when actually needed.
- Core logic must be testable independently from the LLM.

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
