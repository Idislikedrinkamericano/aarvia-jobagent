"""Minimal deterministic and atomic JSON I/O for Phase 2 artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .role_catalog import Phase2ValidationError


def save_phase2_json(value: Any, path: str | Path) -> Path:
    """Atomically write a deterministic UTF-8 Phase 2 JSON document."""
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return target


def load_phase2_json(path: str | Path) -> Any:
    """Read JSON while mapping syntax failures to the Phase 2 validation error."""
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise Phase2ValidationError(f"{source} does not contain valid JSON") from error
