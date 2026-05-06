"""SIEM formatting exporters derived from observability audit logs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Mapping

from legacy.export.trace_exporter import SCHEMA_VERSION, deterministic_trace_id, load_audit_events, normalize_events


def _base_record(event: Mapping[str, Any]) -> dict[str, Any]:
    request_id = str(event.get("request_id", "")).strip() or "missing-request-id"
    return {
        "event_id": str(event.get("event_id", "")),
        "timestamp": str(event.get("timestamp", "")),
        "trace_id": deterministic_trace_id(request_id),
        "event_type": str(event.get("event_type", "")),
        "decision": str(event.get("decision", "")),
        "reason_code": str(event.get("reason_code", "")),
        "schema_version": SCHEMA_VERSION,
    }


def _splunk_record(base: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "time": str(base["timestamp"]),
        "source": "ansible.export",
        "sourcetype": "json",
        "event": dict(base),
    }


def _elastic_record(base: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "@timestamp": str(base["timestamp"]),
        "event": {
            "id": str(base["event_id"]),
            "kind": "event",
            "category": "process",
            "type": str(base["event_type"]),
            "outcome": str(base["decision"]),
        },
        "trace": {"id": str(base["trace_id"])},
        "labels": {
            "reason_code": str(base["reason_code"]),
            "schema_version": SCHEMA_VERSION,
        },
        "raw": dict(base),
    }


def _datadog_record(base: Mapping[str, Any]) -> dict[str, Any]:
    text = str(base["timestamp"])
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    epoch_ms = int(datetime.fromisoformat(text).timestamp() * 1000)
    return {
        "timestamp": epoch_ms,
        "ddsource": "ansible.export",
        "ddtags": f"event_type:{base['event_type']},decision:{base['decision']},schema_version:{SCHEMA_VERSION}",
        "trace_id": str(base["trace_id"]),
        "message": json.dumps(dict(base), sort_keys=True),
        "event": dict(base),
    }


def format_for_siem(events: Iterable[Mapping[str, Any]], target: str) -> list[dict[str, Any]]:
    ordered = normalize_events(events)
    target_name = target.strip().lower()

    outputs: list[dict[str, Any]] = []
    for event in ordered:
        base = _base_record(event)
        if target_name == "splunk":
            outputs.append(_splunk_record(base))
        elif target_name in {"elastic", "elasticsearch"}:
            outputs.append(_elastic_record(base))
        elif target_name == "datadog":
            outputs.append(_datadog_record(base))
        else:
            raise ValueError(f"unsupported SIEM target: {target}")

    return outputs


def export_siem_from_file(audit_path: str, target: str) -> list[dict[str, Any]]:
    events = load_audit_events(audit_path)
    if not events:
        return []
    try:
        return format_for_siem(events, target=target)
    except ValueError:
        return []
