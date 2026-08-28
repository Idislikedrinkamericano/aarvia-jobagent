import pytest

from aarvia.cli import main
from aarvia.storage import load_profile

from test_interview import scripted_input


def test_discover_accepts_custom_profile_path(tmp_path) -> None:
    path = tmp_path / "custom.json"
    output = []

    exit_code = main(
        ["discover", "--profile", str(path)],
        input_fn=scripted_input([":quit"]),
        output_fn=output.append,
    )

    assert exit_code == 0
    assert load_profile(path).basic_profile.current_location is None
    assert output[-1] == f"Saved to: {path}"


def test_root_help_starts_without_interview(capsys) -> None:
    assert main([]) == 0
    assert "discover" in capsys.readouterr().out


def test_discover_help_lists_profile_option(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["discover", "--help"])

    assert exit_info.value.code == 0
    assert "--profile" in capsys.readouterr().out
