# Aarvia

Aarvia is a Career Navigation + Job Application Agent. It helps people understand where they can go next in their careers and supports evidence-grounded job applications once they choose a direction.

Aarvia is not an "upload a job description and let an LLM rewrite the entire resume" tool. It begins with the person: their background, interests, constraints, and goals. It can help users explore plausible directions when they are uncertain, identify gaps between their current capabilities and target roles, and preserve their real experience as the factual source for every later resume claim.

## What Aarvia Solves

Career decisions are broader than keyword matching against a current resume. Users may not yet know which roles fit them, may have relevant evidence scattered across different experiences, or may need to understand what is missing before applying. Once a direction is selected, repeated full-resume generation creates unnecessary changes and increases the risk of unsupported claims.

Aarvia addresses these problems by separating career discovery, evidence collection, role-specific base resumes, job-description matching, minimal tailoring, and final fact checking into a traceable workflow.

## Product Pipeline

```text
User Profile
-> Career Discovery
-> Role Recommendation
-> User Decision
-> Gap Analysis
-> Evidence Bank
-> Base Resume
-> JD Matching
-> Minimal Tailoring
-> Fact Checking
-> Final Resume
```

## Design Principles

- User direction is not determined only by the current resume.
- User keeps final control over career direction.
- Resume claims must be grounded in real evidence.
- Tailoring should be minimal and traceable.
- Complex agent frameworks should only be introduced when actually needed.
- Core logic should be testable independently from the LLM.

## Current Status

**Phase 0 — Project foundation only.**

This repository currently contains only the Python project skeleton. No agent, career planning, matching, resume generation, LLM integration, data schema, or frontend functionality has been implemented.
