# Aarvia Conversation Log - Phase 3

> Public sanitized summary. This file is not a verbatim personal transcript.

## Conversation 1 - Traceable Local JD Matching

The project owner approved Phase 3A after the role-neutral Base Resume was validated. Version `0.20.0` adds a non-persistent validated Phase 2 context, a confirmed-fact index, JD Analysis schema 1, JD Match Report schema 1, and `aarvia match-jd`. A local UTF-8 JD is treated as untrusted data, every extracted requirement retains an exact source span, and every supporting fact ID must resolve to the fully validated current evidence chain. Contact, rejected or deferred Claims, Evidence Needs, gaps, and future plans cannot support a match. The report distinguishes support, partial support, missing evidence, and fact-backed mismatch without producing a hiring probability or modifying the Base Resume. Automated acceptance uses synthetic fixtures and mock Providers; real-JD acceptance remains pending. Minimal Tailoring and final Fact Checking are not part of this stage.

## Conversation 2 - Provider Fact Reference Recovery

The first private real-JD attempt exposed a Provider contract failure: an output fact reference did not resolve to the eligible inventory. The failed response was not retained, so the exact bad identifier and whether it was a material, wording, stale, or invented ID remain unknown. Version `0.20.1` binds request and validation to one fact-index snapshot, constrains supported structured output to that inventory, sanitizes invalid identifiers in errors, and allows one corrective response before stopping without outputs. Request, authentication, quota, and network failures are not retried. No private JD, Profile fact, or Provider response is included in this public summary.

## Conversation 3 - Semantic Matching Guardrails

A private real-JD run then exposed semantic-quality issues around analysis versus internship dates, completed versus active education, compound requirements, employer-specific responsibility context, learning versus demonstrated use, single-agent versus multi-agent work, return-to-study conditions, deadlines, and application materials. Version `0.20.2` records an explicit analysis date in JD Analysis schema 2 and adds a procedural action status that cannot masquerade as a capability gap. Shared Provider instructions define the remaining conservative semantic boundaries. Python continues to validate sources and relationships rather than pretending to solve unrestricted natural-language entailment; synthetic tests validate the contract, while real-model behavior still requires a separate private acceptance run.

## Conversation 4 - Structured Semantic Decisions

The private v2 report confirmed that the date context and most guidance reached the configured model, but final statuses could still contradict the model's own reasoning. Version `0.20.3` removes final status from Provider control: the Provider classifies evaluation mode, logical operator, and support scope, then Python derives the persisted status. A compact set of fictional human-annotated cases records the expected conclusion, prohibited inference, and allowed conservative result for the observed semantic boundary classes. This improves consistency without employer-, person-, school-, project-, or JD-specific rules. General semantic judgment remains model-dependent and requires private real-model acceptance.

## Conversation 5: Source Context and Auditable JD Clauses

A subsequent private acceptance review showed that isolated fact excerpts lacked their verified project, organization, date, and contribution context, while a compound introductory condition could lose one branch during normalization. Version `0.20.4` adds a minimal non-persistent source context to Provider inputs and JD Analysis schema 3 with deterministic section provenance, importance derivation, logical operators, and exact condition-clause offsets. Contact and non-confirmed content remain excluded, and context cannot independently support a capability. Clause offsets prove source fidelity but not semantic completeness; private real-model acceptance is still required before tailoring.

## Conversation 6: Provider Data-Instance Boundary

A private, fictional model comparison showed that one configured model repeatedly returned a schema-shaped envelope instead of the requested data instance. It also exposed that the comparison's partial checker accepted overlapping condition clauses that production would reject. Version `0.20.5` makes the instance-versus-schema boundary explicit without model-specific repair, shares the pure production Provider contract with the comparison, and records a stricter local-only human rubric. The prior comparison is not rewritten, no endpoint capability is assumed, and a new two-request format check remains necessary before drawing model-quality conclusions.

## Conversation 7: Support-Scope Semantics

The next private fictional comparison passed the repaired output boundary but exposed two semantic conflicts: illustrative examples were treated as additional requirements, and related learning or single-agent background could be labeled partial support even when none of the requested multi-agent conditions was established. Version `0.20.6` replaces the conflicting guidance with explicit support-scope definitions and updates the local-only human rubric. Structural contract success is still not semantic success; focused offline tests prove that both Provider paths receive the same rules, while real-model acceptance remains a separate private check.

## Conversation 8: Human-Assisted Resume Application Cycle

A private real-JD workflow was taken from a traceable match report through a manually reviewed tailoring proposal, explicit user confirmation, a minimally adjusted resume, and line-by-line fact review. This demonstrated a usable human-assisted path while also confirming that real-model match reports can still misread conditions, misattribute an otherwise valid fact, or make an unsupported inference. The B/C instruction variants remain isolated experiments and were not promoted into the production prompt. No automated Tailoring artifact, automated semantic fact checker, or unified PDF export workflow was added. The next product step is the narrow reviewed flow from adjustment proposal to user confirmation and resume output.

## Conversation 9: Traceable Tailoring Proposal

Version `0.21.0` formalizes the first narrow Phase 3B step. A strict proposal artifact derives review-only ordering and existing-bullet selection suggestions from the validated Base Resume and JD Match, while a separate owner-review artifact records accepted, rejected, and deferred decisions. The flow reuses confirmed facts and reviewed wording, never treats match reasoning as a career fact, and does not edit the resume. Applying accepted decisions, free rewriting, semantic fact checking, and rendering tailored output remain outside this release.

## Conversation 10: Ordering-Only Tailored Resume

Version `0.22.0` applies accepted proposal decisions as deterministic ordering changes. A derived Tailored Resume artifact stores full provenance, final layout, and an auditable change list without becoming a new fact authority. The CLI emits new JSON, Markdown, and change-log files atomically while preserving the Base Resume. Rejected, deferred, and unreviewed suggestions remain inert; wording changes, deletions, new facts, semantic fact checking, and PDF export remain separate stages.

## Conversation 11: Human-Reviewed JD-Targeted Wording

Version `0.23.0` adds a narrow wording overlay after ordering-only tailoring. A Provider can propose optional rewrites of existing eligible bullets using verified fact references and JD requirement IDs, but cannot submit facts, IDs, review decisions, scores, or final resume state. A separate profile-owner review reuses the existing wording decision records, and only accepted candidates appear in a new JSON, Markdown resume, and wording change log. The master resume and its reviewed wording remain unchanged. Structural validation proves references and provenance, not semantic fidelity; human review remains responsible for detecting expanded ownership, invented technology, prototype-to-production inflation, or loss of team-contribution qualifiers.

Version `0.23.1` handles an unchanged Provider candidate as an explicit no-op only after the complete response passes strict field, type, reference, duplication, and injection validation. The Proposal records received, unchanged, and reviewable counts; empty or all-no-op responses are valid and do not force a rewrite. No Provider call was repeated for this patch.

## Conversation 12: Final Resume Fact Check

Version `0.24.0` adds a single final fact-check artifact instead of another proposal/review pair. The final ordering or wording-overlay resume is rebuilt from the complete validated chain; every bullet retains its original and final wording, source and fact references, and accepted wording provenance. Python verifies structure, references, membership, staleness, and the exact final content hash, while the profile owner separately records passed, issue-found, or needs-confirmation. The readable report omits Contact and cannot rewrite or repair the resume. No Provider participates in this stage.

## Conversation 13: Validated Final Resume PDF Export

Version `0.25.0` adds a display-only PDF export for the exact validated final resume. Export requires a matching Final Resume Fact Check whose bullet decisions are all passed, rebuilds content from typed artifacts rather than Markdown, preserves ordering and wording, and writes atomically with natural Letter-page pagination. The renderer uses only local built-in fonts and safe HTTPS links. The review remains bullet-scoped and does not certify Contact, Education, Skills, or the resume as a whole.

The private Phase 3 acceptance then completed the minimum human-reviewed loop through real PDF export. The real wording run generated candidates, the owner rejected all of them, and the final resume correctly preserved the reviewed source wording. This validates the all-rejected path, not application of an accepted real-model rewrite. The formal fact check covered four bullets only, so the result is not evidence of automated semantic fact judgment, full-resume certification, application automation, or generally accepted model quality.
