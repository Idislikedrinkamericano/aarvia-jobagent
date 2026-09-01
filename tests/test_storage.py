import json

import pytest

from aarvia import CareerProfile, ProfileValidationError, create_profile, load_profile, save_profile

from test_profile import complete_profile_data


def test_profile_can_be_saved_as_json(tmp_path) -> None:
    profile = create_profile(complete_profile_data())
    path = save_profile(profile, tmp_path / "profiles" / "career-profile.json")

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["basic_profile"]["name"] == "Lin"
    assert saved["open_questions"] == []


def test_json_can_be_loaded_and_round_trip_is_equal(tmp_path) -> None:
    original = create_profile(complete_profile_data())
    path = save_profile(original, tmp_path / "career-profile.json")

    loaded = load_profile(path)

    assert isinstance(loaded, CareerProfile)
    assert loaded == original
    assert loaded.to_dict() == original.to_dict()
    assert loaded.education[0].gpa == "3.7 / 4.0"


def test_old_json_without_gpa_remains_compatible(tmp_path) -> None:
    data = complete_profile_data()
    data["education"][0].pop("gpa")
    path = tmp_path / "old-profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_profile(path)

    assert loaded.education[0].gpa is None


def test_null_experience_summary_round_trips(tmp_path) -> None:
    data = complete_profile_data()
    data["experience_overview"][0]["short_factual_summary"] = None
    original = create_profile(data)

    loaded = load_profile(save_profile(original, tmp_path / "profile.json"))

    assert loaded == original
    assert loaded.experience_overview[0].short_factual_summary is None


def test_load_rejects_unknown_json_fields(tmp_path) -> None:
    path = tmp_path / "career-profile.json"
    path.write_text('{"future_prediction": "unsupported"}', encoding="utf-8")

    with pytest.raises(ProfileValidationError, match="unknown fields: future_prediction"):
        load_profile(path)


def test_load_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "career-profile.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ProfileValidationError, match="valid JSON"):
        load_profile(path)
