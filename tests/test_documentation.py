from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_conversation_index_links_to_each_phase_log() -> None:
    index = (DOCS / "conversation-log.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\((conversation-log-phase-[012]\.md)\)", index)

    assert links == [
        "conversation-log-phase-0.md",
        "conversation-log-phase-1.md",
        "conversation-log-phase-2.md",
    ]
    assert all((DOCS / link).is_file() for link in links)
    assert "sanitized phase summaries" in index
    assert "not verbatim personal transcripts" in index
    for link in links:
        summary = (DOCS / link).read_text(encoding="utf-8")
        assert "Public sanitized summary" in summary
        assert "not a verbatim personal transcript" in summary
    assert "docs/conversation-log.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/conversation-log.md" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")


def test_phase_zero_and_one_history_boundaries_are_preserved() -> None:
    phase_zero = (DOCS / "conversation-log-phase-0.md").read_text(encoding="utf-8")
    phase_one = (DOCS / "conversation-log-phase-1.md").read_text(encoding="utf-8")

    assert len(re.findall(r"^## .*Conversation", phase_zero, flags=re.MULTILINE)) == 4
    assert len(re.findall(r"^## .*Conversation", phase_one, flags=re.MULTILINE)) == 22
    assert "Conversation 4" in phase_zero
    assert "Conversation 5" not in phase_zero
    assert "Conversation 5" in phase_one
    assert "Conversation 26" in phase_one


def test_phase_two_missing_verbatim_history_is_labeled_as_summary() -> None:
    phase_two = (DOCS / "conversation-log-phase-2.md").read_text(encoding="utf-8")

    assert "Where verbatim history was unavailable" in phase_two
    assert "only product and engineering decisions are summarized" in phase_two
    assert "Phase 2A Prompt" in phase_two


def test_private_local_data_directory_is_git_ignored_by_policy() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "local_data/" in gitignore
