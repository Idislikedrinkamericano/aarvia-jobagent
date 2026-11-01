# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a local-first career navigation tool. It helps you turn confirmed
career facts into role recommendations, a reusable base resume, and a
traceable job-description match report.

**Current version: 0.25.0**

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
6. **Review optional JD-targeted bullet rewrites.** The Provider may suggest a
   rewrite of an existing bullet, but only the profile owner can accept it.
   Accepted wording is applied to a new resume; the master wording is unchanged.
7. **Fact-check the final resume bullet by bullet.** Python verifies provenance,
   membership, references, and accepted wording; the profile owner separately
   records whether each final statement passes semantic review.
8. **Export the reviewed final resume to PDF.** The local renderer preserves the
   validated order and wording, supports natural Letter-page pagination, and
   never calls a Provider.

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

For the separate, human-reviewed wording step:

```bash
aarvia propose-tailored-wording --help
aarvia review-tailored-wording --help
aarvia apply-tailored-wording --help
```

The Provider sees the JD, eligible existing bullets, and their verified fact
references—but never Contact. Python validates IDs and provenance; the user
must still judge whether a rewrite stays faithful to those facts. Unchanged
Provider candidates are counted and omitted after the complete response passes
strict validation, so a valid run may contain no rewrite suggestions.

### 5. Fact-check the final resume

```bash
aarvia fact-check-resume --help
```

The command supports an ordering-only Tailored Resume or an optional reviewed
wording overlay. It writes a typed check artifact and a Contact-free readable
report. It never edits the resume and never calls a Provider.

### 6. Export the reviewed final resume

```bash
aarvia export-final-resume-pdf --help
```

PDF export requires the matching Final Resume Fact Check with every bullet
marked `passed`. It rebuilds content from typed artifacts instead of parsing
Markdown. The fact check covers bullets only; it does not certify Contact,
Education, Skills, or the resume as a whole. ReportLab is installed with
Aarvia; contributors get the `pypdf` extraction checks from `.[dev]`.

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
| Propose optional bullet rewrites | `aarvia propose-tailored-wording` | Yes |
| Review proposed rewrites | `aarvia review-tailored-wording` | No |
| Apply accepted rewrites | `aarvia apply-tailored-wording` | No |
| Review every final bullet against verified facts | `aarvia fact-check-resume` | No |
| Export a validated final resume to PDF | `aarvia export-final-resume-pdf` | No |

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
  -> ordering proposal and owner review
  -> optional wording proposal and owner review
  -> final resume fact check and owner semantic review
  -> validated PDF export
```

The files are separate on purpose. Model suggestions are not facts, a gap
is not resume evidence, and resume wording cannot increase evidence strength.

## Important Limits

- A real model can still misunderstand a JD condition, attribute a fact to the
  wrong source, or make an unsupported inference. **Review every JD report.**
- The instruction variants under `scripts/` are offline, non-production
  experiments. They have not replaced the production prompt.
- Aarvia can apply owner-accepted ordering and wording candidates and export a
  passed final resume to a local PDF. PDF export preserves validated content;
  it does not prove that Provider wording is semantically faithful or automate
  applications.
- One private real-JD workflow has completed a human-assisted proposal,
  confirmation, resume-output, bullet fact-review, and PDF-export cycle. This is
  the minimum human-reviewed Phase 3 loop, not automated Tailoring, automatic
  semantic fact judgment, application automation, or general model-quality
  acceptance. In that wording run, every proposed rewrite was rejected and the
  reviewed source text was preserved; applying an accepted real-model rewrite
  still needs real-world acceptance. The formal fact review covered four bullets
  only, not the rest of the resume.
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
- Human-reviewed JD-targeted rewrites: implemented
- Traceable final-resume fact checking: implemented; semantic decision remains human
- Validated final-resume PDF export: implemented for ordering-only and wording-overlay outputs
- Free rewriting, automated semantic judgment, and applications: not implemented

The next product step is broader real-world acceptance of this deliberately
narrow, reviewed tailoring loop.

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

Engineering history lives in the
[sanitized phase summaries](docs/conversation-log.md) and
[development log](docs/codex-log.md).
