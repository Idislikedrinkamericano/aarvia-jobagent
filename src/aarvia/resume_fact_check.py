"""Deterministic final-resume trace checks plus profile-owner semantic review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base_resume import BaseResumeArtifact, _render_validated_base_resume_markdown
from .jd_matching import JDAnalysisArtifact, JDMatchReportArtifact
from .phase2_storage import load_phase2_json, save_phase2_json
from .resume_context import ValidatedResumeContext
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
from .tailored_resume import TailoredResumeArtifact, materialize_tailored_resume
from .tailored_wording import (
    TailoredWordingProposalArtifact,
    TailoredWordingReviewArtifact,
    WordingTailoredResumeArtifact,
)
from .tailoring_proposal import TailoringProposalArtifact, TailoringProposalReviewArtifact


FINAL_RESUME_FACT_CHECK_SCHEMA = "aarvia.final_resume_fact_check"
FINAL_RESUME_FACT_CHECK_SCHEMA_VERSION = 1


class FinalResumeKind(str, Enum):
    ORDERING_ONLY = "ordering_only"
    WORDING_OVERLAY = "wording_overlay"


class ResumeSemanticReviewStatus(str, Enum):
    PASSED = "passed"
    ISSUE_FOUND = "issue_found"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ResumeFactCheckReviewerType(str, Enum):
    PROFILE_OWNER = "profile_owner"


def _stable_hash(prefix: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _optional_id(value: Any, path: str) -> str | None:
    return None if value is None else _stable_id(value, path)


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _text(value, path)


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Phase2ValidationError(f"{path} must be a non-negative integer")
    return value


@dataclass(frozen=True, eq=True)
class FinalResumeBulletCheck:
    bullet_check_id: str
    bullet_id: str
    entry_id: str
    source_record_id: str
    original_text: str
    final_text: str
    wording_changed: bool
    base_wording_candidate_id: str
    base_wording_review_id: str
    applied_wording_candidate_id: str | None
    applied_wording_review_id: str | None
    material_ids: tuple[str, ...]
    evidence_item_ids: tuple[str, ...]
    enrichment_claim_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "FinalResumeBulletCheck":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        changed = data.get("wording_changed")
        if not isinstance(changed, bool):
            raise Phase2ValidationError(f"{path}.wording_changed must be boolean")
        result = cls(
            _stable_id(data.get("bullet_check_id"), f"{path}.bullet_check_id"),
            _stable_id(data.get("bullet_id"), f"{path}.bullet_id"),
            _stable_id(data.get("entry_id"), f"{path}.entry_id"),
            _stable_id(data.get("source_record_id"), f"{path}.source_record_id"),
            _text(data.get("original_text"), f"{path}.original_text"),
            _text(data.get("final_text"), f"{path}.final_text"),
            changed,
            _stable_id(data.get("base_wording_candidate_id"), f"{path}.base_wording_candidate_id"),
            _stable_id(data.get("base_wording_review_id"), f"{path}.base_wording_review_id"),
            _optional_id(data.get("applied_wording_candidate_id"), f"{path}.applied_wording_candidate_id"),
            _optional_id(data.get("applied_wording_review_id"), f"{path}.applied_wording_review_id"),
            _string_tuple(data.get("material_ids"), f"{path}.material_ids"),
            _string_tuple(data.get("evidence_item_ids"), f"{path}.evidence_item_ids"),
            _string_tuple(data.get("enrichment_claim_ids"), f"{path}.enrichment_claim_ids"),
            _string_tuple(data.get("fact_ids"), f"{path}.fact_ids"),
        )
        for name in ("material_ids", "evidence_item_ids", "enrichment_claim_ids", "fact_ids"):
            items = getattr(result, name)
            if not items or items != tuple(sorted(set(items))):
                raise Phase2ValidationError(f"{path}.{name} must be non-empty, unique, and sorted")
        applied = (result.applied_wording_candidate_id, result.applied_wording_review_id)
        if result.wording_changed:
            if result.original_text == result.final_text or None in applied:
                raise Phase2ValidationError(f"{path} changed wording provenance is incomplete")
        elif result.original_text != result.final_text or applied != (None, None):
            raise Phase2ValidationError(f"{path} unchanged wording provenance is inconsistent")
        if result.bullet_check_id != generate_final_resume_bullet_check_id(result):
            raise Phase2ValidationError(f"{path}.bullet_check_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "bullet_check_id": self.bullet_check_id,
            "bullet_id": self.bullet_id,
            "entry_id": self.entry_id,
            "source_record_id": self.source_record_id,
            "original_text": self.original_text,
            "final_text": self.final_text,
            "wording_changed": self.wording_changed,
            "base_wording_candidate_id": self.base_wording_candidate_id,
            "base_wording_review_id": self.base_wording_review_id,
            "applied_wording_candidate_id": self.applied_wording_candidate_id,
            "applied_wording_review_id": self.applied_wording_review_id,
            "material_ids": list(self.material_ids),
            "evidence_item_ids": list(self.evidence_item_ids),
            "enrichment_claim_ids": list(self.enrichment_claim_ids),
            "fact_ids": list(self.fact_ids),
        }


def generate_final_resume_bullet_check_id(value: FinalResumeBulletCheck) -> str:
    payload = value.to_dict()
    payload.pop("bullet_check_id", None)
    return _stable_hash("final_resume_bullet_check", payload)


@dataclass(frozen=True, eq=True)
class FinalResumeBulletReview:
    review_id: str
    bullet_check_id: str
    status: ResumeSemanticReviewStatus
    reviewer_type: ResumeFactCheckReviewerType
    reviewed_at: str
    reason: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "FinalResumeBulletReview":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(
            _stable_id(data.get("review_id"), f"{path}.review_id"),
            _stable_id(data.get("bullet_check_id"), f"{path}.bullet_check_id"),
            _enum(data.get("status"), ResumeSemanticReviewStatus, f"{path}.status"),
            _enum(data.get("reviewer_type"), ResumeFactCheckReviewerType, f"{path}.reviewer_type"),
            _iso_datetime(data.get("reviewed_at"), f"{path}.reviewed_at"),
            _optional_text(data.get("reason"), f"{path}.reason"),
        )
        if result.status != ResumeSemanticReviewStatus.PASSED and result.reason is None:
            raise Phase2ValidationError(f"{path}.reason is required for an issue or uncertainty")
        if result.review_id != generate_final_resume_bullet_review_id(result):
            raise Phase2ValidationError(f"{path}.review_id is not deterministic")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "bullet_check_id": self.bullet_check_id,
            "status": self.status.value,
            "reviewer_type": self.reviewer_type.value,
            "reviewed_at": self.reviewed_at,
            "reason": self.reason,
        }


def generate_final_resume_bullet_review_id(value: FinalResumeBulletReview) -> str:
    payload = value.to_dict()
    payload.pop("review_id", None)
    return _stable_hash("final_resume_bullet_review", payload)


@dataclass(frozen=True, eq=True)
class FinalResumeFactCheckSummary:
    bullet_count: int
    changed_bullet_count: int
    passed_count: int
    issue_count: int
    needs_confirmation_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], path: str) -> "FinalResumeFactCheckSummary":
        data = _mapping(value, path)
        _reject_unknown(data, set(cls.__dataclass_fields__), path)
        result = cls(*(_count(data.get(name), f"{path}.{name}") for name in cls.__dataclass_fields__))
        if result.bullet_count != result.passed_count + result.issue_count + result.needs_confirmation_count:
            raise Phase2ValidationError(f"{path} review counts do not cover every bullet")
        if result.changed_bullet_count > result.bullet_count:
            raise Phase2ValidationError(f"{path}.changed_bullet_count is invalid")
        return result

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, eq=True)
class FinalResumeFactCheckArtifact:
    fact_check_id: str
    profile_fingerprint: str
    final_resume_kind: FinalResumeKind
    tailored_resume_id: str
    wording_tailored_resume_id: str | None
    wording_proposal_id: str | None
    wording_review_id: str | None
    jd_analysis_id: str
    match_report_id: str
    final_resume_content_hash: str
    created_at: str
    bullet_checks: tuple[FinalResumeBulletCheck, ...]
    reviews: tuple[FinalResumeBulletReview, ...]
    summary: FinalResumeFactCheckSummary
    schema: str = FINAL_RESUME_FACT_CHECK_SCHEMA
    schema_version: int = FINAL_RESUME_FACT_CHECK_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FinalResumeFactCheckArtifact":
        data = _mapping(value, "final_resume_fact_check")
        _reject_unknown(data, set(cls.__dataclass_fields__), "final_resume_fact_check")
        if data.get("schema") != FINAL_RESUME_FACT_CHECK_SCHEMA or data.get("schema_version") != 1:
            raise Phase2ValidationError("unsupported Final Resume Fact Check schema")
        raw_checks, raw_reviews = data.get("bullet_checks"), data.get("reviews")
        if not isinstance(raw_checks, list) or not isinstance(raw_reviews, list):
            raise Phase2ValidationError("Final Resume Fact Check checks and reviews must be lists")
        result = cls(
            _stable_id(data.get("fact_check_id"), "final_resume_fact_check.fact_check_id"),
            _text(data.get("profile_fingerprint"), "final_resume_fact_check.profile_fingerprint"),
            _enum(data.get("final_resume_kind"), FinalResumeKind, "final_resume_fact_check.final_resume_kind"),
            _stable_id(data.get("tailored_resume_id"), "final_resume_fact_check.tailored_resume_id"),
            _optional_id(data.get("wording_tailored_resume_id"), "final_resume_fact_check.wording_tailored_resume_id"),
            _optional_id(data.get("wording_proposal_id"), "final_resume_fact_check.wording_proposal_id"),
            _optional_id(data.get("wording_review_id"), "final_resume_fact_check.wording_review_id"),
            _stable_id(data.get("jd_analysis_id"), "final_resume_fact_check.jd_analysis_id"),
            _stable_id(data.get("match_report_id"), "final_resume_fact_check.match_report_id"),
            _text(data.get("final_resume_content_hash"), "final_resume_fact_check.final_resume_content_hash"),
            _iso_datetime(data.get("created_at"), "final_resume_fact_check.created_at"),
            tuple(FinalResumeBulletCheck.from_dict(item, f"final_resume_fact_check.bullet_checks[{i}]") for i, item in enumerate(raw_checks)),
            tuple(FinalResumeBulletReview.from_dict(item, f"final_resume_fact_check.reviews[{i}]") for i, item in enumerate(raw_reviews)),
            FinalResumeFactCheckSummary.from_dict(data.get("summary"), "final_resume_fact_check.summary"),
        )
        check_ids = tuple(item.bullet_check_id for item in result.bullet_checks)
        review_ids = tuple(item.bullet_check_id for item in result.reviews)
        if len(check_ids) != len(set(check_ids)) or review_ids != check_ids:
            raise Phase2ValidationError("Final Resume Fact Check must review every unique check in canonical order")
        overlay_values = (
            result.wording_tailored_resume_id,
            result.wording_proposal_id,
            result.wording_review_id,
        )
        if result.final_resume_kind == FinalResumeKind.ORDERING_ONLY:
            if overlay_values != (None, None, None):
                raise Phase2ValidationError("ordering-only fact check cannot reference a wording overlay")
        elif None in overlay_values:
            raise Phase2ValidationError("wording-overlay fact check provenance is incomplete")
        if not result.final_resume_content_hash.startswith("sha256:") or len(result.final_resume_content_hash) != 71:
            raise Phase2ValidationError("Final Resume content hash is invalid")
        if result.summary != _summary(result.bullet_checks, result.reviews):
            raise Phase2ValidationError("Final Resume Fact Check summary is not deterministic")
        if result.fact_check_id != generate_final_resume_fact_check_id(result):
            raise Phase2ValidationError("Final Resume Fact Check ID is not deterministic")
        return result

    def validate(self, **context: Any) -> None:
        canonical = FinalResumeFactCheckArtifact.from_dict(self.to_dict())
        if canonical != self:
            raise Phase2ValidationError("Final Resume Fact Check is not canonical")
        rebuilt = build_final_resume_fact_check(
            decisions={item.bullet_check_id: review.status for item, review in zip(self.bullet_checks, self.reviews)},
            reasons={item.bullet_check_id: review.reason for item, review in zip(self.bullet_checks, self.reviews)},
            created_at=self.created_at,
            **context,
        )
        if rebuilt != self:
            raise Phase2ValidationError("Final Resume Fact Check provenance or content is stale")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "fact_check_id": self.fact_check_id,
            "profile_fingerprint": self.profile_fingerprint,
            "final_resume_kind": self.final_resume_kind.value,
            "tailored_resume_id": self.tailored_resume_id,
            "wording_tailored_resume_id": self.wording_tailored_resume_id,
            "wording_proposal_id": self.wording_proposal_id,
            "wording_review_id": self.wording_review_id,
            "jd_analysis_id": self.jd_analysis_id,
            "match_report_id": self.match_report_id,
            "final_resume_content_hash": self.final_resume_content_hash,
            "created_at": self.created_at,
            "bullet_checks": [item.to_dict() for item in self.bullet_checks],
            "reviews": [item.to_dict() for item in self.reviews],
            "summary": self.summary.to_dict(),
        }


def generate_final_resume_fact_check_id(value: FinalResumeFactCheckArtifact) -> str:
    payload = value.to_dict()
    payload.pop("fact_check_id", None)
    return _stable_hash("final-resume-fact-check-v1", payload)


def _summary(
    checks: Sequence[FinalResumeBulletCheck],
    reviews: Sequence[FinalResumeBulletReview],
) -> FinalResumeFactCheckSummary:
    statuses = [item.status for item in reviews]
    return FinalResumeFactCheckSummary(
        len(checks),
        sum(item.wording_changed for item in checks),
        statuses.count(ResumeSemanticReviewStatus.PASSED),
        statuses.count(ResumeSemanticReviewStatus.ISSUE_FOUND),
        statuses.count(ResumeSemanticReviewStatus.NEEDS_CONFIRMATION),
    )


def _ordering_validation(
    *, context: ValidatedResumeContext, jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact, tailoring_proposal: TailoringProposalArtifact,
    tailoring_review: TailoringProposalReviewArtifact,
) -> dict[str, Any]:
    return dict(
        context=context, jd_analysis=jd_analysis, match_report=match_report,
        proposal=tailoring_proposal, proposal_review=tailoring_review,
    )


def _fact_matches_bullet(fact: Any, bullet: Any) -> bool:
    return bool(
        fact.fact_id in bullet.evidence_item_ids
        or fact.fact_id in bullet.enrichment_claim_ids
        or set(fact.evidence_item_ids).intersection(bullet.evidence_item_ids)
        or set(fact.enrichment_claim_ids).intersection(bullet.enrichment_claim_ids)
    )


def prepare_final_resume_bullet_checks(
    *, context: ValidatedResumeContext, jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact, tailoring_proposal: TailoringProposalArtifact,
    tailoring_review: TailoringProposalReviewArtifact,
    tailored_resume: TailoredResumeArtifact,
    wording_proposal: TailoredWordingProposalArtifact | None = None,
    wording_review: TailoredWordingReviewArtifact | None = None,
    wording_tailored_resume: WordingTailoredResumeArtifact | None = None,
) -> tuple[tuple[FinalResumeBulletCheck, ...], BaseResumeArtifact, str, FinalResumeKind]:
    overlay = (wording_proposal, wording_review, wording_tailored_resume)
    if any(item is None for item in overlay) and any(item is not None for item in overlay):
        raise Phase2ValidationError("wording Proposal, Review, and final overlay must be supplied together")
    ordered = materialize_tailored_resume(
        tailored_resume,
        **_ordering_validation(
            context=context, jd_analysis=jd_analysis, match_report=match_report,
            tailoring_proposal=tailoring_proposal, tailoring_review=tailoring_review,
        ),
    )
    changes = {}
    kind = FinalResumeKind.ORDERING_ONLY
    if wording_tailored_resume is not None:
        wording_tailored_resume.validate(
            proposal=wording_proposal, review=wording_review,
            context=context, jd_analysis=jd_analysis, match_report=match_report,
            tailoring_proposal=tailoring_proposal, tailoring_review=tailoring_review,
            tailored_resume=tailored_resume,
        )
        changes = {item.original_bullet_id: item for item in wording_tailored_resume.changes}
        kind = FinalResumeKind.WORDING_OVERLAY
    bullets = {item.bullet_id: item for item in ordered.bullets}
    bullet_entries: dict[str, str] = {}
    for entry in ordered.entries:
        for bullet_id in entry.bullet_ids:
            if bullet_id in bullet_entries:
                raise Phase2ValidationError("Final Resume contains duplicate bullet membership")
            bullet_entries[bullet_id] = entry.entry_id
    if set(bullet_entries) != set(bullets):
        raise Phase2ValidationError("Final Resume bullet membership is incomplete")
    entries = {item.entry_id: item for item in ordered.entries}
    bullet_order = tuple(
        bullet_id
        for section in ordered.sections
        for entry_id in section.entry_ids
        for bullet_id in entries[entry_id].bullet_ids
    )
    fact_index = context.fact_index()
    checks: list[FinalResumeBulletCheck] = []
    overrides: dict[str, str] = {}
    for bullet_id in bullet_order:
        bullet = bullets[bullet_id]
        change = changes.get(bullet_id)
        final_text = bullet.text if change is None else change.final_text
        if change is not None:
            overrides[bullet_id] = final_text
        fact_ids = tuple(sorted(
            fact.fact_id for fact in fact_index.facts if _fact_matches_bullet(fact, bullet)
        ))
        draft = FinalResumeBulletCheck(
            "final_resume_bullet_check_pending", bullet_id, bullet_entries[bullet_id],
            bullet.source_record_id, bullet.text, final_text, change is not None,
            bullet.wording_candidate_id, bullet.wording_review_id,
            None if change is None else change.wording_candidate_id,
            None if change is None else change.wording_review_id,
            bullet.material_ids, bullet.evidence_item_ids,
            bullet.enrichment_claim_ids, fact_ids,
        )
        checks.append(FinalResumeBulletCheck.from_dict({
            **draft.to_dict(),
            "bullet_check_id": generate_final_resume_bullet_check_id(draft),
        }, "final_resume_bullet_check"))
    markdown = _render_validated_base_resume_markdown(ordered, overrides)
    return tuple(checks), ordered, markdown, kind


def _normalize_status(value: ResumeSemanticReviewStatus | str) -> ResumeSemanticReviewStatus:
    return value if isinstance(value, ResumeSemanticReviewStatus) else ResumeSemanticReviewStatus(value)


def build_final_resume_fact_check(
    *, decisions: Mapping[str, ResumeSemanticReviewStatus | str],
    reasons: Mapping[str, str | None] | None, created_at: str,
    context: ValidatedResumeContext, jd_analysis: JDAnalysisArtifact,
    match_report: JDMatchReportArtifact, tailoring_proposal: TailoringProposalArtifact,
    tailoring_review: TailoringProposalReviewArtifact,
    tailored_resume: TailoredResumeArtifact,
    wording_proposal: TailoredWordingProposalArtifact | None = None,
    wording_review: TailoredWordingReviewArtifact | None = None,
    wording_tailored_resume: WordingTailoredResumeArtifact | None = None,
) -> FinalResumeFactCheckArtifact:
    checks, _, markdown, kind = prepare_final_resume_bullet_checks(
        context=context, jd_analysis=jd_analysis, match_report=match_report,
        tailoring_proposal=tailoring_proposal, tailoring_review=tailoring_review,
        tailored_resume=tailored_resume, wording_proposal=wording_proposal,
        wording_review=wording_review, wording_tailored_resume=wording_tailored_resume,
    )
    check_by_id = {item.bullet_check_id: item for item in checks}
    if set(decisions) != set(check_by_id):
        raise Phase2ValidationError("Final Resume decisions must cover every bullet check exactly once")
    reason_values = {} if reasons is None else dict(reasons)
    if not set(reason_values) <= set(check_by_id):
        raise Phase2ValidationError("Final Resume review reason references an unknown check")
    timestamp = _iso_datetime(created_at, "created_at")
    reviews: list[FinalResumeBulletReview] = []
    for check in checks:
        status = _normalize_status(decisions[check.bullet_check_id])
        draft = FinalResumeBulletReview(
            "final_resume_bullet_review_pending", check.bullet_check_id, status,
            ResumeFactCheckReviewerType.PROFILE_OWNER, timestamp,
            reason_values.get(check.bullet_check_id),
        )
        reviews.append(FinalResumeBulletReview.from_dict({
            **draft.to_dict(), "review_id": generate_final_resume_bullet_review_id(draft),
        }, "final_resume_bullet_review"))
    review_tuple = tuple(reviews)
    content_hash = "sha256:" + hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    draft = FinalResumeFactCheckArtifact(
        "final-resume-fact-check-v1_pending", context.base_resume.profile_fingerprint,
        kind, tailored_resume.tailored_resume_id,
        None if wording_tailored_resume is None else wording_tailored_resume.wording_tailored_resume_id,
        None if wording_proposal is None else wording_proposal.wording_proposal_id,
        None if wording_review is None else wording_review.wording_review_id,
        jd_analysis.jd_analysis_id, match_report.match_report_id, content_hash,
        timestamp, checks, review_tuple, _summary(checks, review_tuple),
    )
    return FinalResumeFactCheckArtifact.from_dict({
        **draft.to_dict(), "fact_check_id": generate_final_resume_fact_check_id(draft),
    })


def render_final_resume_fact_check_report(
    value: FinalResumeFactCheckArtifact, **context: Any,
) -> str:
    value.validate(**context)
    facts = context["context"].fact_index().fact_by_id
    review_by_check = {item.bullet_check_id: item for item in value.reviews}
    lines = [
        "# Final Resume Fact Check",
        "",
        "> Deterministic provenance and reference validation passed. Human semantic review is recorded separately below and is not inferred from valid IDs.",
        "",
        f"Final resume kind: {value.final_resume_kind.value}",
        f"Bullets checked: {value.summary.bullet_count}",
        f"Wording changes: {value.summary.changed_bullet_count}",
        f"Human review: {value.summary.passed_count} passed, {value.summary.issue_count} issue(s), {value.summary.needs_confirmation_count} awaiting confirmation",
        "",
    ]
    for index, check in enumerate(value.bullet_checks, 1):
        review = review_by_check[check.bullet_check_id]
        lines.extend((
            f"## {index}. `{check.bullet_id}`",
            "",
            f"- Final wording: {check.final_text}",
            f"- Original wording: {check.original_text}",
            f"- Wording changed: {'yes' if check.wording_changed else 'no'}",
            f"- Human semantic status: {review.status.value}",
            f"- Review reason: {review.reason or 'No issue recorded.'}",
            f"- Source record: `{check.source_record_id}`",
            "- Verified facts:",
        ))
        for fact_id in check.fact_ids:
            fact = facts[fact_id]
            source = fact.source_context
            source_label = "source context unavailable"
            if source is not None:
                parts = [source.organization_or_project_name, source.title_or_role, source.record_type]
                source_label = " / ".join(item for item in parts if item)
            lines.append(f"  - `{fact_id}` ({source_label}): {fact.text}")
        if review.status != ResumeSemanticReviewStatus.PASSED:
            guidance = (
                "Return to JD-targeted wording review."
                if check.wording_changed
                else "Return to Resume Wording Review or Evidence Enrichment; do not edit this check artifact."
            )
            lines.extend((f"- Required follow-up: {guidance}", ""))
        else:
            lines.append("")
    lines.extend((
        "This report excludes Contact and does not copy the full Profile. Valid references, accepted wording, or model self-assessment do not automatically establish semantic truth.",
        "",
    ))
    return "\n".join(lines)


def save_final_resume_fact_check(
    value: FinalResumeFactCheckArtifact, path: str | Path, **context: Any,
) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Final Resume Fact Check already exists: {target}")
    value.validate(**context)
    return save_phase2_json(value.to_dict(), target)


def load_final_resume_fact_check(
    path: str | Path, **context: Any,
) -> FinalResumeFactCheckArtifact:
    value = FinalResumeFactCheckArtifact.from_dict(load_phase2_json(path))
    value.validate(**context)
    return value
