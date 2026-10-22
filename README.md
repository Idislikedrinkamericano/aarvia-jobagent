# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a career navigation agent that learns who you actually are before suggesting where to go next.

It builds a confirmed Career Profile, asks focused follow-up questions, and turns your evidence into explainable role recommendations. A new job description should not magically create a new version of you. 🎰🚫

## Where It Stands

**Version 0.20.6**

Ready today:

- Career discovery from a narrative, a text file, or a guided interview
- One-question-at-a-time follow-up for missing information
- Strict confirmation and correction before Profile facts are saved
- Current Fit and Directional Fit for Applied AI Engineer, Machine Learning Engineer, and Research Engineer
- Reusable evidence Mapping plus separate allocation and evidence reviews
- One global role ranking: Core-supported roles first, then Extended-only roles
- A confirmed or deferred Career Direction Decision, saved separately from the recommendation
- Deterministic Gap Analysis for the confirmed Primary and Secondary directions
- A deterministic Evidence Bank that separates verified facts, capability links, and open needs
- User-confirmed Evidence Enrichment that preserves exact wording and never rewrites the Evidence Bank
- Deterministic Resume Material with an honest coverage report
- User-authored Resume wording review with source-linked bullets and structural entries
- A private Resume Profile Supplement for confirmed contact and uncovered structural Profile facts
- A role-neutral master Base Resume with explicit omissions, private layout sessions, and fact-preserving deterministic Markdown
- Local UTF-8 JD decomposition and source-linked matching against confirmed Phase 2 facts
- Conservative JD support semantics: illustrative examples are not extra requirements, and related background alone is not partial support
- OpenAI-compatible Providers, including Alibaba Cloud Bailian
- Atomic JSON storage, deterministic validation, and privacy-safe diagnostics

Not ready yet:

- Live job discovery
- Resume tailoring or application automation

The Supplement is private local data: contact values and their content-derived IDs must not be committed to a public repository. It adds structure only; it cannot turn uncovered experience or skills into achievements.

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

# Choose a direction without calling the Provider
aarvia decide --profile profile.json \
  --recommendation reviewed-recommendation.json \
  --output career-decision.json

# Analyze only the confirmed Primary and Secondary directions; no Provider call
aarvia analyze-gaps --profile profile.json \
  --mapping mapping.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --output gap-analysis.json

# Build a reusable fact bank from the validated chain; no Provider call
aarvia build-evidence-bank --profile profile.json \
  --mapping mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --gap-analysis gap-analysis.json \
  --output evidence-bank.json

# Add and review your own missing details; no Provider call
aarvia enrich-evidence --profile profile.json \
  --mapping mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --gap-analysis gap-analysis.json \
  --evidence-bank evidence-bank.json \
  --output evidence-enrichment.json
```

Evidence references accept one or more comma-separated numbers, such as `1, 2`.
During enrichment, use `b` to go back; Claims can be edited or deleted before the final save.

Prepare source-linked, role-neutral Resume material with `aarvia prepare-resume-materials --help`.
It is not a finished resume; uncovered Profile records and contact fields stay visible.

Turn those materials into user-authored, reviewed wording with
`aarvia review-resume-wording --help`. Materials may be combined only within one
source, and this stage still does not render a finished resume or call a Provider.

Use `aarvia supplement-resume-profile --help` to review private contact details and
uncovered Education, Skills, and Experience context without changing the Career Profile.
Record sections accept `all`, lists, and ranges for one-step review. Use `p` to save a
private session draft, then resume it explicitly with `--resume-session`.

Assemble the reviewed facts without changing them using
`aarvia build-base-resume --help`. The command can include, exclude, restore, and
reorder content; `p` saves an explicit private session. Then use
`aarvia render-base-resume --help` to render deterministic Markdown after the
entire provenance chain is validated again. The result is a role-neutral master
resume, not a one-page or job-tailored resume.

Match a local UTF-8 job description without changing the Base Resume:

```bash
aarvia match-jd --help
```

`match-jd` saves the original JD analysis, a structured match artifact, and a
readable report. It distinguishes verified support, partial support, missing
evidence, and a fact-backed mismatch. Missing evidence is not treated as lack
of ability, and the report is not an interview or offer probability. Fact IDs
must come from the request inventory; one invalid Provider response may be
corrected once before the command stops without writing outputs.

Strict source and contract validation does not make unrestricted semantic
judgment deterministic. Real-model use has shown that a model can still
misread a condition, attribute a fact to the wrong source, or make an
unsupported inference, so every match report requires human review. The local
B/C instruction variants under `scripts/` are non-production experiments and
have not replaced the production prompt.

Pass `--analysis-date YYYY-MM-DD` explicitly. JD Analysis schema 3 keeps that
date separate from internship dates and deadlines. Application deadlines and
required documents remain procedural actions rather than capability gaps.
The Provider classifies evaluation mode, AND/OR structure, and support scope;
Python derives the final match status and rejects inconsistent combinations.
Each fact also carries minimal, non-contact source context, while every JD
requirement records its source section, logical operator, and exact condition
clauses. Source context explains provenance and does not add capability evidence.
Provider output must be a data instance with exactly one top-level
`requirements` key; schema-shaped wrappers and overlapping condition clauses
are rejected by the same contract used for production matching.

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
- **Phase 2C:** explainable recommendation, evidence review, and User Decision — complete
- **Phase 2D:** Gap Analysis, Evidence Bank, enrichment, Resume Material, and wording review foundations — complete
- **Phase 3A:** local JD decomposition and traceable matching — implemented; contract-tested and exercised on a private real JD, with human semantic review still required
- **Phase 3B+:** the private real-JD workflow has completed one human-assisted proposal, confirmation, resume-output, and fact-check cycle; automated tailoring, automated semantic fact checking, a unified PDF export workflow, live discovery, and application automation are not implemented

The next smallest product step is a typed, reviewable flow from tailoring
proposal to user confirmation and resume output. The completed private cycle
is usage evidence for that flow, not an automated Tailoring implementation.

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

Engineering history lives in the [sanitized phase summaries](docs/conversation-log.md) and [development log](docs/codex-log.md). They are intentionally separate from this README so the front door stays readable.
