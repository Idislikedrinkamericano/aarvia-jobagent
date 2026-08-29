# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia helps you build an accurate Career Profile before making career or job-application decisions. It collects what you have actually done, what work you want, and the practical constraints that affect your search.

It is not a tool that uploads a job description and rewrites an entire resume without context.

## What Works Today

Version `0.4.3` supports:

- A validated Career Profile for education, experience, skills, preferences, and constraints.
- JSON save/load with strict rejection of unknown fields.
- A manual terminal questionnaire.
- A natural-language discovery mode using an OpenAI-compatible Responses API.
- A Candidate layer: extracted information is reviewed before it reaches the formal Profile.
- Explicit conflict handling when new information differs from an existing Profile.
- OpenAI and Alibaba Cloud Bailian configuration through the same SDK.

Resume file import, adaptive follow-up questions, and role recommendations are not implemented yet.

## Quick Start

Install the project in development mode:

```bash
python -m pip install -e ".[dev]"
```

### Natural-Language Mode

Configure a provider, then run:

```bash
export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-structured-output-model"

aarvia discover --narrative
```

Aarvia asks for one normal-language description of your education, experience, projects, and skills. It then shows each extracted topic separately:

- `y`: accept this topic
- `n`: reject this topic
- `e`: describe a correction
- `q`: exit without accepting pending topics

Nothing extracted by the model becomes formal Profile data until you accept it.

### Alibaba Cloud Bailian

Bailian requires a regional OpenAI-compatible endpoint:

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"

aarvia discover --narrative
```

Replace `{WorkspaceId}` with your own business workspace ID.

Important:

- API keys and endpoints from regions such as Beijing and Singapore are not interchangeable.
- The model must be enabled in the same region and support the Responses API.
- Aarvia does not assume every Bailian model supports that API.
- Never commit API keys, workspace IDs, personal endpoints, or Profile JSON.

OpenAI users can omit `AARVIA_LLM_BASE_URL`. The old `OPENAI_API_KEY` and `AARVIA_OPENAI_MODEL` variables still work when the generic variables are absent.

### Manual Mode

The manual questionnaire does not require an LLM:

```bash
aarvia discover
aarvia discover --manual
```

Use `:skip` to leave a question unanswered or `:quit` to save and exit. Manual mode is a fallback and exposes more of the underlying Profile fields.

## Profile Files

The default path is:

```text
data/profiles/default.json
```

Choose another path with:

```bash
aarvia discover --narrative --profile data/profiles/example.json
```

Files under `data/profiles/` are ignored by Git. Do not commit private career information.

## How Natural-Language Data Is Handled

```text
User narrative
-> Provider extraction
-> Deterministic normalization
-> Candidate validation
-> User confirmation
-> Conflict resolution
-> Confirmed Career Profile
```

The Responses API schema asks providers to return the formal nested field names. Some compatible providers may still return these supported aliases:

- `projects` -> project entries in `experience_overview`
- `target_roles` -> `career_preferences.currently_considered_roles`
- `target_locations` -> `constraints.target_locations`
- `target_employment_type` -> `constraints.employment_type_preference`

Aarvia normalizes only these documented aliases. Other unknown fields are rejected. Normalized data must still pass the original Career Profile validation, and a failed Candidate never modifies the formal Profile.

Some compatible providers also return extra fields with no data, such as `"contact_info": {}`. Aarvia removes an unknown field only when its value is completely empty, including recursively empty objects or lists. If an unknown field contains any real value, Aarvia rejects the Candidate instead of discarding that information.

## Current Product Stage

- **Phase 1A — Career Profile Foundation: completed**
- **Phase 1B-v1 — Manual Career Discovery CLI: completed**
- **Phase 1B-v2.1 — Candidate and Confirmation Workflow: completed**
- **Phase 1B-v2.2 — Resume file import: not implemented**
- **Phase 1B-v2.3 — Adaptive follow-up questions: not implemented**

The planned product pipeline is:

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

Only User Profile and the current Career Discovery foundation exist today.

## Design Principles

- The current resume does not determine the user's career direction.
- The user keeps final control.
- Profile and resume claims must be grounded in real information.
- Extracted information is a Candidate until the user confirms it.
- Unknown or conflicting information is never silently accepted.
- Core validation and merge logic remain testable without an LLM.

## Python API (Optional)

Most users should use `aarvia discover`. This section is only for developers who want to create a Profile directly from Python.

The example intentionally creates a partial Profile. `create_profile()` validates the supplied facts and generates questions for anything still missing. `save_profile()` writes JSON, and `load_profile()` reads and validates it again.

```python
from aarvia import create_profile, load_profile, save_profile

profile_data = {
    "basic_profile": {
        "current_location": "Shanghai",
        "current_status": "Graduate student",
    },
    "skills": [
        {"skill_name": "Python", "category": "programming language"}
    ],
}

profile = create_profile(profile_data)
print(profile.open_questions)

profile_path = "data/profiles/example.json"
save_profile(profile, profile_path)

loaded_profile = load_profile(profile_path)
assert loaded_profile == profile
```
