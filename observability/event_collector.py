"""Event collector that normalizes, validates, and safely stores observability events."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from observability.audit_logger import AuditLogger, AuditLoggerError
from observability.schema import normalize_event, validate_event
from observability.trace_context import add_event_link

LOGGER = logging.getLogger("observability.event_collector")


class EventCollector:
    """Collects audit events with fail-safe malformed-event drop behavior."""

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        self._audit_logger = audit_logger or AuditLogger()
        self._emitted = 0
        self._dropped = 0
        self._errors = 0

    def emit(self, event: Mapping[str, Any]) -> bool:
        try:
            normalized = normalize_event(event)
            valid, errors = validate_event(normalized)
            if not valid:
                self._dropped += 1
                LOGGER.warning("Dropped malformed observability event: %s", "; ".join(errors))
                return False

            self._audit_logger.append_event(normalized)
            add_event_link(str(normalized.get("request_id", "")), str(normalized.get("event_id", "")))
            self._emitted += 1
            return True
        except AuditLoggerError:
            self._errors += 1
            LOGGER.exception("Audit logger write failed; event skipped")
            return False
        except Exception:
            self._errors += 1
            LOGGER.exception("Unexpected event collector failure; event skipped")
            return False

    def emit_many(self, events: list[Mapping[str, Any]]) -> int:
        success = 0
        for event in events:
            if self.emit(event):
                success += 1
        return success

    def stats(self) -> dict[str, int]:
        return {
            "emitted": self._emitted,
            "dropped": self._dropped,
            "errors": self._errors,
        }
