"""Aarvia career navigation and job application package."""

from .discovery import create_profile, generate_open_questions
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

__version__ = "0.4.3"

__all__ = [
    "BasicProfile",
    "CareerPreferences",
    "CareerProfile",
    "Constraints",
    "Education",
    "ExperienceOverview",
    "ProfileValidationError",
    "Skill",
    "create_profile",
    "generate_open_questions",
    "load_profile",
    "save_profile",
]
