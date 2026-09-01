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
from .career_direction import (
    ProfileReference,
    RecommendationSet,
    RoleGapAnalysis,
    UserRoleDecision,
    load_recommendation_set,
    load_role_gap_analysis,
    load_user_role_decision,
    save_recommendation_set,
    save_role_gap_analysis,
    save_user_role_decision,
)
from .live_jobs import (
    ApplicationURLStatus,
    LiveJobCollection,
    load_live_job_collection,
    save_live_job_collection,
)
from .role_catalog import (
    Phase2ValidationError,
    RoleCatalog,
    RoleFamily,
    RoleRequirement,
    SourceReference,
    load_role_catalog,
    production_role_catalog,
    save_role_catalog,
)

__version__ = "0.6.0"

__all__ = [
    "BasicProfile",
    "ApplicationURLStatus",
    "CareerPreferences",
    "CareerProfile",
    "Constraints",
    "Education",
    "DiscoveryState",
    "ExperienceOverview",
    "FollowUpStatus",
    "ProfileValidationError",
    "ProfileReference",
    "Phase2ValidationError",
    "RecommendationSet",
    "RoleCatalog",
    "RoleFamily",
    "RoleGapAnalysis",
    "RoleRequirement",
    "Skill",
    "SourceReference",
    "LiveJobCollection",
    "UserRoleDecision",
    "create_profile",
    "generate_open_questions",
    "load_profile",
    "load_live_job_collection",
    "load_recommendation_set",
    "load_role_catalog",
    "load_role_gap_analysis",
    "load_user_role_decision",
    "load_discovery_state",
    "production_role_catalog",
    "save_profile",
    "save_live_job_collection",
    "save_recommendation_set",
    "save_role_catalog",
    "save_role_gap_analysis",
    "save_user_role_decision",
]
