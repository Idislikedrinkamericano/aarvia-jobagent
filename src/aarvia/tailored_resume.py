"""Apply reviewed ordering-only tailoring without creating new resume facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base_resume import BaseResumeArtifact, build_base_resume, render_base_resume_markdown
from .jd_matching import JDAnalysisArtifact, JDMatchReportArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .resume_context import ValidatedResumeContext
from .resume_material import ResumeSectionType
from .role_catalog import (
    Phase2ValidationError,
    _enum,
    _iso_datetime,
    _mapping,
    _reject_unknown,
    _stable_id,
    _string_tuple,
    _text,
)
from .tailoring_proposal import (
    TailoringAction,
    TailoringProposalArtifact,
    TailoringProposalReviewArtifact,
    TailoringReviewDecision,
    TailoringTargetType,
)


TAILORED_RESUME_SCHEMA = "aarvia.tailored_resume"
TAILORED_RESUME_SCHEMA_VERSION = 1
TAILORED_RESUME_ID_VERSION = "tailored-resume-v1"


class TailoringApplicationOutcome(str, Enum):
    MOVED = "moved"
    RETAINED = "retained"


def _stable_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _non_negative_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


def _positive_int(value: Any, path: str) -> int:
    result = _non_negative_int(value, path)
    if result < 1:
        raise Phase2ValidationError(f"{path} must be a positive integer")
    return result


@dataclass(frozen=True, eq=True)
class TailoredSectionLayout:
    section_type: ResumeSectionType
    entry_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoredSectionLayout":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _enum(data.get("section_type"), ResumeSectionType, f"{path}.section_type"),
            _string_tuple(data.get("entry_ids"), f"{path}.entry_ids"),
        )
        if len(result.entry_ids) != len(set(result.entry_ids)):
            raise Phase2ValidationError(f"{path}.entry_ids must be unique")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"section_type": self.section_type.value, "entry_ids": list(self.entry_ids)}


@dataclass(frozen=True, eq=True)
class TailoredEntryLayout:
    entry_id: str
    bullet_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoredEntryLayout":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("entry_id"), f"{path}.entry_id"),
            _string_tuple(data.get("bullet_ids"), f"{path}.bullet_ids"),
        )
        if not result.bullet_ids or len(result.bullet_ids) != len(set(result.bullet_ids)):
            raise Phase2ValidationError(f"{path}.bullet_ids must be non-empty and unique")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, "bullet_ids": list(self.bullet_ids)}


@dataclass(frozen=True, eq=True)
class TailoringApplicationChange:
    change_id: str
    suggestion_id: str
    target_type: TailoringTargetType
    target_id: str
    action: TailoringAction
    before_position: int
    after_position: int
    outcome: TailoringApplicationOutcome

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoringApplicationChange":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("change_id"), f"{path}.change_id"),
            _stable_id(data.get("suggestion_id"), f"{path}.suggestion_id"),
            _enum(data.get("target_type"), TailoringTargetType, f"{path}.target_type"),
            _stable_id(data.get("target_id"), f"{path}.target_id"),
            _enum(data.get("action"), TailoringAction, f"{path}.action"),
            _positive_int(data.get("before_position"), f"{path}.before_position"),
            _positive_int(data.get("after_position"), f"{path}.after_position"),
            _enum(data.get("outcome"), TailoringApplicationOutcome, f"{path}.outcome"),
        )
        if result.outcome == TailoringApplicationOutcome.RETAINED:
            if result.action != TailoringAction.RETAIN_BULLET or result.before_position != result.after_position:
                raise Phase2ValidationError(f"{path} retained change is inconsistent")
        elif result.action == TailoringAction.RETAIN_BULLET or result.after_position >= result.before_position:
            raise Phase2ValidationError(f"{path} moved change must move an item earlier")
        if result.change_id != generate_tailoring_change_id(result):
            raise Phase2ValidationError(f"{path}.change_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "suggestion_id": self.suggestion_id,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "action": self.action.value,
            "before_position": self.before_position,
            "after_position": self.after_position,
            "outcome": self.outcome.value,
        }


def generate_tailoring_change_id(value: TailoringApplicationChange) -> str:
    payload = value.to_dict()
    payload.pop("change_id", None)
    return _stable_hash("tailoring_change", payload)


@dataclass(frozen=True, eq=True)
class TailoredResumeSummary:
    accepted_suggestion_count: int
    moved_count: int
    retained_count: int
    rejected_count: int
    deferred_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoredResumeSummary":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(*(
            _non_negative_int(data.get(name), f"{path}.{name}")
            for name in cls.__dataclass_fields__
        ))
        if result.accepted_suggestion_count != result.moved_count + result.retained_count:
            raise Phase2ValidationError(f"{path} accepted count does not match applied outcomes")
        return result

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class TailoredResumeArtifact:
    tailored_resume_id: str
    profile_fingerprint: str
    base_resume_id: str
    tailoring_proposal_id: str
    proposal_review_id: str
    jd_analysis_id: str
    match_report_id: str
    materialized_base_resume_id: str
    created_at: str
    section_layouts: tuple[TailoredSectionLayout, ...]
    entry_layouts: tuple[TailoredEntryLayout, ...]
    applied_suggestion_ids: tuple[str, ...]
    changes: tuple[TailoringApplicationChange, ...]
    summary: TailoredResumeSummary
    schema: str = TAILORED_RESUME_SCHEMA
    schema_version: int = TAILORED_RESUME_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TailoredResumeArtifact":
        data = _mapping(value, "tailored_resume")
        _reject_unknown(data, set(cls.__dataclass_fields__), "tailored_resume")
        if data.get("schema") != TAILORED_RESUME_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Tailored Resume schema")
        raw_sections = data.get("section_layouts")
        raw_entries = data.get("entry_layouts")
        raw_changes = data.get("changes")
        if not isinstance(raw_sections, list) or not isinstance(raw_entries, list) or not isinstance(raw_changes, list):
            raise Phase2ValidationError("Tailored Resume layouts and changes must be lists")
        result = cls(
            _stable_id(data.get("tailored_resume_id"), "tailored_resume.tailored_resume_id"),
            _text(data.get("profile_fingerprint"), "tailored_resume.profile_fingerprint"),
            _stable_id(data.get("base_resume_id"), "tailored_resume.base_resume_id"),
            _stable_id(data.get("tailoring_proposal_id"), "tailored_resume.tailoring_proposal_id"),
            _stable_id(data.get("proposal_review_id"), "tailored_resume.proposal_review_id"),
            _stable_id(data.get("jd_analysis_id"), "tailored_resume.jd_analysis_id"),
            _stable_id(data.get("match_report_id"), "tailored_resume.match_report_id"),
            _stable_id(data.get("materialized_base_resume_id"), "tailored_resume.materialized_base_resume_id"),
            _iso_datetime(data.get("created_at"), "tailored_resume.created_at"),
            tuple(TailoredSectionLayout.from_dict(item, f"tailored_resume.section_layouts[{i}]") for i, item in enumerate(raw_sections)),
            tuple(TailoredEntryLayout.from_dict(item, f"tailored_resume.entry_layouts[{i}]") for i, item in enumerate(raw_entries)),
            _string_tuple(data.get("applied_suggestion_ids"), "tailored_resume.applied_suggestion_ids"),
            tuple(TailoringApplicationChange.from_dict(item, f"tailored_resume.changes[{i}]") for i, item in enumerate(raw_changes)),
            TailoredResumeSummary.from_dict(data.get("summary"), "tailored_resume.summary"),
        )
        section_types = tuple(item.section_type for item in result.section_layouts)
        if len(section_types) != len(set(section_types)):
            raise Phase2ValidationError("Tailored Resume section layouts must be unique")
        entry_ids = tuple(item.entry_id for item in result.entry_layouts)
        if entry_ids != tuple(sorted(set(entry_ids))):
            raise Phase2ValidationError("Tailored Resume entry layouts must be unique and sorted")
        if result.applied_suggestion_ids != tuple(sorted(set(result.applied_suggestion_ids))):
            raise Phase2ValidationError("Tailored Resume applied suggestion IDs must be unique and sorted")
        change_suggestions = tuple(item.suggestion_id for item in result.changes)
        if change_suggestions != tuple(sorted(set(change_suggestions))):
            raise Phase2ValidationError("Tailored Resume changes must be unique and sorted by suggestion")
        if change_suggestions != result.applied_suggestion_ids:
            raise Phase2ValidationError("Tailored Resume changes must cover every accepted suggestion")
        if result.tailored_resume_id != generate_tailored_resume_id(result):
            raise Phase2ValidationError("Tailored Resume ID is not deterministic")
        return result

    def validate(
        self,
        *,
        context: ValidatedResumeContext,
        jd_analysis: JDAnalysisArtifact,
        match_report: JDMatchReportArtifact,
        proposal: TailoringProposalArtifact,
        proposal_review: TailoringProposalReviewArtifact,
    ) -> None:
        canonical = TailoredResumeArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Tailored Resume is not canonical")
        rebuilt = build_tailored_resume(
            context=context,
            jd_analysis=jd_analysis,
            match_report=match_report,
            proposal=proposal,
            proposal_review=proposal_review,
            created_at=self.created_at,
        )
        if rebuilt != self:
            raise Phase2ValidationError("Tailored Resume content, provenance, or layout is stale")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "tailored_resume_id": self.tailored_resume_id,
            "profile_fingerprint": self.profile_fingerprint,
            "base_resume_id": self.base_resume_id,
            "tailoring_proposal_id": self.tailoring_proposal_id,
            "proposal_review_id": self.proposal_review_id,
            "jd_analysis_id": self.jd_analysis_id,
            "match_report_id": self.match_report_id,
            "materialized_base_resume_id": self.materialized_base_resume_id,
            "created_at": self.created_at,
            "section_layouts": [item.to_dict() for item in self.section_layouts],
            "entry_layouts": [item.to_dict() for item in self.entry_layouts],
            "applied_suggestion_ids": list(self.applied_suggestion_ids),
            "changes": [item.to_dict() for item in self.changes],
            "summary": self.summary.to_dict(),
        }


def generate_tailored_resume_id(value: TailoredResumeArtifact) -> str:
    payload = value.to_dict()
    payload.pop("tailored_resume_id", None)
    return _stable_hash(TAILORED_RESUME_ID_VERSION, payload)


def _apply_forward_moves(original: Sequence[str], moves: Mapping[str, int]) -> tuple[str, ...]:
    """Remove accepted targets, then insert by target position and original order."""
    values = tuple(original)
    unknown = set(moves) - set(values)
    if unknown:
        raise Phase2ValidationError("Tailoring move references an unknown layout item")
    original_positions = {item: index + 1 for index, item in enumerate(values)}
    remaining = [item for item in values if item not in moves]
    same_target_counts: dict[int, int] = {}
    for item in sorted(moves, key=lambda identifier: (moves[identifier], original_positions[identifier], identifier)):
        target = moves[item]
        if target >= original_positions[item]:
            raise Phase2ValidationError("Tailoring can only move an existing item earlier")
        offset = same_target_counts.get(target, 0)
        remaining.insert(min(target - 1 + offset, len(remaining)), item)
        same_target_counts[target] = offset + 1
    return tuple(remaining)


def _base_build_context(context: ValidatedResumeContext) -> dict[str, Any]:
    values = context.validation_context()
    values.pop("superseded_base_resume", None)
    return values


def _materialize(
    *,
    context: ValidatedResumeContext,
    section_layouts: Sequence[TailoredSectionLayout],
    entry_layouts: Sequence[TailoredEntryLayout],
    created_at: str,
) -> BaseResumeArtifact:
    values = _base_build_context(context)
    profile = values.pop("profile")
    material = values.pop("resume_material")
    wording = values.pop("resume_wording_review")
    supplement = values.pop("resume_profile_supplement")
    return build_base_resume(
        profile=profile,
        resume_material=material,
        resume_wording_review=wording,
        resume_profile_supplement=supplement,
        omitted_subject_ids=[item.subject_id for item in context.base_resume.omissions],
        section_order=[item.section_type for item in context.base_resume.sections],
        entry_order={item.section_type: item.entry_ids for item in section_layouts},
        bullet_order={item.entry_id: item.bullet_ids for item in entry_layouts},
        created_at=created_at,
        **values,
    )


def _draft_change(suggestion: Any, after_position: int) -> TailoringApplicationChange:
    outcome = (
        TailoringApplicationOutcome.RETAINED
        if suggestion.action == TailoringAction.RETAIN_BULLET
        else TailoringApplicationOutcome.MOVED
    )
    draft = TailoringApplicationChange(
        "tailoring_change_pending",
        suggestion.suggestion_id,
        suggestion.target_type,
        suggestion.target_id,
        suggestion.action,
        suggestion.current_position,
        after_position,
        outcome,
    )
    return TailoringApplicationChange.from_dict(
        {**draft.to_dict(), "change_id": generate_tailoring_change_id(draft)},
        "tailoring_change",
    )


def build_tailored_resume(
    *,
    context: ValidatedResumeContext,
    jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact,
    proposal: TailoringProposalArtifact,
    proposal_review: TailoringProposalReviewArtifact,
    created_at: str,
) -> TailoredResumeArtifact:
    """Apply accepted ordering suggestions; never change facts or wording."""
    context.validate()
    proposal_review.validate(
        proposal=proposal,
        context=context,
        jd_analysis=jd_analysis,
        match_report=match_report,
    )
    _iso_datetime(created_at, "created_at")
    suggestions = {item.suggestion_id: item for item in proposal.suggestions}
    decisions = {item.suggestion_id: item.decision for item in proposal_review.reviews}
    accepted = tuple(sorted(
        identifier for identifier, decision in decisions.items()
        if decision == TailoringReviewDecision.ACCEPTED
    ))
    accepted_values = [suggestions[item] for item in accepted]

    section_moves: dict[ResumeSectionType, dict[str, int]] = {}
    bullet_moves: dict[str, dict[str, int]] = {}
    for suggestion in accepted_values:
        if suggestion.action == TailoringAction.PRIORITIZE_ENTRY:
            section_moves.setdefault(suggestion.section_type, {})[suggestion.entry_id] = suggestion.suggested_position
        elif suggestion.action == TailoringAction.PRIORITIZE_BULLET:
            bullet_moves.setdefault(suggestion.entry_id, {})[suggestion.bullet_id] = suggestion.suggested_position

    section_layouts = tuple(
        TailoredSectionLayout(
            section.section_type,
            _apply_forward_moves(section.entry_ids, section_moves.get(section.section_type, {})),
        )
        for section in context.base_resume.sections
    )
    entry_layouts = tuple(sorted((
        TailoredEntryLayout(
            entry.entry_id,
            _apply_forward_moves(entry.bullet_ids, bullet_moves.get(entry.entry_id, {})),
        )
        for entry in context.base_resume.entries if entry.bullet_ids
    ), key=lambda item: item.entry_id))
    materialized = _materialize(
        context=context,
        section_layouts=section_layouts,
        entry_layouts=entry_layouts,
        created_at=created_at,
    )
    final_entry_positions = {
        identifier: index + 1
        for section in section_layouts
        for index, identifier in enumerate(section.entry_ids)
    }
    final_bullet_positions = {
        identifier: index + 1
        for entry in entry_layouts
        for index, identifier in enumerate(entry.bullet_ids)
    }
    changes = tuple(sorted((
        _draft_change(
            suggestion,
            (
                final_entry_positions[suggestion.entry_id]
                if suggestion.target_type == TailoringTargetType.ENTRY
                else final_bullet_positions[suggestion.bullet_id]
            ),
        )
        for suggestion in accepted_values
    ), key=lambda item: item.suggestion_id))
    counts = {item: 0 for item in TailoringReviewDecision}
    for decision in decisions.values():
        counts[decision] += 1
    summary = TailoredResumeSummary(
        len(accepted),
        sum(item.outcome == TailoringApplicationOutcome.MOVED for item in changes),
        sum(item.outcome == TailoringApplicationOutcome.RETAINED for item in changes),
        counts[TailoringReviewDecision.REJECTED],
        counts[TailoringReviewDecision.DEFERRED],
    )
    draft = TailoredResumeArtifact(
        "tailored-resume-v1_pending",
        context.base_resume.profile_fingerprint,
        context.base_resume.base_resume_id,
        proposal.tailoring_proposal_id,
        proposal_review.proposal_review_id,
        jd_analysis.jd_analysis_id,
        match_report.match_report_id,
        materialized.base_resume_id,
        created_at,
        section_layouts,
        entry_layouts,
        accepted,
        changes,
        summary,
    )
    return TailoredResumeArtifact.from_dict({
        **draft.to_dict(),
        "tailored_resume_id": generate_tailored_resume_id(draft),
    })


def materialize_tailored_resume(
    value: TailoredResumeArtifact,
    *,
    context: ValidatedResumeContext,
    jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact,
    proposal: TailoringProposalArtifact,
    proposal_review: TailoringProposalReviewArtifact,
) -> BaseResumeArtifact:
    value.validate(
        context=context,
        jd_analysis=jd_analysis,
        match_report=match_report,
        proposal=proposal,
        proposal_review=proposal_review,
    )
    result = _materialize(
        context=context,
        section_layouts=value.section_layouts,
        entry_layouts=value.entry_layouts,
        created_at=value.created_at,
    )
    if result.base_resume_id != value.materialized_base_resume_id:
        raise Phase2ValidationError("Tailored Resume materialized ID is stale")
    return result


def render_tailored_resume_markdown(value: TailoredResumeArtifact, **context: Any) -> str:
    resume = materialize_tailored_resume(value, **context)
    validation = _base_build_context(context["context"])
    validation.pop("superseded_base_resume", None)
    return render_base_resume_markdown(resume, **validation)


def render_tailoring_change_log(value: TailoredResumeArtifact, **context: Any) -> str:
    value.validate(**context)
    lines = [
        "# Tailoring Change Log",
        "",
        "> Ordering-only tailoring. No facts or wording were changed.",
        "",
        f"Accepted suggestions: {value.summary.accepted_suggestion_count}",
        f"Moved items: {value.summary.moved_count}",
        f"Retained bullets: {value.summary.retained_count}",
        f"Rejected suggestions: {value.summary.rejected_count}",
        f"Deferred suggestions: {value.summary.deferred_count}",
        "",
    ]
    for item in value.changes:
        lines.extend((
            f"## {item.action.value}",
            "",
            f"- Suggestion: `{item.suggestion_id}`",
            f"- Target: `{item.target_id}`",
            f"- Position: {item.before_position} -> {item.after_position}",
            f"- Outcome: {item.outcome.value}",
            "",
        ))
    if not value.changes:
        lines.extend(("No accepted suggestions were applied.", ""))
    return "\n".join(lines)


def save_tailored_resume(value: TailoredResumeArtifact, path: str | Path, **context: Any) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Tailored Resume already exists: {target}")
    value.validate(**context)
    return save_phase2_json(value.to_dict(), target)


def load_tailored_resume(path: str | Path, **context: Any) -> TailoredResumeArtifact:
    value = TailoredResumeArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value
