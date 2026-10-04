# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a career navigation agent that learns who you actually are before suggesting where to go next.

It builds a confirmed Career Profile, asks focused follow-up questions, and turns your evidence into explainable role recommendations. A new job description should not magically create a new version of you. 🎰🚫

## Where It Stands

**Version 0.11.0**

Ready today:

- Career discovery from a narrative, a text file, or a guided interview
- One-question-at-a-time follow-up for missing information
- Strict confirmation and correction before Profile facts are saved
- Current Fit and Directional Fit for Applied AI Engineer, Machine Learning Engineer, and Research Engineer
- Reusable evidence Mapping plus separate allocation and evidence reviews
- One global role ranking: Core-supported roles first, then Extended-only roles
- OpenAI-compatible Providers, including Alibaba Cloud Bailian
- Atomic JSON storage, deterministic validation, and privacy-safe diagnostics

Not ready yet:

- Final User Decision and Gap Analysis
- Live job discovery and detailed JD matching
- Evidence Bank, resume tailoring, or application automation

The production Role Catalog still contains **8 role families, 0 published requirements, and 0 sources**. Aarvia has a real recommendation rubric, but it does not pretend an unfinished market dataset is complete.

## Quick Start

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
# Optional for OpenAI; required for another compatible Provider
export AARVIA_LLM_BASE_URL="https://your-provider.example/v1"
```

Create a Profile:

```bash
# Tell your story interactively
aarvia discover --narrative --profile profile.json

# Use a longer UTF-8 text file
aarvia discover --narrative-file background.txt --profile profile.json

# Continue an existing Profile, one missing topic at a time
aarvia discover --follow-up --profile profile.json

# Fully manual, no LLM
aarvia discover --manual --profile profile.json
```

Generate and review role recommendations:

```bash
# First run: Provider proposes evidence bindings; Python validates and scores them
aarvia recommend --profile profile.json \
  --mapping-output mapping.json \
  --output recommendation.json

# Review ambiguous allocation first, then provisional semantic evidence
aarvia review-evidence --profile profile.json \
  --mapping mapping.json \
  --allocation-output allocation-review.json \
  --output evidence-review.json

# Recompute from saved artifacts; this does not call the Provider
aarvia recommend --profile profile.json \
  --mapping-artifact mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --output reviewed-recommendation.json
```

Role recommendations are evidence coverage assessments, **not probabilities of getting an interview or offer**.

## Follow-Up Controls

- `.done` submits a multiline answer.
- `.cancel` discards the current answer and asks again.
- `:none` means there is nothing to report.
- `:skip` means ask again in a future session.
- `:decline` means the user chose not to answer.
- `:finish` reviews the session; `q` cancels it without saving.

If nothing is missing or changed, Aarvia exits without calling the Provider or rewriting files. Doing nothing is sometimes the correct feature.

## Bailian

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

Use a key, endpoint, and model from the same region. Aarvia uses Chat Completions for Bailian and keeps Provider-specific parameters away from OpenAI endpoints.

## Safety Promises 🔒

- Extracted facts remain temporary until the user confirms them.
- The Provider may propose evidence links; Python owns IDs, validation, allocation, status, score, confidence, and rank.
- Profile references must point to real fields and exact evidence in the current Profile snapshot.
- Ambiguous evidence contributes nothing until reviewed; rejected evidence never influences scoring.
- Corrections and reviews cannot silently overwrite unrelated facts.
- Profile and review files are written atomically.
- Opt-in diagnostics store safe metadata, not raw Provider responses, API keys, or Profile excerpts.

Keep personal Profiles and diagnostics under ignored local paths such as `local_data/`. Do not commit secrets.

## Roadmap

- **Phase 1:** Career Profile and adaptive discovery — complete
- **Phase 2A:** shared contracts and Role Catalog foundation — complete
- **Phase 2B:** US early-career market evidence and curation foundation — complete, publication dataset still unfinished
- **Phase 2C:** explainable role recommendation and evidence review — in progress
- **Phase 2D–2G:** decision, gap analysis, live jobs, matching, and end-to-end hardening — not implemented

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

Engineering history lives in the [sanitized phase summaries](docs/conversation-log.md) and [development log](docs/codex-log.md). They are intentionally separate from this README so the front door stays readable.
