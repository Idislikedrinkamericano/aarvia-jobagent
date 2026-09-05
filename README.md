# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a **Career Navigation + Job Application Agent**. It learns your real experience, interests, goals, and constraints before discussing jobs.

It is not a resume slot machine. A new JD should not generate a newly invented person. 🎰🚫

## Status

**Version 0.6.3 — Phase 2B-1 curation lineage contract complete.**

- ✅ Phase 1: confirmed Career Profile and adaptive Career Discovery
- ✅ Phase 2A: versioned Role Catalog and shared data contracts
- ✅ Eight stable Role Families with specializations and search aliases
- ✅ Atomic JSON persistence for Phase 2A recommendation, decision, gap, and job artifacts
- ✅ Schema 2 contracts for US internship, new-grad, and 0-2 year JD sources
- ✅ Tier A/B/C provenance, canonical dedup, human curation, and prevalence foundations
- ✅ Machine-readable approve, reject, revise, and split lineage for requirement candidates
- ⏳ Source-backed Role Requirements, recommendations, gaps, and live jobs are not implemented

The production Catalog deliberately contains **zero requirements and zero sources** today. It is a taxonomy with guardrails, not a trench coat full of invented labor-market facts. 🕵️

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
Career Profile → Career Discovery → Role Recommendation → Live Job Examples
→ User Decision → Role-level Gap Analysis → Evidence Bank → Base Resume
→ Live Job Discovery → Detailed JD Matching
→ Minimal Tailoring → Fact Checking → Final Resume
```

## Phase 2 Map

- **2A — Contracts:** complete. Role taxonomy, provenance, recommendation, decision, gap, and live-job schemas are validated and serializable.
- **2B — Role Recommendation:** 2B-1 source/curation foundation is complete; recommendation ranking is not implemented.
- **2C — User Decision:** not implemented. Recommendation and confirmed user choice remain separate objects.
- **2D — Role-level Gap Analysis:** not implemented. Its contract preserves `unknown != missing`.
- **2E — Live Job Discovery:** not implemented. This is where official pages and listing status will be checked.
- **2F — Preliminary Job Matching:** not implemented. It will describe JD requirement coverage, not interview or offer probability.
- **2G — End-to-end hardening:** not implemented.

**Role Fit and Job Fit are different.** A Role Family can be a sensible direction while a particular job has an eligibility conflict. Detailed JD Matching and resume work still happen after the Evidence Bank and Base Resume stages.

## Phase 2A Safety Boundary

- Every production requirement must cite a known provenance source.
- The `1.0.0` production taxonomy is loaded from packaged `catalog_data/role-catalog-1.0.0.json`, not duplicated in Python.
- An unsourced requirement cannot claim `common` or `frequent` prevalence.
- Test fixture sources cannot enter a production Catalog or job collection.
- Recommendations may reference only existing Role, Requirement, and confirmed Profile facts.
- A Decision that cites a RecommendationSet must validate that set against the same Catalog and exact CareerProfile snapshot.
- Every saved or loaded Gap Analysis requires a confirmed User Decision and may cover only its Primary or Secondary roles.
- Profile references store a field path, exact value snapshot, and deterministic Profile fingerprint.
- List paths currently use indexes, so a reference belongs to one exact Profile snapshot. Stable local entry IDs require a future migration; Providers never create them.
- `verified_open` requires a specific official job posting; a general careers page can support only `possibly_open`.
- An application URL being present is separate from being verified. Verification has its own status, timestamp, and source reference.
- A live job requires an official source contract, but no real job discovery or verification occurs in 2A.

Before Phase 2B can rank roles, Aarvia still needs a reviewed dataset of real Role Requirements backed by traceable official job sources. Empty requirements are not market evidence.

### Phase 2B-1 Source Foundation

- The first market scope is United States internship, new-grad, and early-career work with explicitly stated experience capped at two years.
- Tier A means an official company or ATS source. Tier B means a verified hiring-platform posting; LinkedIn is a platform source, never an official company page. Tier C is discovery-only.
- Official and platform verified-open states are distinct. Careers homepages and unverified discovery sources can support only `possibly_open`.
- Live Job schema 2 keeps application-link presence separate from verification status, timestamp, and source. A specific qualified posting must support a verified application link; URL syntax alone proves nothing.
- LLM output can create only unapproved `RequirementCandidate` objects. Python generates IDs, hashes, exact deduplication, source-mix checks, and prevalence; a human approves normalization, importance, evidence, and publication.
- Curation schema 3 records immutable human review events. Revised and split candidates retain deterministic parent/successor lineage, source hashes, evidence bounds, and Role mapping. Schema 2 remains explicitly readable and round-trippable, but cannot publish through the Catalog builder.
- Catalog drafts consume only approved leaf candidates. Split evidence from one company still counts as one company sample.
- Full JD text and Pilot artifacts belong only under ignored `local_data/`; this contract fix does not modify them.
- The production Catalog remains `1.0.0`: eight roles, zero requirements, and zero sources. Phase 2B completion, including recommendation behavior, will use project version `0.7.0`.

## Design Principles

- User direction is not determined only by the current resume.
- The user keeps final control over career direction.
- Resume claims must be grounded in real evidence.
- Tailoring should be minimal and traceable.
- Complex agent frameworks should appear only when actually needed.
- Core logic must be testable independently from the LLM.

## Development

Project history: [sanitized public conversation summaries by phase](docs/conversation-log.md) and [development log](docs/codex-log.md). Verbatim personal transcripts are not tracked by Git.

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
