"""Deterministic trace reconstruction and replay from observability audit events."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "2.1"

_REQUIRED_FIELDS = (
    "event_id",
    "timestamp",
    "event_type",
    "request_id",
    "actor_role",
    "endpoint",
    "decision",
    "reason_code",
    "schema_version",
    "latency_ms",
    "trace_hash",
)


def deterministic_trace_id(request_id: str) -> str:
    normalized = request_id.strip() or "missing-request-id"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def load_audit_events(audit_path: str | Path) -> list[dict[str, Any]]:
    path = Path(audit_path)
    if not path.exists() or not path.is_file():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _is_valid_event(event: Mapping[str, Any]) -> bool:
    if str(event.get("schema_version", "")).strip() != SCHEMA_VERSION:
        return False
    for field in _REQUIRED_FIELDS:
        if field not in event:
            return False
    return True


def _sort_key(event: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(event.get("timestamp", "")),
        str(event.get("event_id", "")),
        str(event.get("event_type", "")),
        str(event.get("reason_code", "")),
    )


def normalize_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not _is_valid_event(event):
            continue
        normalized.append(dict(event))
    normalized.sort(key=_sort_key)
    return normalized


def reconstruct_traces(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = normalize_events(events)

    grouped: dict[str, dict[str, Any]] = {}
    for event in ordered:
        request_id = str(event.get("request_id", "")).strip() or "missing-request-id"
        trace_id = deterministic_trace_id(request_id)

        trace = grouped.setdefault(
            trace_id,
            {
                "trace_id": trace_id,
                "request_id": request_id,
                "schema_version": SCHEMA_VERSION,
                "events": [],
            },
        )

        projected = {
            "event_id": str(event["event_id"]),
            "timestamp": str(event["timestamp"]),
            "event_type": str(event["event_type"]),
            "decision": str(event["decision"]),
            "reason_code": str(event["reason_code"]),
            "endpoint": str(event["endpoint"]),
            "actor_role": str(event["actor_role"]),
            "latency_ms": float(event["latency_ms"]),
            "trace_hash": str(event["trace_hash"]),
            "schema_version": SCHEMA_VERSION,
        }
        trace["events"].append(projected)

    traces = list(grouped.values())
    traces.sort(key=lambda trace: (str(trace["events"][0]["timestamp"]) if trace["events"] else "", str(trace["trace_id"])))

    for trace in traces:
        events_list = trace["events"]
        events_list.sort(key=lambda event: (str(event["timestamp"]), str(event["event_id"]), str(event["event_type"])))
        trace["event_count"] = len(events_list)

    return traces


def export_replay_sequences(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    traces = reconstruct_traces(events)
    replay: list[dict[str, Any]] = []

    for trace in traces:
        replay.append(
            {
                "trace_id": trace["trace_id"],
                "request_id": trace["request_id"],
                "schema_version": SCHEMA_VERSION,
                "event_count": trace["event_count"],
                "replay_sequence": list(trace["events"]),
            }
        )

    return replay


def export_replay_from_file(audit_path: str | Path) -> list[dict[str, Any]]:
    return export_replay_sequences(load_audit_events(audit_path))
