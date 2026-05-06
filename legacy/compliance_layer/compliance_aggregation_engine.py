"""Compliance aggregation for unified gate orchestration output."""

from __future__ import annotations

import hashlib
import json
from typing import Any

AGGREGATION_SCHEMA_VERSION = "2.1"


def _stable_projection(report: dict[str, Any]) -> dict[str, Any]:
    layer_projection = {
        gate: {
            "layer": detail.get("layer"),
            "rule_id": detail.get("rule_id"),
            "status": detail.get("status"),
            "violation_count": int(detail.get("violation_count", 0)),
        }
        for gate, detail in sorted(report.get("layer_results", {}).items())
    }

    trace_projection = [
        {
            "step": int(item.get("step", 0)),
            "gate": item.get("gate"),
            "layer": item.get("layer"),
            "status": item.get("status"),
            "rule_id": item.get("rule_id"),
        }
        for item in report.get("execution_trace", [])
    ]

    return {
        "compliance_status": report.get("compliance_status"),
        "violation_count": int(report.get("violation_count", 0)),
        "failing_layer": report.get("failing_layer"),
        "failing_gate": report.get("failing_gate"),
        "layer_results": layer_projection,
        "execution_trace": trace_projection,
        "schema_version": report.get("schema_version"),
    }


def compute_deterministic_hash(report: dict[str, Any]) -> str:
    stable = _stable_projection(report)
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_compliance_report(
    *,
    layer_results: dict[str, dict[str, Any]],
    execution_trace: list[dict[str, Any]],
    started_at: str,
    completed_at: str,
    failed_gate: str | None,
) -> dict[str, Any]:
    compliance_status = "GATE_FAILED" if failed_gate else "COMPLIANT"

    failure_violations: list[dict[str, Any]] = []
    if failed_gate:
        failure_violations = list(layer_results.get(failed_gate, {}).get("violations", []))

    violation_count = sum(int(detail.get("violation_count", 0)) for detail in layer_results.values())
    if failed_gate and violation_count < len(failure_violations):
        violation_count = len(failure_violations)

    failing_layer = None
    if failed_gate:
        failing_layer = layer_results.get(failed_gate, {}).get("layer")

    report = {
        "schema_version": AGGREGATION_SCHEMA_VERSION,
        "timestamp": completed_at,
        "started_at": started_at,
        "compliance_status": compliance_status,
        "violation_count": violation_count,
        "layer_results": layer_results,
        "execution_trace": execution_trace,
        "failing_gate": failed_gate,
        "failing_layer": failing_layer,
        "violations": failure_violations,
        "partial_success_allowed": False,
    }
    report["deterministic_hash"] = compute_deterministic_hash(report)
    return report
