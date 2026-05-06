"""Derived metrics exporter from immutable observability audit logs."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from legacy.export.trace_exporter import SCHEMA_VERSION, load_audit_events, normalize_events


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * float(len(ordered) - 1)
    idx = int(round(rank))
    if idx < 0:
        idx = 0
    if idx >= len(ordered):
        idx = len(ordered) - 1
    return ordered[idx]


def compute_metrics(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = normalize_events(events)

    api_events = [event for event in ordered if str(event.get("event_type", "")) == "API_REQUEST"]
    auth_events = [event for event in ordered if str(event.get("event_type", "")) == "AUTH_DECISION"]
    governance_events = [event for event in ordered if str(event.get("event_type", "")) == "GOVERNANCE_DECISION"]
    query_events = [event for event in ordered if str(event.get("event_type", "")) == "QUERY_EXECUTION"]

    api_failures = [event for event in api_events if str(event.get("decision", "")) != "PASS"]
    auth_failures = [event for event in auth_events if str(event.get("decision", "")) != "PASS"]
    governance_denials = [event for event in governance_events if str(event.get("decision", "")) in {"FAIL", "BLOCKED"}]

    query_latencies = [float(event.get("latency_ms", 0.0)) for event in query_events]

    event_counts: dict[str, int] = {}
    for event in ordered:
        event_type = str(event.get("event_type", ""))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "request_volume": len(api_events),
        "event_counts": dict(sorted(event_counts.items())),
        "rates": {
            "api_error_rate": _rate(len(api_failures), len(api_events)),
            "auth_failure_rate": _rate(len(auth_failures), len(auth_events)),
            "governance_denial_rate": _rate(len(governance_denials), len(governance_events)),
        },
        "query_latency_ms": {
            "count": len(query_latencies),
            "p50": _percentile(query_latencies, 50),
            "p90": _percentile(query_latencies, 90),
            "p99": _percentile(query_latencies, 99),
        },
    }


def export_metrics_from_file(audit_path: str) -> dict[str, Any]:
    return compute_metrics(load_audit_events(audit_path))
