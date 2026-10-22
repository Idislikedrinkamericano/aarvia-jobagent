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
