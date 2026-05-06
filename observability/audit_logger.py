"""Append-only JSONL audit logger for observability events."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping


class AuditLoggerError(RuntimeError):
    """Raised when append-only audit writes fail."""


class AuditLogger:
    """Append-only JSONL audit sink."""

    def __init__(self, file_path: str | Path | None = None) -> None:
        path = Path(file_path) if file_path is not None else Path("logs/observability/audit_events.jsonl")
        self._path = path
        self._lock = threading.Lock()

    @property
    def file_path(self) -> Path:
        return self._path

    def append_event(self, event: Mapping[str, Any]) -> None:
        line = json.dumps(dict(event), sort_keys=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.write("\n")
        except OSError as exc:
            raise AuditLoggerError(f"append-only audit write failed: {exc}") from exc

    def read_events(self) -> list[dict[str, Any]]:
        if not self._path.exists() or not self._path.is_file():
            return []

        events: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                events.append(payload)
        return events
