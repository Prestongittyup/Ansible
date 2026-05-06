"""Derived OpenTelemetry mapping from observability audit events."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from legacy.export.trace_exporter import SCHEMA_VERSION, deterministic_trace_id, load_audit_events, normalize_events


_SYSTEM_EVENT_TYPES = frozenset({"EMV_RUN", "CI_KERNEL_RUN"})


def _span_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def map_events_to_otel(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = normalize_events(events)

    spans: list[dict[str, Any]] = []
    api_span_by_request: dict[str, dict[str, Any]] = {}

    for event in ordered:
        event_type = str(event.get("event_type", ""))
        request_id = str(event.get("request_id", "")).strip() or "missing-request-id"
        trace_id = deterministic_trace_id(request_id)
        event_id = str(event.get("event_id", ""))
        timestamp = str(event.get("timestamp", ""))

        if event_type in _SYSTEM_EVENT_TYPES:
            spans.append(
                {
                    "trace_id": trace_id,
                    "span_id": _span_id(f"system:{event_id}"),
                    "parent_span_id": None,
                    "name": event_type,
                    "kind": "INTERNAL",
                    "start_time": timestamp,
                    "end_time": timestamp,
                    "attributes": {
                        "endpoint": str(event.get("endpoint", "")),
                        "decision": str(event.get("decision", "")),
                        "reason_code": str(event.get("reason_code", "")),
                        "schema_version": SCHEMA_VERSION,
                    },
                    "events": [],
                }
            )
            continue

        if event_type == "API_REQUEST":
            span = {
                "trace_id": trace_id,
                "span_id": _span_id(f"api:{event_id}"),
                "parent_span_id": None,
                "name": f"api:{event.get('endpoint', '')}",
                "kind": "SERVER",
                "start_time": timestamp,
                "end_time": timestamp,
                "attributes": {
                    "endpoint": str(event.get("endpoint", "")),
                    "decision": str(event.get("decision", "")),
                    "reason_code": str(event.get("reason_code", "")),
                    "actor_role": str(event.get("actor_role", "")),
                    "latency_ms": float(event.get("latency_ms", 0.0)),
                    "schema_version": SCHEMA_VERSION,
                },
                "events": [],
            }
            spans.append(span)
            api_span_by_request[request_id] = span
            continue

        if event_type == "QUERY_EXECUTION":
            parent = api_span_by_request.get(request_id)
            spans.append(
                {
                    "trace_id": trace_id,
                    "span_id": _span_id(f"query:{event_id}"),
                    "parent_span_id": None if parent is None else parent["span_id"],
                    "name": f"query:{event.get('endpoint', '')}",
                    "kind": "INTERNAL",
                    "start_time": timestamp,
                    "end_time": timestamp,
                    "attributes": {
                        "decision": str(event.get("decision", "")),
                        "reason_code": str(event.get("reason_code", "")),
                        "latency_ms": float(event.get("latency_ms", 0.0)),
                        "schema_version": SCHEMA_VERSION,
                    },
                    "events": [],
                }
            )
            continue

        if event_type in {"AUTH_DECISION", "AUTHZ_DECISION"}:
            parent = api_span_by_request.get(request_id)
            if parent is not None:
                parent["events"].append(
                    {
                        "name": event_type,
                        "timestamp": timestamp,
                        "attributes": {
                            "decision": str(event.get("decision", "")),
                            "reason_code": str(event.get("reason_code", "")),
                            "schema_version": SCHEMA_VERSION,
                        },
                    }
                )
            continue

        if event_type == "GOVERNANCE_DECISION":
            parent = api_span_by_request.get(request_id)
            if parent is not None:
                parent["attributes"]["governance.decision"] = str(event.get("decision", ""))
                parent["attributes"]["governance.reason_code"] = str(event.get("reason_code", ""))
            continue

    spans.sort(key=lambda span: (str(span.get("start_time", "")), str(span.get("span_id", ""))))
    for span in spans:
        span["events"].sort(key=lambda item: (str(item.get("timestamp", "")), str(item.get("name", ""))))

    return {
        "schema_version": SCHEMA_VERSION,
        "resource": {
            "service.name": "ansible-read-only-surface",
            "service.namespace": "export",
        },
        "spans": spans,
    }


def map_audit_file_to_otel(audit_path: str) -> dict[str, Any]:
    return map_events_to_otel(load_audit_events(audit_path))
