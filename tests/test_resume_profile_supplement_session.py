from __future__ import annotations

from dataclasses import replace

import pytest

from aarvia.resume_profile_supplement import (
    ContactFieldType,
    SupplementReviewDecision,
    create_contact_field,
    create_supplement_review,
)
from aarvia.resume_profile_supplement_session import (
    ResumeProfileSupplementSession,
    SupplementSessionSection,
    build_resume_profile_supplement_session,
    load_resume_profile_supplement_session,
    save_resume_profile_supplement_session,
)
from aarvia.role_catalog import Phase2ValidationError
from test_resume_profile_supplement import _fixture


NOW = "2026-10-10T00:00:00+00:00"
LATER = "2026-10-10T01:00:00+00:00"


def _session(tmp_path):
    source, material, wording, context = _fixture(tmp_path)
    contact = create_contact_field(ContactFieldType.EMAIL, "owner@example.test")
    review = create_supplement_review(
        contact, decision=SupplementReviewDecision.CONFIRMED, reviewed_at=NOW
    )
    target = tmp_path / "supplement.json"
    session = build_resume_profile_supplement_session(
        target_output=target,
        current_section=SupplementSessionSection.SKILLS,
        contact_fields=[contact],
        structural_selections=[],
        inclusion_reviews=[review],
        updated_at=NOW,
        **context,
    )
    validation = dict(
        target_output=target,
        **context,
    )
    return source, material, wording, target, session, validation


def test_session_round_trip_and_resume_revision_are_deterministic(tmp_path):
    _, _, _, target, session, validation = _session(tmp_path)
    assert ResumeProfileSupplementSession.from_dict(session.to_dict()) == session
    path = tmp_path / "session.json"
    save_resume_profile_supplement_session(session, path, **validation)
    assert load_resume_profile_supplement_session(path, **validation) == session

    resumed = build_resume_profile_supplement_session(
        target_output=target,
        current_section=SupplementSessionSection.FINAL_REVIEW,
        contact_fields=session.contact_fields,
        structural_selections=session.structural_selections,
        inclusion_reviews=session.inclusion_reviews,
        updated_at=LATER,
        previous_session=session,
        **{key: value for key, value in validation.items() if key != "target_output"},
    )
    assert resumed.created_at == session.created_at
    assert resumed.updated_at == LATER
    assert resumed.session_id != session.session_id


def test_session_rejects_target_and_upstream_staleness(tmp_path):
    source, material, wording, _, session, validation = _session(tmp_path)
    with pytest.raises(Phase2ValidationError, match="different output"):
        session.validate(**{**validation, "target_output": tmp_path / "other.json"})
    with pytest.raises(Phase2ValidationError, match="provenance is stale"):
        session.validate(
            **{**validation, "resume_material": replace(material, resume_material_id="resume_material_stale")}
        )
    with pytest.raises(Phase2ValidationError, match="provenance is stale"):
        session.validate(
            **{**validation, "resume_wording_review": replace(wording, wording_review_id="resume_wording_stale")}
        )
    stale_profile = replace(
        source["profile_object"],
        basic_profile=replace(
            source["profile_object"].basic_profile, current_location="Changed"
        ),
    )
    with pytest.raises(Phase2ValidationError, match="provenance is stale"):
        session.validate(**{**validation, "profile": stale_profile})


@pytest.mark.parametrize("mutation", ["unknown", "session_id", "contact"])
def test_session_typed_load_rejects_tampering(tmp_path, mutation):
    _, _, _, _, session, _ = _session(tmp_path)
    data = session.to_dict()
    if mutation == "unknown":
        data["unexpected"] = True
    elif mutation == "session_id":
        data["session_id"] = "resume-profile-supplement-session-v1_forged"
    else:
        data["contact_fields"][0]["value"] = "changed@example.test"
    with pytest.raises(Phase2ValidationError):
        ResumeProfileSupplementSession.from_dict(data)


def test_session_atomic_replacement_failure_preserves_existing_file(tmp_path, monkeypatch):
    import aarvia.resume_profile_supplement_session as module

    _, _, _, _, session, validation = _session(tmp_path)
    path = tmp_path / "session.json"
    path.write_text("existing", encoding="utf-8")

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(module, "save_phase2_json", fail_write)
    with pytest.raises(OSError, match="simulated"):
        save_resume_profile_supplement_session(session, path, **validation)
    assert path.read_text(encoding="utf-8") == "existing"
