"""Deterministic career discovery rules for incomplete profiles."""

from __future__ import annotations

from typing import Any, Mapping

from .profile import CareerProfile


def generate_open_questions(profile: CareerProfile) -> list[str]:
    """Generate stable questions for missing profile information without an LLM."""
    questions: list[str] = []
    if profile.basic_profile.current_location is None:
        questions.append("What is your current location?")
    if profile.basic_profile.current_status is None:
        questions.append("What is your current education or employment status?")
    if not profile.education:
        questions.append("What is your education background?")
    if not profile.experience_overview:
        questions.append("What work, internship, research, volunteer, or project experience do you have?")
    if not profile.skills:
        questions.append("What skills do you currently have?")

    preferences = profile.career_preferences
    if not preferences.interested_fields:
        questions.append("Which career fields interest you?")
    if not preferences.preferred_work_activities:
        questions.append("What kinds of work activities do you prefer?")
    if not preferences.preferred_industries:
        questions.append("Which industries interest you?")
    if not preferences.currently_considered_roles:
        questions.append("Are there any roles you are currently considering?")

    constraints = profile.constraints
    if not constraints.target_locations:
        questions.append("Which locations are you targeting?")
    if constraints.work_authorization_or_visa_constraints is None:
        questions.append("Do you have any work authorization or visa constraints?")
    if constraints.work_arrangement_preference is None:
        questions.append("Do you prefer remote, hybrid, or onsite work?")
    if constraints.employment_type_preference is None:
        questions.append("Are you seeking an internship, a full-time role, or either?")
    if constraints.target_start_date is None:
        questions.append("When would you like to start your next role?")
    return questions


def create_profile(data: Mapping[str, Any] | None = None) -> CareerProfile:
    """Create and validate a career profile from a dictionary."""
    return CareerProfile.from_dict({} if data is None else data)
