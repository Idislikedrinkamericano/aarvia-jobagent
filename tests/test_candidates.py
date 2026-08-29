import pytest

from aarvia import ProfileValidationError
from aarvia.candidates import (
    CandidateProfile,
    ConfirmationStatus,
    DiscoveryAnswerState,
    DiscoveryState,
)


def test_candidate_records_source_timestamp_and_pending_status() -> None:
    candidate = CandidateProfile.from_extracted(
        {"skills": [{"skill_name": "Python", "category": "language"}]}
    )

    assert candidate.source == "user_narrative"
    assert candidate.created_at
    assert candidate.confirmation_status == ConfirmationStatus.PENDING
    assert candidate.topic_statuses["skills"] == ConfirmationStatus.PENDING


def test_candidate_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ProfileValidationError, match="unknown fields: recommended_role"):
        CandidateProfile.from_extracted({"recommended_role": "Engineer"})


def test_candidate_reuses_phase_1a_nested_validation() -> None:
    with pytest.raises(ProfileValidationError, match="unknown fields: inferred_level"):
        CandidateProfile.from_extracted(
            {"skills": [{"skill_name": "Python", "category": "language", "inferred_level": "expert"}]}
        )


def test_candidate_reuses_phase_1a_date_validation() -> None:
    with pytest.raises(ProfileValidationError, match="YYYY-MM"):
        CandidateProfile.from_extracted(
            {
                "education": [{
                    "institution": "University",
                    "degree": "MSc",
                    "field_of_study": "AI",
                    "start_date": "last year",
                    "expected_graduation_date": "2027-06",
                }]
            }
        )


def test_discovery_state_is_separate_from_candidate_data() -> None:
    state = DiscoveryState()
    state.mark("skills", DiscoveryAnswerState.SKIPPED)

    assert state.topics["skills"] == DiscoveryAnswerState.SKIPPED
    assert state.topics["education"] == DiscoveryAnswerState.UNANSWERED
