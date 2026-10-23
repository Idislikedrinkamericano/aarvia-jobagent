"""Traceable, review-only resume tailoring proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base_resume import BaseResumeArtifact, BaseResumeItemType
from .jd_matching import (
    JDAnalysisArtifact,
    JDMatchReportArtifact,
    JDMatchStatus,
    JDRequirementImportance,
)
from .phase2_storage import load_phase2_json, save_phase2_json
from .resume_context import ResumeFact, ValidatedResumeContext
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


TAILORING_PROPOSAL_SCHEMA = "aarvia.tailoring_proposal"
TAILORING_PROPOSAL_SCHEMA_VERSION = 1
TAILORING_PROPOSAL_ID_VERSION = "tailoring-proposal-v1"
TAILORING_REVIEW_SCHEMA = "aarvia.tailoring_proposal_review"
TAILORING_REVIEW_SCHEMA_VERSION = 1
TAILORING_REVIEW_ID_VERSION = "tailoring-proposal-review-v1"


class TailoringTargetType(str, Enum):
    ENTRY = "entry"
    BULLET = "bullet"


class TailoringAction(str, Enum):
    PRIORITIZE_ENTRY = "prioritize_entry"
    RETAIN_BULLET = "retain_bullet"
    PRIORITIZE_BULLET = "prioritize_bullet"


class TailoringReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class TailoringReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"


def _stable_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise Phase2ValidationError(f"{path} must be a positive integer")
    return value


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _text(value, path)


@dataclass(frozen=True, eq=True)
class TailoringSuggestion:
    suggestion_id: str
    target_type: TailoringTargetType
    target_id: str
    section_type: ResumeSectionType
    entry_id: str
    bullet_id: str | None
    current_position: int
    suggested_position: int
    original_content: str
    action: TailoringAction
    requirement_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    rationale: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoringSuggestion":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("suggestion_id"), f"{path}.suggestion_id"),
            _enum(data.get("target_type"), TailoringTargetType, f"{path}.target_type"),
            _stable_id(data.get("target_id"), f"{path}.target_id"),
            _enum(data.get("section_type"), ResumeSectionType, f"{path}.section_type"),
            _stable_id(data.get("entry_id"), f"{path}.entry_id"),
            None if data.get("bullet_id") is None else _stable_id(
                data.get("bullet_id"), f"{path}.bullet_id"
            ),
            _positive_int(data.get("current_position"), f"{path}.current_position"),
            _positive_int(data.get("suggested_position"), f"{path}.suggested_position"),
            _text(data.get("original_content"), f"{path}.original_content"),
            _enum(data.get("action"), TailoringAction, f"{path}.action"),
            _string_tuple(data.get("requirement_ids"), f"{path}.requirement_ids"),
            _string_tuple(data.get("fact_ids"), f"{path}.fact_ids"),
            _string_tuple(data.get("reason_codes"), f"{path}.reason_codes"),
            _text(data.get("rationale"), f"{path}.rationale"),
        )
        for name in ("requirement_ids", "fact_ids", "reason_codes"):
            items = getattr(result, name)
            if not items or items != tuple(sorted(set(items))):
                raise Phase2ValidationError(f"{path}.{name} must be non-empty, unique, and sorted")
        if result.target_type == TailoringTargetType.ENTRY:
            if result.bullet_id is not None or result.target_id != result.entry_id:
                raise Phase2ValidationError(f"{path} entry target references are inconsistent")
            if result.action != TailoringAction.PRIORITIZE_ENTRY:
                raise Phase2ValidationError(f"{path} entry target action is invalid")
        else:
            if result.bullet_id is None or result.target_id != result.bullet_id:
                raise Phase2ValidationError(f"{path} bullet target references are inconsistent")
            if result.action not in {
                TailoringAction.RETAIN_BULLET,
                TailoringAction.PRIORITIZE_BULLET,
            }:
                raise Phase2ValidationError(f"{path} bullet target action is invalid")
        if result.action == TailoringAction.RETAIN_BULLET:
            if result.current_position != result.suggested_position:
                raise Phase2ValidationError(f"{path} retained bullet position must not change")
        elif result.current_position == result.suggested_position:
            raise Phase2ValidationError(f"{path} priority suggestion must change position")
        if result.suggestion_id != generate_tailoring_suggestion_id(result):
            raise Phase2ValidationError(f"{path}.suggestion_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "section_type": self.section_type.value,
            "entry_id": self.entry_id,
            "bullet_id": self.bullet_id,
            "current_position": self.current_position,
            "suggested_position": self.suggested_position,
            "original_content": self.original_content,
            "action": self.action.value,
            "requirement_ids": list(self.requirement_ids),
            "fact_ids": list(self.fact_ids),
            "reason_codes": list(self.reason_codes),
            "rationale": self.rationale,
        }


def generate_tailoring_suggestion_id(value: TailoringSuggestion) -> str:
    payload = value.to_dict()
    payload.pop("suggestion_id", None)
    return _stable_hash("tailoring_suggestion", payload)


@dataclass(frozen=True, eq=True)
class TailoringProposalSummary:
    suggestion_count: int
    entry_suggestion_count: int
    bullet_suggestion_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoringProposalSummary":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        values = []
        for name in cls.__dataclass_fields__:
            raw = data.get(name)
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                raise Phase2ValidationError(f"{path}.{name} must be a non-negative integer")
            values.append(raw)
        result = cls(*values)
        if result.suggestion_count != result.entry_suggestion_count + result.bullet_suggestion_count:
            raise Phase2ValidationError(f"{path} counts do not add up")
        return result

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class TailoringProposalArtifact:
    tailoring_proposal_id: str
    profile_fingerprint: str
    base_resume_id: str
    resume_wording_review_id: str
    jd_analysis_id: str
    jd_content_fingerprint: str
    match_report_id: str
    created_at: str
    suggestions: tuple[TailoringSuggestion, ...]
    summary: TailoringProposalSummary
    schema: str = TAILORING_PROPOSAL_SCHEMA
    schema_version: int = TAILORING_PROPOSAL_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TailoringProposalArtifact":
        data = _mapping(value, "tailoring_proposal")
        _reject_unknown(data, set(cls.__dataclass_fields__), "tailoring_proposal")
        if data.get("schema") != TAILORING_PROPOSAL_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Tailoring Proposal schema")
        raw = data.get("suggestions")
        if not isinstance(raw, list):
            raise Phase2ValidationError("tailoring_proposal.suggestions must be a list")
        result = cls(
            _stable_id(data.get("tailoring_proposal_id"), "tailoring_proposal.tailoring_proposal_id"),
            _text(data.get("profile_fingerprint"), "tailoring_proposal.profile_fingerprint"),
            _stable_id(data.get("base_resume_id"), "tailoring_proposal.base_resume_id"),
            _stable_id(data.get("resume_wording_review_id"), "tailoring_proposal.resume_wording_review_id"),
            _stable_id(data.get("jd_analysis_id"), "tailoring_proposal.jd_analysis_id"),
            _text(data.get("jd_content_fingerprint"), "tailoring_proposal.jd_content_fingerprint"),
            _stable_id(data.get("match_report_id"), "tailoring_proposal.match_report_id"),
            _iso_datetime(data.get("created_at"), "tailoring_proposal.created_at"),
            tuple(
                TailoringSuggestion.from_dict(item, f"tailoring_proposal.suggestions[{index}]")
                for index, item in enumerate(raw)
            ),
            TailoringProposalSummary.from_dict(data.get("summary"), "tailoring_proposal.summary"),
        )
        ids = tuple(item.suggestion_id for item in result.suggestions)
        if ids != tuple(sorted(set(ids))):
            raise Phase2ValidationError("Tailoring Proposal suggestions must be unique and sorted")
        if result.summary != _proposal_summary(result.suggestions):
            raise Phase2ValidationError("Tailoring Proposal summary is not deterministic")
        if result.tailoring_proposal_id != generate_tailoring_proposal_id(result):
            raise Phase2ValidationError("Tailoring Proposal ID is not deterministic")
        return result

    def validate(
        self,
        *,
        context: ValidatedResumeContext,
        jd_analysis: JDAnalysisArtifact,
        match_report: JDMatchReportArtifact,
    ) -> None:
        canonical = TailoringProposalArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Tailoring Proposal is not canonical")
        rebuilt = build_tailoring_proposal(
            context=context,
            jd_analysis=jd_analysis,
            match_report=match_report,
            created_at=self.created_at,
        )
        if rebuilt != self:
            raise Phase2ValidationError("Tailoring Proposal content or provenance is stale")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "tailoring_proposal_id": self.tailoring_proposal_id,
            "profile_fingerprint": self.profile_fingerprint,
            "base_resume_id": self.base_resume_id,
            "resume_wording_review_id": self.resume_wording_review_id,
            "jd_analysis_id": self.jd_analysis_id,
            "jd_content_fingerprint": self.jd_content_fingerprint,
            "match_report_id": self.match_report_id,
            "created_at": self.created_at,
            "suggestions": [item.to_dict() for item in self.suggestions],
            "summary": self.summary.to_dict(),
        }


def generate_tailoring_proposal_id(value: TailoringProposalArtifact) -> str:
    payload = value.to_dict()
    payload.pop("tailoring_proposal_id", None)
    return _stable_hash(TAILORING_PROPOSAL_ID_VERSION, payload)


def _proposal_summary(suggestions: Sequence[TailoringSuggestion]) -> TailoringProposalSummary:
    return TailoringProposalSummary(
        len(suggestions),
        sum(item.target_type == TailoringTargetType.ENTRY for item in suggestions),
        sum(item.target_type == TailoringTargetType.BULLET for item in suggestions),
    )


def _fact_supports_bullet(fact: ResumeFact, bullet: Any) -> bool:
    return bool(
        fact.fact_id in bullet.evidence_item_ids
        or fact.fact_id in bullet.enrichment_claim_ids
        or set(fact.evidence_item_ids) & set(bullet.evidence_item_ids)
        or set(fact.enrichment_claim_ids) & set(bullet.enrichment_claim_ids)
    )


def _reason_codes(requirements: Sequence[Any]) -> tuple[str, ...]:
    values = {"confirmed_fact_in_reviewed_wording"}
    values.update(f"matched_{item.importance.value}_requirement" for item in requirements)
    return tuple(sorted(values))


def _score(requirements: Sequence[Any], statuses: Sequence[JDMatchStatus], fact_ids: Sequence[str]) -> tuple[int, int, int]:
    importance = {
        JDRequirementImportance.REQUIRED: 3,
        JDRequirementImportance.PREFERRED: 2,
        JDRequirementImportance.UNSPECIFIED: 1,
    }
    support = {
        JDMatchStatus.SATISFIED: 2,
        JDMatchStatus.PARTIALLY_SUPPORTED: 1,
    }
    return (
        sum(importance[item.importance] for item in requirements)
        + sum(support[item] for item in statuses),
        len({item.requirement_id for item in requirements}),
        len(set(fact_ids)),
    )


def _draft_suggestion(**values: Any) -> TailoringSuggestion:
    draft = TailoringSuggestion(suggestion_id="tailoring_suggestion_pending", **values)
    return TailoringSuggestion.from_dict(
        {**draft.to_dict(), "suggestion_id": generate_tailoring_suggestion_id(draft)},
        "tailoring_suggestion",
    )


def build_tailoring_proposal(
    *,
    context: ValidatedResumeContext,
    jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact,
    created_at: str,
) -> TailoringProposalArtifact:
    """Build review-only ordering and selection suggestions from verified references."""
    match_report.validate(jd_analysis=jd_analysis, context=context)
    _iso_datetime(created_at, "created_at")
    fact_by_id = context.fact_index().fact_by_id
    requirement_by_id = {item.requirement_id: item for item in jd_analysis.requirements}
    base = context.base_resume
    bullet_by_id = {item.bullet_id: item for item in base.bullets}
    entry_by_id = {item.entry_id: item for item in base.entries}
    section_by_entry: dict[str, ResumeSectionType] = {}
    entry_positions: dict[str, int] = {}
    for section in base.sections:
        for position, entry_id in enumerate(section.entry_ids, 1):
            section_by_entry[entry_id] = section.section_type
            entry_positions[entry_id] = position

    bullet_matches: dict[str, list[tuple[Any, JDMatchStatus, str]]] = {}
    for result in match_report.results:
        if result.status not in {JDMatchStatus.SATISFIED, JDMatchStatus.PARTIALLY_SUPPORTED}:
            continue
        requirement = requirement_by_id[result.requirement_id]
        for reference in result.evidence_references:
            fact = fact_by_id[reference.fact_id]
            for bullet in base.bullets:
                if _fact_supports_bullet(fact, bullet):
                    bullet_matches.setdefault(bullet.bullet_id, []).append(
                        (requirement, result.status, fact.fact_id)
                    )

    bullet_scores: dict[str, tuple[int, int, int]] = {}
    suggestions: list[TailoringSuggestion] = []
    for entry in base.entries:
        if entry.item_type not in {BaseResumeItemType.EXPERIENCE, BaseResumeItemType.PROJECT}:
            continue
        current_ids = entry.bullet_ids
        matched_ids = [item for item in current_ids if item in bullet_matches]
        if not matched_ids:
            continue
        for bullet_id in matched_ids:
            matches = bullet_matches[bullet_id]
            bullet_scores[bullet_id] = _score(
                [item[0] for item in matches],
                [item[1] for item in matches],
                [item[2] for item in matches],
            )
        proposed_order = tuple(sorted(
            current_ids,
            key=lambda item: (
                0 if item in bullet_scores else 1,
                tuple(-value for value in bullet_scores.get(item, (0, 0, 0))),
                current_ids.index(item),
            ),
        ))
        for bullet_id in matched_ids:
            matches = bullet_matches[bullet_id]
            requirements = tuple({item[0].requirement_id: item[0] for item in matches}.values())
            requirement_ids = tuple(sorted(item.requirement_id for item in requirements))
            fact_ids = tuple(sorted({item[2] for item in matches}))
            current = current_ids.index(bullet_id) + 1
            proposed = proposed_order.index(bullet_id) + 1
            if proposed > current:
                continue
            action = (
                TailoringAction.RETAIN_BULLET
                if current == proposed
                else TailoringAction.PRIORITIZE_BULLET
            )
            suggestions.append(_draft_suggestion(
                target_type=TailoringTargetType.BULLET,
                target_id=bullet_id,
                section_type=section_by_entry[entry.entry_id],
                entry_id=entry.entry_id,
                bullet_id=bullet_id,
                current_position=current,
                suggested_position=proposed,
                original_content=bullet_by_id[bullet_id].text,
                action=action,
                requirement_ids=requirement_ids,
                fact_ids=fact_ids,
                reason_codes=_reason_codes(requirements),
                rationale=(
                    "Keep this reviewed wording because confirmed facts support the cited JD requirements."
                    if action == TailoringAction.RETAIN_BULLET
                    else "Move this reviewed wording earlier because confirmed facts support higher-priority cited JD requirements."
                ),
            ))

    for section in base.sections:
        if section.section_type not in {ResumeSectionType.EXPERIENCE, ResumeSectionType.PROJECTS}:
            continue
        entry_scores: dict[str, tuple[int, int, int]] = {}
        entry_matches: dict[str, list[tuple[Any, JDMatchStatus, str]]] = {}
        for entry_id in section.entry_ids:
            entry = entry_by_id[entry_id]
            matches = [value for bullet_id in entry.bullet_ids for value in bullet_matches.get(bullet_id, ())]
            if not matches:
                continue
            entry_matches[entry_id] = matches
            entry_scores[entry_id] = _score(
                [item[0] for item in matches],
                [item[1] for item in matches],
                [item[2] for item in matches],
            )
        proposed_order = tuple(sorted(
            section.entry_ids,
            key=lambda item: (
                0 if item in entry_scores else 1,
                tuple(-value for value in entry_scores.get(item, (0, 0, 0))),
                section.entry_ids.index(item),
            ),
        ))
        for entry_id, matches in entry_matches.items():
            current = entry_positions[entry_id]
            proposed = proposed_order.index(entry_id) + 1
            if proposed >= current:
                continue
            entry = entry_by_id[entry_id]
            requirements = tuple({item[0].requirement_id: item[0] for item in matches}.values())
            suggestions.append(_draft_suggestion(
                target_type=TailoringTargetType.ENTRY,
                target_id=entry_id,
                section_type=section.section_type,
                entry_id=entry_id,
                bullet_id=None,
                current_position=current,
                suggested_position=proposed,
                original_content=json.dumps(dict(entry.fields), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                action=TailoringAction.PRIORITIZE_ENTRY,
                requirement_ids=tuple(sorted(item.requirement_id for item in requirements)),
                fact_ids=tuple(sorted({item[2] for item in matches})),
                reason_codes=_reason_codes(requirements),
                rationale="Move this existing entry earlier in its current section because its reviewed bullets cite confirmed facts relevant to the JD.",
            ))

    ordered = tuple(sorted(suggestions, key=lambda item: item.suggestion_id))
    summary = _proposal_summary(ordered)
    draft = TailoringProposalArtifact(
        "tailoring_proposal_pending",
        base.profile_fingerprint,
        base.base_resume_id,
        context.resume_wording_review.wording_review_id,
        jd_analysis.jd_analysis_id,
        jd_analysis.content_fingerprint,
        match_report.match_report_id,
        created_at,
        ordered,
        summary,
    )
    return TailoringProposalArtifact.from_dict({
        **draft.to_dict(),
        "tailoring_proposal_id": generate_tailoring_proposal_id(draft),
    })


def render_tailoring_proposal_report(
    value: TailoringProposalArtifact,
    *,
    context: ValidatedResumeContext,
    jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact,
) -> str:
    value.validate(context=context, jd_analysis=jd_analysis, match_report=match_report)
    requirements = {item.requirement_id: item for item in jd_analysis.requirements}
    lines = [
        "# Tailoring Proposal",
        "",
        "> Review-only suggestions. No resume content has been changed.",
        "",
        f"Suggestions: {value.summary.suggestion_count}",
        "",
    ]
    for index, item in enumerate(value.suggestions, 1):
        lines.extend((
            f"## {index}. {item.action.value}",
            "",
            f"- Target: `{item.target_id}` ({item.section_type.value})",
            f"- Position: {item.current_position} -> {item.suggested_position}",
            f"- Current content: {item.original_content}",
            f"- Rationale: {item.rationale}",
            "- JD requirements:",
        ))
        lines.extend(f"  - {requirements[identifier].exact_quote}" for identifier in item.requirement_ids)
        lines.append("- Confirmed fact references: " + ", ".join(f"`{identifier}`" for identifier in item.fact_ids))
        lines.append("")
    lines.extend((
        "Matching reasoning is not treated as a career fact. Every suggestion remains unreviewed until the profile owner saves a separate review artifact.",
        "",
    ))
    return "\n".join(lines)


def save_tailoring_proposal(value: TailoringProposalArtifact, path: str | Path, **context: Any) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Tailoring Proposal already exists: {target}")
    value.validate(**context)
    return save_phase2_json(value.to_dict(), target)


def load_tailoring_proposal(path: str | Path, **context: Any) -> TailoringProposalArtifact:
    value = TailoringProposalArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value


@dataclass(frozen=True, eq=True)
class TailoringSuggestionReview:
    review_id: str
    suggestion_id: str
    decision: TailoringReviewDecision
    reviewer_type: TailoringReviewerType
    reviewed_at: str
    reason: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "TailoringSuggestionReview":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("review_id"), f"{path}.review_id"),
            _stable_id(data.get("suggestion_id"), f"{path}.suggestion_id"),
            _enum(data.get("decision"), TailoringReviewDecision, f"{path}.decision"),
            _enum(data.get("reviewer_type"), TailoringReviewerType, f"{path}.reviewer_type"),
            _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            _optional_text(data.get("reason"), f"{path}.reason"),
        )
        if result.review_id != generate_tailoring_review_id(result):
            raise Phase2ValidationError(f"{path}.review_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "suggestion_id": self.suggestion_id,
            "decision": self.decision.value,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
            "reason": self.reason,
        }


def generate_tailoring_review_id(value: TailoringSuggestionReview) -> str:
    payload = value.to_dict()
    payload.pop("review_id", None)
    return _stable_hash("tailoring_suggestion_review", payload)


@dataclass(frozen=True, eq=True)
class TailoringProposalReviewArtifact:
    proposal_review_id: str
    tailoring_proposal_id: str
    profile_fingerprint: str
    base_resume_id: str
    jd_analysis_id: str
    match_report_id: str
    created_at: str
    reviews: tuple[TailoringSuggestionReview, ...]
    schema: str = TAILORING_REVIEW_SCHEMA
    schema_version: int = TAILORING_REVIEW_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TailoringProposalReviewArtifact":
        data = _mapping(value, "tailoring_proposal_review")
        _reject_unknown(data, set(cls.__dataclass_fields__), "tailoring_proposal_review")
        if data.get("schema") != TAILORING_REVIEW_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Tailoring Proposal Review schema")
        raw = data.get("reviews")
        if not isinstance(raw, list):
            raise Phase2ValidationError("tailoring_proposal_review.reviews must be a list")
        result = cls(
            _stable_id(data.get("proposal_review_id"), "tailoring_proposal_review.proposal_review_id"),
            _stable_id(data.get("tailoring_proposal_id"), "tailoring_proposal_review.tailoring_proposal_id"),
            _text(data.get("profile_fingerprint"), "tailoring_proposal_review.profile_fingerprint"),
            _stable_id(data.get("base_resume_id"), "tailoring_proposal_review.base_resume_id"),
            _stable_id(data.get("jd_analysis_id"), "tailoring_proposal_review.jd_analysis_id"),
            _stable_id(data.get("match_report_id"), "tailoring_proposal_review.match_report_id"),
            _iso_datetime(data.get("created_at"), "tailoring_proposal_review.created_at"),
            tuple(
                TailoringSuggestionReview.from_dict(item, f"tailoring_proposal_review.reviews[{index}]")
                for index, item in enumerate(raw)
            ),
        )
        ids = tuple(item.suggestion_id for item in result.reviews)
        if ids != tuple(sorted(set(ids))):
            raise Phase2ValidationError("Tailoring Proposal reviews must be unique and sorted by suggestion")
        if result.proposal_review_id != generate_tailoring_review_artifact_id(result):
            raise Phase2ValidationError("Tailoring Proposal Review ID is not deterministic")
        return result

    def validate(
        self,
        *,
        proposal: TailoringProposalArtifact,
        context: ValidatedResumeContext,
        jd_analysis: JDAnalysisArtifact,
        match_report: JDMatchReportArtifact,
    ) -> None:
        canonical = TailoringProposalReviewArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Tailoring Proposal Review is not canonical")
        proposal.validate(context=context, jd_analysis=jd_analysis, match_report=match_report)
        expected = (
            proposal.tailoring_proposal_id,
            proposal.profile_fingerprint,
            proposal.base_resume_id,
            proposal.jd_analysis_id,
            proposal.match_report_id,
        )
        actual = (
            self.tailoring_proposal_id,
            self.profile_fingerprint,
            self.base_resume_id,
            self.jd_analysis_id,
            self.match_report_id,
        )
        if actual != expected:
            raise Phase2ValidationError("Tailoring Proposal Review provenance is stale")
        if tuple(item.suggestion_id for item in self.reviews) != tuple(
            sorted(item.suggestion_id for item in proposal.suggestions)
        ):
            raise Phase2ValidationError("Tailoring Proposal Review must decide every current suggestion exactly once")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "proposal_review_id": self.proposal_review_id,
            "tailoring_proposal_id": self.tailoring_proposal_id,
            "profile_fingerprint": self.profile_fingerprint,
            "base_resume_id": self.base_resume_id,
            "jd_analysis_id": self.jd_analysis_id,
            "match_report_id": self.match_report_id,
            "created_at": self.created_at,
            "reviews": [item.to_dict() for item in self.reviews],
        }


def generate_tailoring_review_artifact_id(value: TailoringProposalReviewArtifact) -> str:
    payload = value.to_dict()
    payload.pop("proposal_review_id", None)
    return _stable_hash(TAILORING_REVIEW_ID_VERSION, payload)


def create_tailoring_proposal_review(
    *,
    proposal: TailoringProposalArtifact,
    decisions: Mapping[str, TailoringReviewDecision | str],
    reviewed_at: str,
    reason_by_suggestion: Mapping[str, str | None] | None = None,
    context: ValidatedResumeContext,
    jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact,
) -> TailoringProposalReviewArtifact:
    proposal.validate(context=context, jd_analysis=jd_analysis, match_report=match_report)
    expected = {item.suggestion_id for item in proposal.suggestions}
    if set(decisions) != expected:
        raise Phase2ValidationError("Tailoring Proposal Review decisions must match current suggestions")
    reasons = {} if reason_by_suggestion is None else dict(reason_by_suggestion)
    if not set(reasons) <= expected:
        raise Phase2ValidationError("Tailoring Proposal Review reason references an unknown suggestion")
    reviewed = _iso_datetime(reviewed_at, "reviewed_at")
    reviews = []
    for suggestion_id in sorted(expected):
        decision = _enum(decisions[suggestion_id], TailoringReviewDecision, "decision")
        reason = reasons.get(suggestion_id)
        draft = TailoringSuggestionReview(
            "tailoring_suggestion_review_pending",
            suggestion_id,
            decision,
            TailoringReviewerType.PROFILE_OWNER,
            reviewed,
            reason,
        )
        reviews.append(TailoringSuggestionReview.from_dict({
            **draft.to_dict(),
            "review_id": generate_tailoring_review_id(draft),
        }, "tailoring_suggestion_review"))
    draft = TailoringProposalReviewArtifact(
        "tailoring_proposal_review_pending",
        proposal.tailoring_proposal_id,
        proposal.profile_fingerprint,
        proposal.base_resume_id,
        proposal.jd_analysis_id,
        proposal.match_report_id,
        reviewed,
        tuple(reviews),
    )
    result = TailoringProposalReviewArtifact.from_dict({
        **draft.to_dict(),
        "proposal_review_id": generate_tailoring_review_artifact_id(draft),
    })
    result.validate(
        proposal=proposal,
        context=context,
        jd_analysis=jd_analysis,
        match_report=match_report,
    )
    return result


def accepted_tailoring_suggestions(
    proposal: TailoringProposalArtifact,
    review: TailoringProposalReviewArtifact,
    **context: Any,
) -> tuple[TailoringSuggestion, ...]:
    review.validate(proposal=proposal, **context)
    decisions = {item.suggestion_id: item.decision for item in review.reviews}
    return tuple(
        item for item in proposal.suggestions
        if decisions[item.suggestion_id] == TailoringReviewDecision.ACCEPTED
    )


def save_tailoring_proposal_review(
    value: TailoringProposalReviewArtifact,
    path: str | Path,
    **context: Any,
) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Tailoring Proposal Review already exists: {target}")
    value.validate(**context)
    return save_phase2_json(value.to_dict(), target)


def load_tailoring_proposal_review(
    path: str | Path,
    **context: Any,
) -> TailoringProposalReviewArtifact:
    value = TailoringProposalReviewArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value
