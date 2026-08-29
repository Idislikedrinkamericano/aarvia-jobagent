"""Candidate profile data kept separate from the confirmed CareerProfile."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .discovery import create_profile
from .profile import ProfileValidationError

TOPICS = (
    "basic_profile",
    "education",
    "experience_overview",
    "skills",
    "career_preferences",
    "constraints",
)


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DiscoveryAnswerState(str, Enum):
    UNANSWERED = "unanswered"
    ANSWERED = "answered"
    CONFIRMED_NONE = "confirmed_none"
    SKIPPED = "skipped"
    DECLINED = "declined"


def _has_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_content(item) for item in value.values())
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""


@dataclass
class CandidateProfile:
    data: dict[str, Any]
    source: str = "user_narrative"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING
    topic_statuses: dict[str, ConfirmationStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source != "user_narrative":
            raise ProfileValidationError("candidate source must be user_narrative")
        unknown = sorted(set(self.data) - set(TOPICS))
        if unknown:
            raise ProfileValidationError(f"candidate contains unknown fields: {', '.join(unknown)}")
        validated = create_profile(self.data).to_dict()
        validated.pop("open_questions")
        self.data = {
            topic: validated[topic]
            for topic in TOPICS
            if topic in self.data and _has_content(validated[topic])
        }
        self.topic_statuses = {
            topic: self.topic_statuses.get(topic, ConfirmationStatus.PENDING)
            for topic in self.data
        }

    @classmethod
    def from_extracted(cls, data: Mapping[str, Any]) -> CandidateProfile:
        if not isinstance(data, Mapping):
            raise ProfileValidationError("candidate profile data must be a dictionary")
        return cls(data=dict(data))

    @property
    def topics(self) -> list[str]:
        return [topic for topic in TOPICS if topic in self.data]

    def replace_topic(self, topic: str, value: Any) -> None:
        replacement = CandidateProfile.from_extracted({topic: value})
        if topic not in replacement.data:
            raise ProfileValidationError(f"correction did not contain {topic}")
        self.data[topic] = replacement.data[topic]
        self.topic_statuses[topic] = ConfirmationStatus.PENDING

    def mark(self, topic: str, status: ConfirmationStatus) -> None:
        if topic not in self.data:
            raise ProfileValidationError(f"candidate does not contain topic: {topic}")
        self.topic_statuses[topic] = status
        statuses = set(self.topic_statuses.values())
        if statuses == {ConfirmationStatus.ACCEPTED}:
            self.confirmation_status = ConfirmationStatus.ACCEPTED
        elif ConfirmationStatus.PENDING not in statuses:
            self.confirmation_status = ConfirmationStatus.REJECTED


@dataclass
class DiscoveryState:
    topics: dict[str, DiscoveryAnswerState] = field(
        default_factory=lambda: {topic: DiscoveryAnswerState.UNANSWERED for topic in TOPICS}
    )

    def mark(self, topic: str, state: DiscoveryAnswerState) -> None:
        if topic not in TOPICS:
            raise ProfileValidationError(f"unknown discovery topic: {topic}")
        self.topics[topic] = state
