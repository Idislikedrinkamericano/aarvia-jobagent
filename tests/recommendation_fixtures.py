from __future__ import annotations

from copy import deepcopy

from aarvia.capability_rubric import production_capability_rubric
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import ProfileDimensionMappingCandidateSet
from aarvia.role_catalog import production_role_catalog


def synthetic_profile() -> CareerProfile:
    return CareerProfile.from_dict({
        "basic_profile": {"name": "Sample User", "current_location": "Test City", "current_status": "Student"},
        "education": [{"institution": "Example University", "degree": "Bachelor", "field_of_study": "Computing", "start_date": "2023-09", "expected_graduation_date": "2027-05", "gpa": None}],
        "experience_overview": [{"experience_type": "project", "organization_or_project_name": "Synthetic Project", "title_or_role": "Developer", "short_factual_summary": "Built and evaluated a small software system.", "start_date": "2025-01", "end_date": "2025-05"}],
        "skills": [{"skill_name": f"Synthetic Skill {index}", "category": "technical", "self_reported_proficiency": None} for index in range(1, 25)],
        "career_preferences": {"interested_fields": ["Applied AI"], "preferred_work_activities": ["Building systems"], "preferred_industries": [], "fields_or_activities_to_avoid": [], "currently_considered_roles": ["AI Engineer"]},
        "constraints": {"target_locations": ["United States"], "work_authorization_or_visa_constraints": "Needs confirmation", "work_arrangement_preference": "flexible", "employment_type_preference": "internship", "target_start_date": "2027-06", "other_constraints": []},
    })


def mapping_payload(*, all_demonstrated: bool = False, unknown_constraints: bool = False) -> dict:
    rubric = production_capability_rubric()
    mappings = []
    skill_index = 0
    for dimension in rubric.dimensions:
        if all_demonstrated or skill_index % 2 == 0:
            mappings.append({
                "role_id": dimension.role_id,
                "dimension_id": dimension.dimension_id,
                "match_status": "demonstrated" if all_demonstrated else "partially_demonstrated",
                "profile_fact_references": [{"path": f"skills[{skill_index}].skill_name", "value_snapshot": f"Synthetic Skill {skill_index + 1}"}],
                "reasoning": "The synthetic skill directly supports this test dimension.",
                "inference_type": "direct",
                "evidence_strength": "strong",
                "provider_confidence": "high",
                "review_required": False,
                "relationship": "primary",
                "suggested_follow_up": None,
            })
        skill_index += 1
    signals = []
    for role in rubric.supported_role_ids:
        for signal_type, path, value in (
            ("career_goal_alignment", "career_preferences.currently_considered_roles", ["AI Engineer"]),
            ("work_content_preference_alignment", "career_preferences.preferred_work_activities", ["Building systems"]),
            ("growth_direction_alignment", "career_preferences.interested_fields", ["Applied AI"]),
            ("transferable_foundation", "experience_overview[0].short_factual_summary", "Built and evaluated a small software system."),
        ):
            signals.append({"role_id": role, "signal_type": signal_type, "status": "aligned", "profile_fact_references": [{"path": path, "value_snapshot": value}], "reasoning": "Synthetic directional evidence.", "provider_confidence": "high", "review_required": False, "suggested_follow_up": None})
    constraints = []
    for role in rubric.supported_role_ids:
        constraints.append({"role_id": role, "status": "unknown" if unknown_constraints else "compatible", "profile_fact_references": [] if unknown_constraints else [{"path": "constraints.target_locations", "value_snapshot": ["United States"]}], "reasoning": "Synthetic constraint assessment.", "provider_confidence": "low" if unknown_constraints else "high", "review_required": unknown_constraints, "unknown_constraint_ids": ["work_authorization"] if unknown_constraints else [], "suggested_follow_up": "What work authorization constraints apply?" if unknown_constraints else None})
    return {"mappings": mappings, "directional_signals": signals, "constraints": constraints, "conflict_warnings": []}


def mapping_set(**kwargs) -> ProfileDimensionMappingCandidateSet:
    return ProfileDimensionMappingCandidateSet.from_provider_payload(mapping_payload(**kwargs), profile=synthetic_profile(), rubric=production_capability_rubric(), catalog=production_role_catalog(), provider_name="fixture", provider_model="fixture-model")


def clone_payload(**kwargs) -> dict:
    return deepcopy(mapping_payload(**kwargs))
