from __future__ import annotations

from pathlib import Path

from aarvia.base_resume import load_base_resume
from aarvia.base_resume_session import BaseResumeSession, load_base_resume_session
from aarvia.cli import main
from aarvia.resume_profile_supplement import (
    ContactFieldType,
    SupplementReviewDecision,
    build_resume_profile_supplement,
    create_contact_field,
    create_structural_selection,
    create_supplement_review,
    save_resume_profile_supplement,
)
from test_resume_profile_supplement_cli import _context as supplement_context


NOW = "2026-10-11T00:00:00+00:00"


def test_base_resume_session_schema_is_publicly_exported():
    import aarvia

    assert aarvia.BASE_RESUME_SESSION_SCHEMA == "aarvia.base_resume_session"
    assert aarvia.BASE_RESUME_SESSION_SCHEMA_VERSION == 1


def _fixture(tmp_path):
    value = supplement_context(tmp_path)
    profile = value["profile_object"]
    contact = create_contact_field(ContactFieldType.NAME, "Profile Owner")
    selection = create_structural_selection(
        profile, "education[0]", ["institution", "degree", "field_of_study"],
    )
    reviews = [
        create_supplement_review(contact, decision=SupplementReviewDecision.CONFIRMED, reviewed_at=NOW),
        create_supplement_review(selection, decision=SupplementReviewDecision.CONFIRMED, reviewed_at=NOW),
    ]
    artifact = build_resume_profile_supplement(
        contact_fields=[contact], structural_selections=[selection],
        inclusion_reviews=reviews, created_at=NOW, **value["supplement_context"],
    )
    value["supplement_object"] = artifact
    value["supplement"] = save_resume_profile_supplement(
        artifact, tmp_path / "supplement.json", **value["supplement_context"],
    )
    return value


def _chain_args(value):
    return [
        "--profile", str(value["profile"]), "--mapping", str(value["mapping"]),
        "--review-artifact", str(value["review"]),
        "--recommendation", str(value["recommendation"]),
        "--decision", str(value["decision"]), "--gap-analysis", str(value["gap"]),
        "--evidence-bank", str(value["bank"]),
        "--evidence-enrichment", str(value["enrichment"]),
        "--resume-material", str(value["material"]),
        "--resume-wording-review", str(value["wording"]),
        "--resume-profile-supplement", str(value["supplement"]),
    ]


def _context(value):
    return dict(value["supplement_context"], resume_profile_supplement=value["supplement_object"])


def test_build_cli_saves_and_typed_loads(tmp_path):
    value = _fixture(tmp_path); output = tmp_path / "base.json"
    assert main(["build-base-resume", *_chain_args(value), "--output", str(output)],
                input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    artifact = load_base_resume(output, **_context(value))
    assert artifact.role_neutral is True
    assert artifact.coverage.contact_count == 1


def test_quit_and_interrupt_write_nothing(tmp_path):
    value = _fixture(tmp_path)
    for name, input_fn in (
        ("quit", lambda _: "q"),
        ("eof", lambda _: (_ for _ in ()).throw(EOFError())),
        ("interrupt", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())),
    ):
        output = tmp_path / f"{name}.json"
        assert main(["build-base-resume", *_chain_args(value), "--output", str(output)],
                    input_fn=input_fn, output_fn=lambda _: None) == 130
        assert not output.exists()


def test_pause_is_explicit_and_resume_validates_output(tmp_path):
    value = _fixture(tmp_path); output = tmp_path / "base.json"
    args = ["build-base-resume", *_chain_args(value), "--output", str(output)]
    assert main(args, input_fn=lambda _: "p", output_fn=lambda _: None) == 0
    session = Path(str(output) + ".session.json")
    assert session.exists() and not output.exists()
    loaded = BaseResumeSession.from_dict(__import__("json").loads(session.read_text()))
    assert loaded.target_output == str(output.resolve())
    try:
        load_base_resume_session(
            session, target_output=tmp_path / "different.json",
            **_context(value),
        )
    except Exception as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("stale session output was accepted")
    args.extend(["--resume-session", str(session)])
    assert main(args, input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    assert output.exists()


def test_renderer_requires_full_chain_and_does_not_print_private_content(tmp_path):
    value = _fixture(tmp_path); base = tmp_path / "base.json"; markdown = tmp_path / "base.md"
    assert main(["build-base-resume", *_chain_args(value), "--output", str(base)],
                input_fn=lambda _: "s", output_fn=lambda _: None) == 0
    messages = []
    assert main(["render-base-resume", *_chain_args(value), "--base-resume", str(base), "--output", str(markdown)],
                output_fn=messages.append) == 0
    assert markdown.exists()
    assert "Profile Owner" not in "\n".join(messages)
    assert all("sha256" not in item for item in messages)


def test_help_has_full_chain_and_session(capsys):
    try:
        main(["build-base-resume", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    for flag in ("--profile", "--mapping", "--resume-wording-review", "--resume-profile-supplement", "--resume-session", "--supersede"):
        assert flag in output
