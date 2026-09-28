# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia is a **Career Navigation + Job Application Agent**. It learns your real experience, interests, goals, and constraints before discussing jobs.

It is not a resume slot machine. A new JD should not generate a newly invented person. 🎰🚫

## Status

**Version 0.9.0 — Evidence-aware recommendations with reusable human review.**

- ✅ Phase 1: confirmed Career Profile and adaptive Career Discovery
- ✅ Phase 2A: versioned Role Catalog and shared data contracts
- ✅ Eight stable Role Families with specializations and search aliases
- ✅ Atomic JSON persistence for Phase 2A recommendation, decision, gap, and job artifacts
- ✅ Versioned logical sources and immutable JD captures for US early-career evidence
- ✅ Tier A/B/C provenance, canonical dedup, human curation, and prevalence foundations
- ✅ Machine-readable approve, reject, revise, and split lineage for requirement candidates
- ✅ Human-confirmed Role Assignment is the authority for each canonical job's Role Family
- ✅ Non-recursive `all_of` / `any_of` Candidate logic with deterministic review provenance
- ✅ Independent Candidate review, controlled Cluster review, and explicit Logic Group resolution
- ✅ Capability Rubric schema 2: 20 dimensions with stable criteria and typed Evidence Support Policies
- ✅ Deterministic Current Fit, Directional Fit, constraints, confidence, ties, and follow-ups
- ✅ Mapping schema 3 bindings, Recommendation schema 4 scoring, and typed evidence review provenance
- ✅ Bailian/custom JSON mode, bounded repair retry, and opt-in metadata-only diagnostics
- ⏳ User Decision, Gap Analysis, live-job discovery, and resume work are not implemented

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

# First recommendation: save Mapping 3 and Recommendation 4 together
aarvia recommend --profile data/profiles/example.json \
  --mapping-output mapping.json \
  --output recommendation.json

# Review provisional experience/project evidence without calling the Provider
aarvia review-evidence --profile data/profiles/example.json \
  --mapping mapping.json \
  --output evidence-review.json

# Recompute from the exact same Mapping and the saved Review; Provider is not called
aarvia recommend --profile data/profiles/example.json \
  --mapping-artifact mapping.json \
  --review-artifact evidence-review.json \
  --output reviewed-recommendation.json

# Explicit legacy offline workflow (Mapping schema 1/2 only)
aarvia recommend --profile data/profiles/example.json \
  --mapping-candidates mapping-candidates.json \
  --output legacy-recommendation.json
```

Without a Mapping input, `aarvia recommend` uses the configured OpenAI-compatible Provider only to propose Mapping schema 3 criterion bindings, then atomically saves the Mapping and Recommendation schema 4. Python validates references and calculates every status, score, band, confidence result, tie, and rank. `--mapping-artifact` always reuses an existing Mapping without calling the Provider. A Review is valid only for the exact Profile, Rubric, Mapping, and binding identities it was created from.

Provider diagnostics are opt-in and metadata-only:

```bash
aarvia recommend --profile data/profiles/example.json \
  --provider-diagnostics-dir local_data/provider_diagnostics
```

These files may describe failures derived from private Profile data. Keep them under ignored `local_data/`. Aarvia records hashes, lengths, protocol, JSON mode, parser errors, and fallback reasons; it does not save the raw Provider response, API key, headers, or environment variables.

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

Replace `{WorkspaceId}`. The key, endpoint, and model must share a region. Recommendation mapping uses Chat Completions JSON object mode plus a strict schema prompt. If a compatible endpoint explicitly rejects JSON mode, Aarvia records the reason and retries that request in strict prompt-only mode. Malformed JSON is never repaired locally; it receives one bounded full-task retry and then fails safely.

Profile references are selected from a deterministic list of canonical leaf paths. The `career_profile` request key is only a transport envelope; one accidental leading `career_profile.` prefix is removed and the resulting path, value snapshot, and Profile fingerprint are then validated again. No other path alias is accepted.

User-entered target-role names are preferences, not Rubric identifiers. Every Provider `role_id` is constrained to the current Rubric's canonical enum; an unknown value gets one explicit full-response retry and is then isolated without fuzzy or automatic remapping.

One bad candidate no longer poisons an otherwise usable response. Aarvia retries once with the exact rule, then isolates any still-invalid candidate with a structured reason code. The affected capability stays `unknown`; it is never rewritten as evidence or silently moved into Directional Fit. Any rejection caps recommendation confidence below High, a rejection ratio of at least 50% or rejection of a ready scoring dimension caps it at Low, and an entirely rejected response produces Insufficient confidence with a `provider_mapping_insufficient` blocker. These thresholds are deterministic Python constants.

The retry is not automatically trusted. Aarvia validates both complete responses independently and uses attempt two only when it strictly lowers both rejection count and ratio without losing an accepted Candidate, canonical Role, or Current Fit Dimension, and without increasing Candidate volume. A tie or regression keeps attempt one; responses are never merged. Diagnostics record only the selected attempt and a safe reason code.

Atomic-evidence validation emits structured source-level codes for invalid excerpts, token boundaries, duplicate or overlapping evidence, cross-dimension reuse, and invalid status/inference relationships. Classification never depends on human-readable exception wording. Diagnostics and persisted warnings contain only the category and safe identifiers, never the excerpt or Profile value.

The same structured boundary covers Current Fit field types, evidence strength, Provider confidence, review flags, contribution relationships, deterministic provenance, duplicate mappings, and aggregate contribution caps. Only genuinely unknown legacy failures use the generic rejection category.

Current Fit evidence is span-level, not just field-level. Mapping schema 3 lets the Provider propose only a canonical Role, Dimension, criterion, span, binding type, and confidence. Python materializes the span, identifies its structural or behavioral evidence class, applies the Rubric policy, generates stable provenance, and derives a conservative status, strength, inference, and review requirement. Recommendation schema 4 aggregates only those derived bindings and deterministically recomputes coverage, confidence, ranking, ties, blockers, and follow-ups. A separate typed review artifact can confirm, reject, or defer an existing binding while remaining bound to the exact Profile, Rubric, and Mapping artifact. Confirmation still does not guarantee `demonstrated`: policy thresholds, criterion coverage, independent evidence, and behavior evidence remain mandatory. Structural evidence never becomes semantic evidence through review. Version `0.9.0` makes this the default `recommend` flow while keeping legacy schemas behind the explicit `--mapping-candidates` option.

## Safety First

- Provider output is cleaned deterministically and validated against a strict schema.
- Provider Profile references must use real canonical leaf paths and exact Profile values.
- Structurally invalid responses fail as a whole; isolated semantic candidate errors remain auditable and cannot influence scores.
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
- **2B — Market evidence and curation:** source, capture, assignment, candidate, logic, and review foundations are complete.
- **2C — Role Recommendation:** implemented for Applied AI Engineer, Machine Learning Engineer, and Research Engineer. Current Fit and Directional Fit remain separate.
- **2D–2G:** User Decision, role-level Gap Analysis, live-job workflows, matching, and end-to-end hardening remain future work.

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

The packaged Capability Rubric is separate from the Production Role Catalog. It provides reviewed aggregate dimensions for recommendation, while the Catalog deliberately remains at zero published requirements and sources.

### Phase 2B-1 Source Foundation

- The first market scope is United States internship, new-grad, and early-career work with explicitly stated experience capped at two years.
- Tier A means an official company or ATS source. Tier B means a verified hiring-platform posting; LinkedIn is a platform source, never an official company page. Tier C is discovery-only.
- Official and platform verified-open states are distinct. Careers homepages and unverified discovery sources can support only `possibly_open`.
- Live Job schemas 2 and 3 keep application-link presence separate from verification. Schema 3 additionally binds that verification to an immutable capture; URL syntax alone proves nothing.
- LLM output can create only unapproved `RequirementCandidate` objects. Python generates IDs, hashes, exact deduplication, source-mix checks, and prevalence; a human approves normalization, importance, evidence, and publication.
- Source schema 3 separates stable job identity from immutable captures. Capture scope distinguishes status checks, excerpts, complete JDs, and legacy evidence; only a validated complete JD can support a future `not_stated` conclusion.
- Live Job schema 3 binds listing and application-link verification to explicit captures. Adding a capture never rewrites an old job artifact.
- Curation schema 4 binds every Candidate to one source, capture, hash, and evidence locator. Revise/split successors keep that capture unless a future explicit rebase workflow is designed.
- Legacy Source v2, Live Job v1/v2, and Curation v2/v3 remain explicitly readable. Migration is opt-in, never invents a complete capture, and ambiguous hash matches stop safely.
- Role Assignment schema 1 records human-confirmed initial mappings and reclassifications with exact capture evidence, deterministic lineage, reviewer, time, and reason.
- Live Job schema 4 and Curation schema 5 keep compatibility Role fields only as validated projections of the current Role Assignment. Their legacy schemas remain explicitly readable.
- Reclassification is all-or-nothing across Assignment, Live Job, and Curation artifacts. It safely handles unreviewed Candidates and proposed single-job Clusters; reviewed Candidates, lineage, confirmed/rejected Clusters, and mixed-job Clusters block automation.
- Curation schema 6 can preserve a source clause as a non-recursive `all_of` or `any_of` group of atomic Candidates. Python owns stable IDs, validation, and truth-table evaluation; Providers may only propose logic; humans confirm or reject it.
- Curation schema 7 separates Candidate fact review from Cluster membership. Proposed Clusters use controlled create, assign, remove, merge, and split operations; confirmation or rejection creates deterministic human review provenance bound to the exact semantic snapshot.
- Logic Groups now support six explicit outcomes: confirm, quarantine, reject members, release members, revise, and split. Current bindings are computed from reviewed lineage, so released or superseded history cannot masquerade as active logic.
- Catalog drafts consume only schema 7 approved leaves in current confirmed Clusters whose samples agree with confirmed Assignments. Unclustered approvals and unresolved Logic Groups are blocked; confirmed logic still returns `production_requirement_logic_contract_required` until a production requirement-logic schema exists.
- Builder diagnostics aggregate Logic Group blockers independently of Cluster status, so an unconfirmed Cluster cannot hide a group-level safety decision.
- The same canonical job cannot enter two Role Families, and multiple captures, split successors, or logic branches cannot inflate company counts.
- Full JD text and Pilot artifacts belong only under ignored `local_data/`; this contract fix does not modify them.
- The real Pilot job identified for reclassification has not been migrated. Clause Coverage and sample counts must be regenerated only after a separately approved local migration.
- The real schema 6 Candidate Completion artifact remains local and unmodified. It has not been migrated to schema 7, no review state changed, and no formal prevalence has been calculated.
- Production requirement logic, prevalence publication, User Decision, and Gap Analysis remain intentionally unimplemented.
- Capability Rubric schema 2 assigns stable IDs to every inclusion criterion and declares per-Dimension evidence classes, conservative status caps, confirmed-evidence thresholds, and behavior-evidence rules. Schema 1 stays explicitly readable and round-trippable.
- Version `0.9.0` uses Mapping schema 3 and Recommendation schema 4 by default, saves Mapping provenance for reuse, and provides the separate `review-evidence` command. User Decision, Gap Analysis, and Phase 2D are not implemented.
- The production Catalog remains `1.0.0`: eight roles, zero requirements, and zero sources. No unpublished market requirement is implied by the Rubric policy contract.

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
