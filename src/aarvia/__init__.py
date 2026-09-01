"""Aarvia career navigation and job application package."""

from .discovery import create_profile, generate_open_questions
from .discovery_state import DiscoveryState, FollowUpStatus, load_discovery_state
from .profile import (
    BasicProfile,
    CareerPreferences,
    CareerProfile,
    Constraints,
    Education,
    ExperienceOverview,
    ProfileValidationError,
    Skill,
)
from .storage import load_profile, save_profile

__version__ = "0.5.7"

__all__ = [
    "BasicProfile",
    "CareerPreferences",
    "CareerProfile",
    "Constraints",
    "Education",
    "DiscoveryState",
    "ExperienceOverview",
    "FollowUpStatus",
    "ProfileValidationError",
    "Skill",
    "create_profile",
    "generate_open_questions",
    "load_profile",
    "load_discovery_state",
    "save_profile",
]
