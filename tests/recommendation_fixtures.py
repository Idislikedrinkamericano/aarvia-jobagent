from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from aarvia.capability_rubric import production_capability_rubric
from aarvia.profile import CareerProfile
from aarvia.profile_dimension_mapping import (
    ProfileDimensionMappingCandidateSet,
    canonical_evidence_span_inventory,
)
from aarvia.capability_rubric import EvidenceClass
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
                "profile_fact_references": [{"path": f"skills[{skill_index}].skill_name", "value_snapshot": f"Synthetic Skill {skill_index + 1}", "exact_excerpt": f"Synthetic Skill {skill_index + 1}"}],
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
            ("career_goal_alignment", "career_preferences.currently_considered_roles[0]", "AI Engineer"),
            ("work_content_preference_alignment", "career_preferences.preferred_work_activities[0]", "Building systems"),
            ("growth_direction_alignment", "career_preferences.interested_fields[0]", "Applied AI"),
            ("transferable_foundation", "experience_overview[0].short_factual_summary", "Built and evaluated a small software system."),
        ):
            signals.append({"role_id": role, "signal_type": signal_type, "status": "aligned", "profile_fact_references": [{"path": path, "value_snapshot": value}], "reasoning": "Synthetic directional evidence.", "provider_confidence": "high", "review_required": False, "suggested_follow_up": None})
    constraints = []
    for role in rubric.supported_role_ids:
        constraints.append({"role_id": role, "status": "unknown" if unknown_constraints else "compatible", "profile_fact_references": [] if unknown_constraints else [{"path": "constraints.target_locations[0]", "value_snapshot": "United States"}], "reasoning": "Synthetic constraint assessment.", "provider_confidence": "low" if unknown_constraints else "high", "review_required": unknown_constraints, "unknown_constraint_ids": ["work_authorization"] if unknown_constraints else [], "suggested_follow_up": "What work authorization constraints apply?" if unknown_constraints else None})
    return {"mappings": mappings, "directional_signals": signals, "constraints": constraints, "conflict_warnings": []}


def mapping_set(**kwargs) -> ProfileDimensionMappingCandidateSet:
    return ProfileDimensionMappingCandidateSet.from_provider_payload(mapping_payload(**kwargs), profile=synthetic_profile(), rubric=production_capability_rubric(), catalog=production_role_catalog(), provider_name="fixture", provider_model="fixture-model")


def mapping_set_v3(
    profile: CareerProfile | None = None, *, binding_count: int = 1
) -> ProfileDimensionMappingCandidateSet:
    active_profile = profile or synthetic_profile()
    rubric = production_capability_rubric()
    spans = canonical_evidence_span_inventory(active_profile)
    summary_spans = [
        item for item in spans
        if item.path.endswith(".short_factual_summary")
    ]
    bindings = []
    used_dimensions = set()
    for span in summary_spans:
        record_index = int(span.path.split("[")[1].split("]")[0])
        experience_type = active_profile.experience_overview[record_index].experience_type
        evidence_class = (
            EvidenceClass.PROJECT_SUMMARY
            if experience_type == "project"
            else EvidenceClass.EXPERIENCE_SUMMARY
        )
        dimension = next(
            (
                item for item in rubric.dimensions
                if item.dimension_id not in used_dimensions
                and evidence_class in item.evidence_support_policy.allowed_evidence_classes
            ),
            None,
        )
        if dimension is None:
            continue
        used_dimensions.add(dimension.dimension_id)
        bindings.append(
            {
                "role_id": dimension.role_id,
                "dimension_id": dimension.dimension_id,
                "criterion_id": dimension.criterion_ids[0],
                "span_id": span.span_id,
                "proposed_binding_type": "direct",
                "provider_confidence": "high",
            }
        )
        if len(bindings) == binding_count:
            break
    if len(bindings) != binding_count:
        raise AssertionError("fixture Profile does not contain enough independent summary spans")
    return ProfileDimensionMappingCandidateSet.from_provider_payload_v3(
        {
            "mappings": bindings,
            "directional_signals": [],
            "constraints": [],
            "conflict_warnings": [],
        },
        profile=active_profile,
        rubric=rubric,
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        evidence_spans=spans,
    )


def mapping_set_v4(
    profile: CareerProfile | None = None, *, binding_count: int = 1
) -> ProfileDimensionMappingCandidateSet:
    active_profile = profile or synthetic_profile()
    v3 = mapping_set_v3(active_profile, binding_count=binding_count)
    spans = canonical_evidence_span_inventory(active_profile)
    span_ids = {
        span.materialize(active_profile).evidence_fingerprint: span.span_id
        for span in spans
    }
    payload = {
        "mappings": [
            {
                "role_id": item.role_id,
                "dimension_id": item.dimension_id,
                "criterion_id": item.criterion_id,
                "span_id": span_ids[item.atomic_evidence.evidence_fingerprint],
                "proposed_binding_type": item.proposed_binding_type.value,
                "provider_confidence": item.provider_confidence.value,
            }
            for item in v3.mappings
        ],
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }
    return ProfileDimensionMappingCandidateSet.from_provider_payload_v4(
        payload,
        profile=active_profile,
        rubric=production_capability_rubric(),
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        evidence_spans=spans,
    )


def mapping_set_v5(
    profile: CareerProfile | None = None, *, binding_count: int = 1
) -> ProfileDimensionMappingCandidateSet:
    active_profile = profile or synthetic_profile()
    v4 = mapping_set_v4(active_profile, binding_count=binding_count)
    return replace(v4, schema_version=5, unresolved_evidence_groups=())


def mapping_set_v5_unresolved(
    profile: CareerProfile | None = None,
) -> ProfileDimensionMappingCandidateSet:
    active_profile = profile or synthetic_profile()
    rubric = production_capability_rubric()
    spans = canonical_evidence_span_inventory(active_profile)
    span = next(item for item in spans if item.path.endswith("short_factual_summary"))
    dimensions = [
        item for item in rubric.dimensions
        if item.role_id == "applied_ai_engineer"
        and EvidenceClass.PROJECT_SUMMARY
        in item.evidence_support_policy.allowed_evidence_classes
        and item.evidence_support_policy.provisional_status_cap.value
        == "partially_demonstrated"
    ][:2]
    payload = {
        "mappings": [
            {
                "role_id": dimension.role_id,
                "dimension_id": dimension.dimension_id,
                "criterion_id": dimension.criterion_ids[0],
                "span_id": span.span_id,
                "proposed_binding_type": "direct",
                "provider_confidence": "high",
            }
            for dimension in dimensions
        ],
        "directional_signals": [],
        "constraints": [],
        "conflict_warnings": [],
    }
    return ProfileDimensionMappingCandidateSet.from_provider_payload_isolated(
        payload,
        profile=active_profile,
        rubric=rubric,
        catalog=production_role_catalog(),
        provider_name="fixture",
        provider_model="fixture-model",
        attempt_number=1,
        response_hash_reference="sha256:" + "7" * 64,
        evidence_spans=spans,
        mapping_schema_version=5,
    )


def clone_payload(**kwargs) -> dict:
    return deepcopy(mapping_payload(**kwargs))


def mapping_transport_payload(
    payload: dict | None = None,
    *,
    profile: CareerProfile | None = None,
    strict: bool = True,
) -> dict:
    """Convert valid persisted-style fixture evidence into live Provider span selections."""
    active_profile = profile or synthetic_profile()
    result = deepcopy(payload if payload is not None else mapping_payload())
    spans = {
        (span.path, span.text): span.span_id
        for span in canonical_evidence_span_inventory(active_profile)
    }
    for mapping in result["mappings"]:
        selections = []
        for index, reference in enumerate(mapping["profile_fact_references"]):
            key = (
                reference["path"].removeprefix("career_profile."),
                reference.get("exact_excerpt", reference.get("value_snapshot")),
            )
            if key not in spans:
                if strict:
                    raise KeyError(f"no canonical evidence span for {key!r}")
                selections.append({"span_id": f"invalid_fixture_span_{index}"})
            else:
                selections.append({"span_id": spans[key]})
        mapping["profile_fact_references"] = selections
    for collection in ("directional_signals", "constraints"):
        for candidate in result[collection]:
            selections = []
            for index, reference in enumerate(candidate["profile_fact_references"]):
                key = (
                    reference["path"].removeprefix("career_profile."),
                    reference.get("value_snapshot"),
                )
                if key not in spans:
                    if strict:
                        raise KeyError(f"no canonical evidence span for {key!r}")
                    selections.append({"span_id": f"invalid_fixture_span_{index}"})
                else:
                    selections.append({"span_id": spans[key]})
            candidate["profile_fact_references"] = selections
    return result
