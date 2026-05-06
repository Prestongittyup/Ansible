"""Immutable schema and validation helpers for observability audit events."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

OBSERVABILITY_SCHEMA_VERSION = "2.1"
SCHEMA_AUTHORITY_DOMAIN = "observability_audit_events"

EVENT_TYPES = frozenset(
    {
        "AUTH_DECISION",
        "AUTHZ_DECISION",
        "GOVERNANCE_DECISION",
        "QUERY_EXECUTION",
        "INGESTION_EVENT",
        "API_REQUEST",
        "EMV_RUN",
        "CI_KERNEL_RUN",
    }
)

DECISION_VALUES = frozenset({"PASS", "FAIL", "BLOCKED"})

REQUIRED_FIELDS = (
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    text = str(value).strip()
    if not text:
        return utc_now()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return utc_now()


def _normalize_latency_ms(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0.0 else 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except ValueError:
        return 0.0
    return parsed if parsed >= 0.0 else 0.0


def build_trace_hash(event: Mapping[str, Any]) -> str:
    material = {
        "request_id": str(event.get("request_id", "")),
        "event_type": str(event.get("event_type", "")),
        "endpoint": str(event.get("endpoint", "")),
        "actor_role": str(event.get("actor_role", "")),
        "decision": str(event.get("decision", "")),
        "reason_code": str(event.get("reason_code", "")),
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = dict(event)

    event_id = str(normalized.get("event_id", "")).strip()
    normalized["event_id"] = event_id or str(uuid.uuid4())

    normalized["timestamp"] = _iso_timestamp(normalized.get("timestamp", utc_now()))
    normalized["event_type"] = str(normalized.get("event_type", "")).strip().upper()
    normalized["request_id"] = str(normalized.get("request_id", "")).strip() or "missing-request-id"
    normalized["actor_role"] = str(normalized.get("actor_role", "unknown")).strip() or "unknown"
    normalized["endpoint"] = str(normalized.get("endpoint", "unknown")).strip() or "unknown"

    decision = str(normalized.get("decision", "PASS")).strip().upper()
    normalized["decision"] = decision if decision in DECISION_VALUES else "FAIL"

    normalized["reason_code"] = str(normalized.get("reason_code", "UNSPECIFIED")).strip() or "UNSPECIFIED"
    normalized["schema_version"] = (
        str(normalized.get("schema_version", OBSERVABILITY_SCHEMA_VERSION)).strip()
        or OBSERVABILITY_SCHEMA_VERSION
    )
    normalized["latency_ms"] = _normalize_latency_ms(normalized.get("latency_ms", 0.0))
    normalized["trace_hash"] = str(normalized.get("trace_hash", "")).strip() or build_trace_hash(normalized)

    return normalized


def validate_event(event: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in event:
            errors.append(f"missing field: {field}")

    event_type = str(event.get("event_type", "")).strip().upper()
    if event_type not in EVENT_TYPES:
        errors.append(f"invalid event_type: {event_type}")

    decision = str(event.get("decision", "")).strip().upper()
    if decision not in DECISION_VALUES:
        errors.append(f"invalid decision: {decision}")

    schema_version = str(event.get("schema_version", "")).strip()
    if schema_version != OBSERVABILITY_SCHEMA_VERSION:
        errors.append(f"schema_version must be {OBSERVABILITY_SCHEMA_VERSION}")

    for field in ("event_id", "timestamp", "request_id", "actor_role", "endpoint", "reason_code", "trace_hash"):
        value = str(event.get(field, "")).strip()
        if not value:
            errors.append(f"field must be non-empty: {field}")

    latency = event.get("latency_ms", 0.0)
    if isinstance(latency, bool):
        errors.append("latency_ms must be numeric")
    elif not isinstance(latency, (int, float)):
        errors.append("latency_ms must be numeric")
    elif float(latency) < 0.0:
        errors.append("latency_ms must be >= 0")

    return (len(errors) == 0), errors
