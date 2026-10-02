"""Opt-in, metadata-only diagnostics for Provider compatibility failures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ProviderDiagnosticsWriter:
    """Write redacted attempt metadata without persisting Provider content."""

    def __init__(self, directory: str | Path, *, api_key: str) -> None:
        self.directory = Path(directory)
        self.api_key = api_key

    def write_attempt(
        self,
        *,
        attempt: int,
        provider_path: str,
        model: str,
        response_format_mode: str,
        parser_error: str | None,
        raw_response: str | None,
        fallback_reason: str | None,
        candidate_rejection_count: int = 0,
        candidate_rejection_reason_codes: list[str] | tuple[str, ...] = (),
        rejected_evidence_group_count: int = 0,
        unresolved_candidate_count: int = 0,
        unresolved_evidence_group_count: int = 0,
        retry_selection_reason: str | None = None,
        selected_attempt: int | None = None,
    ) -> Path:
        if (retry_selection_reason is None) != (selected_attempt is None):
            raise ValueError("retry selection reason and selected attempt must be provided together")
        if selected_attempt is not None and selected_attempt not in {1, 2}:
            raise ValueError("selected attempt must be 1 or 2")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        payload: dict[str, Any] = {
            "warning": "Metadata only. Provider output may derive from sensitive Profile data; raw response is not saved.",
            "attempt": attempt,
            "provider_path": provider_path,
            "model": model,
            "response_format_mode": response_format_mode,
            "parser_error": self._redact(parser_error),
            "response_sha256": (
                None
                if raw_response is None
                else hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
            ),
            "response_length": 0 if raw_response is None else len(raw_response),
            "fallback_reason": self._redact(fallback_reason),
            "raw_response_saved": False,
        }
        if candidate_rejection_count:
            payload["candidate_rejection_count"] = candidate_rejection_count
            payload["candidate_rejection_reason_codes"] = sorted(
                set(candidate_rejection_reason_codes)
            )
        payload["rejected_evidence_group_count"] = rejected_evidence_group_count
        payload["unresolved_candidate_count"] = unresolved_candidate_count
        payload["unresolved_evidence_group_count"] = unresolved_evidence_group_count
        if retry_selection_reason is not None:
            payload["retry_selection_reason"] = retry_selection_reason
            payload["selected_attempt"] = selected_attempt
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        path = self._available_path(attempt)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return path

    def _redact(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace(self.api_key, "[REDACTED]") if self.api_key else value

    def _available_path(self, attempt: int) -> Path:
        stem = f"role-mapping-attempt-{attempt:02d}"
        candidate = self.directory / f"{stem}.json"
        suffix = 1
        while candidate.exists():
            candidate = self.directory / f"{stem}-{suffix}.json"
            suffix += 1
        return candidate
