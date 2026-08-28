"""JSON persistence for career profiles."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from .profile import CareerProfile, ProfileValidationError


def save_profile(profile: CareerProfile, path: str | Path) -> Path:
    """Atomically save a validated career profile as UTF-8 JSON."""
    if not isinstance(profile, CareerProfile):
        raise TypeError("profile must be a CareerProfile")
    validated = CareerProfile.from_dict(profile.to_dict())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        json.dump(validated.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(target)
    return target


def load_profile(path: str | Path) -> CareerProfile:
    """Load and validate a career profile from a UTF-8 JSON file."""
    source = Path(path)
    try:
        raw: Any = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProfileValidationError(f"{source} does not contain valid JSON") from error
    return CareerProfile.from_dict(raw)
