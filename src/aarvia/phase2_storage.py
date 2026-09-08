"""Minimal deterministic and atomic JSON I/O for Phase 2 artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

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


def save_phase2_json_transaction(
    documents: Iterable[tuple[str | Path, Any]],
) -> tuple[Path, ...]:
    """Replace several validated JSON artifacts with exception-safe rollback."""
    items = tuple((Path(path), value) for path, value in documents)
    targets = tuple(path for path, _ in items)
    if not items:
        raise Phase2ValidationError("Phase 2 JSON transaction requires documents")
    if len(set(targets)) != len(targets):
        raise Phase2ValidationError("Phase 2 JSON transaction contains duplicate targets")
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for target, value in items:
            payload = (
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                staged[target] = Path(handle.name)
            if target.exists():
                with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                    handle.write(target.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                    backups[target] = Path(handle.name)
        for target in targets:
            os.replace(staged[target], target)
            staged.pop(target, None)
            replaced.append(target)
    except BaseException:
        rollback_error: BaseException | None = None
        for target in reversed(replaced):
            try:
                backup = backups.pop(target, None)
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            except BaseException as error:
                rollback_error = rollback_error or error
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)
        if rollback_error is not None:
            raise RuntimeError("Phase 2 transaction failed and rollback was incomplete") from rollback_error
        raise
    for temporary in backups.values():
        temporary.unlink(missing_ok=True)
    return targets
