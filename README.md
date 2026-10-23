# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a local-first career navigation tool. It helps you turn confirmed
career facts into role recommendations, a reusable base resume, and a
traceable job-description match report.

**Current version: 0.22.0**

## What You Can Do

1. **Build a Career Profile** from a conversation, a UTF-8 text file, or a
   fully manual interview.
2. **Compare career directions** for Applied AI Engineer, Machine Learning
   Engineer, and Research Engineer. Every recommendation links back to your
   evidence and can be reviewed before it affects the result.
3. **Prepare a role-neutral base resume** from confirmed facts. Aarvia keeps
   contact details, structural information, evidence, and resume wording
   separate so that an unverified claim cannot quietly become a bullet.
4. **Match a local JD** against the same confirmed facts. The report separates
   supported requirements, partial support, missing evidence, explicit
   conflicts, and application tasks.
5. **Review a minimal tailoring proposal** for project/experience ordering and
   existing bullet selection. Suggestions and owner decisions are saved
   separately. Accepted ordering suggestions can then produce a new tailored
   JSON, Markdown resume, and change log without overwriting the Base Resume.

Aarvia does not treat a recommendation as a hiring probability. Missing
evidence also does not mean that you lack a skill.

## Quick Start

Install the project and configure an OpenAI-compatible Provider:

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
# Optional for OpenAI; required by many compatible Providers
export AARVIA_LLM_BASE_URL="https://your-provider.example/v1"
```

### 1. Create your Profile

```bash
# Interactive narrative
aarvia discover --narrative --profile profile.json

# Or use a local UTF-8 file
aarvia discover --narrative-file background.txt --profile profile.json

# No LLM
aarvia discover --manual --profile profile.json
```

Continue later with:

```bash
aarvia discover --follow-up --profile profile.json
```

### 2. Generate a recommendation

```bash
aarvia recommend --profile profile.json \
  --mapping-output mapping.json \
  --output recommendation.json
```

The first run asks the Provider to propose evidence links. Python validates
those links and owns the IDs, scoring, confidence, and ranking. Use
`aarvia review-evidence --help` to review ambiguous or provisional evidence,
then rerun `recommend` from the saved mapping without another Provider call.

### 3. Match a job description

```bash
aarvia match-jd --help
```

`match-jd` accepts a local UTF-8 JD, an explicit analysis date, and your saved
career files. It writes a structured JD analysis, a structured match
result, and a readable report without changing your base resume.

### 4. Review tailoring suggestions

```bash
aarvia propose-tailoring --help
aarvia review-tailoring-proposal --help
```

The first command derives traceable suggestions from the validated Base Resume
and JD Match. The second records `accepted`, `rejected`, or `deferred` decisions
in a separate artifact. Apply only accepted ordering decisions with:

```bash
aarvia apply-tailoring --help
```

This creates three new files and never changes the Base Resume. It cannot
rewrite bullets, add facts, delete content, or move content between sections.

## Command Map

| Goal | Command | Provider used? |
| --- | --- | --- |
| Build or continue a Career Profile | `aarvia discover` | Optional |
| Recommend career directions | `aarvia recommend` | First mapping only |
| Review evidence | `aarvia review-evidence` | No |
| Choose Primary and Secondary directions | `aarvia decide` | No |
| Analyze strengths, gaps, and unknowns | `aarvia analyze-gaps` | No |
| Build a reusable fact bank | `aarvia build-evidence-bank` | No |
| Add user-confirmed evidence details | `aarvia enrich-evidence` | No |
| Prepare resume-safe materials | `aarvia prepare-resume-materials` | No |
| Review resume wording | `aarvia review-resume-wording` | No |
| Add private contact and structural data | `aarvia supplement-resume-profile` | No |
| Assemble a role-neutral master resume | `aarvia build-base-resume` | No |
| Render the master resume as Markdown | `aarvia render-base-resume` | No |
| Match a local JD | `aarvia match-jd` | Yes |
| Propose minimal resume ordering/selection | `aarvia propose-tailoring` | No |
| Review tailoring suggestions | `aarvia review-tailoring-proposal` | No |
| Apply accepted ordering suggestions | `aarvia apply-tailoring` | No |

Run any command with `--help` for its required input paths. The long chain
is deliberate: each stage validates its inputs so stale or unreviewed facts do
not silently enter later outputs.

## How The Data Flows

```text
Career Profile
  -> evidence mapping and review
  -> role recommendation and user decision
  -> gap analysis and evidence bank
  -> reviewed resume material
  -> role-neutral base resume
  -> JD match report
  -> tailoring proposal and owner review
```

The files are separate on purpose. Model suggestions are not facts, a gap
is not resume evidence, and resume wording cannot increase evidence strength.

## Important Limits

- A real model can still misunderstand a JD condition, attribute a fact to the
  wrong source, or make an unsupported inference. **Review every JD report.**
- The instruction variants under `scripts/` are offline, non-production
  experiments. They have not replaced the production prompt.
- Aarvia applies accepted ordering suggestions only. It does not rewrite
  bullets, perform automated semantic fact checking, provide a unified PDF
  export workflow, or automate applications.
- One private real-JD workflow has completed a human-assisted proposal,
  confirmation, resume-output, and fact-review cycle. That is usage validation,
  not an automated Tailoring feature.
- The production Role Catalog still has 8 role families and no published
  requirements or sources. The recommendation rubric is usable, but the market
  dataset is not presented as complete.

## Privacy And Safety

- Facts are saved only after confirmation.
- Rejected evidence never affects scoring; ambiguous evidence contributes
  nothing until reviewed.
- Profile and review files are written atomically.
- Diagnostics contain safe metadata, not API keys, raw Provider responses, or
  Profile excerpts.
- Keep personal data under an ignored path such as `local_data/`. Never commit
  `.env`, Profiles, resumes, or Provider responses.

Private contact data belongs in Resume Profile Supplement, not in the public
Career Profile or logs. Supplement data can add structure, but it cannot turn
an uncovered experience into an achievement.

## Alibaba Cloud Bailian

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

Use a key, endpoint, and model from the same region. Aarvia uses Chat
Completions for Bailian and keeps Provider-specific parameters away from
OpenAI endpoints.

## Project Status

- Career Profile and adaptive discovery: complete
- Explainable role recommendation and evidence review: complete
- Gap Analysis, Evidence Bank, and base-resume foundations: complete
- Traceable local JD matching: implemented; human semantic review required
- Traceable tailoring proposal and owner review: implemented
- Applying accepted ordering suggestions: implemented
- Free rewriting, semantic fact checking, and applications: not implemented

The next product step is reviewed wording changes with the same fact and
provenance boundaries.

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

Engineering history lives in the
[sanitized phase summaries](docs/conversation-log.md) and
[development log](docs/codex-log.md).
