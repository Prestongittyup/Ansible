from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from kernel.state import RunState
from observability.context import (
    get_execution_timeline,
    get_external_failures,
    get_failure_point,
    get_run_context,
)

AUDIT_FILE_NAME = "system_enforcement_audit.json"

REQUIRED_TOP_LEVEL_KEYS = (
    "run_id",
    "mode",
    "phase",
    "sst_owner",
    "adapters_active",
    "parity_result",
    "identity_conflicts",
    "drift_percentage",
    "execution_status",
    "fail_closed_triggered",
    "kernel_anchor",
    "run_metadata",
    "phase_source",
    "phase_target",
    "sst_source",
    "drift_metrics",
    "deterministic_signature",
    "adapter_states",
    "identity_conflict_count",
    "gate_results",
    "final_decision",
    "exit_code",
    "exit_code_reason",
    "trace_id",
    "execution_timeline",
    "failure_point",
    "external_failures",
    "total_duration_ms",
)


def _parse_utc_timestamp(value: str) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None

    normalized = token.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _compute_total_duration_ms(*, started_at: str, finished_at: str, timeline: list[dict[str, Any]]) -> int:
    started = _parse_utc_timestamp(started_at)
    finished = _parse_utc_timestamp(finished_at)
    if started is not None and finished is not None:
        return max(0, int(round((finished - started).total_seconds() * 1000.0, 0)))

    timeline_total = 0
    for entry in timeline:
        duration = entry.get("duration_ms")
        if isinstance(duration, (int, float)):
            timeline_total += int(round(float(duration), 0))
    return max(0, timeline_total)


def _resolve_output_path(audit_path: str | Path) -> Path:
    path = Path(audit_path)
    if path.suffix.lower() == ".json":
        output = path
    else:
        output = path / AUDIT_FILE_NAME
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def build_audit_payload(state: RunState) -> Dict[str, Any]:
    state.ensure_finished()

    run_context = get_run_context()

    trace_id = state.data_stats.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id.strip():
        trace_id = str(run_context.get("trace_id", "")).strip()

    execution_timeline = get_execution_timeline()
    failure_point = state.data_stats.get("failure_point")
    if not isinstance(failure_point, str) or not failure_point.strip():
        failure_point = get_failure_point()

    external_failures = state.data_stats.get("external_failures")
    if not isinstance(external_failures, list):
        external_failures = get_external_failures()

    total_duration_ms = _compute_total_duration_ms(
        started_at=state.started_at,
        finished_at=state.finished_at,
        timeline=execution_timeline,
    )

    parity_result = state.data_stats.get("parity_result")
    if not isinstance(parity_result, dict):
        parity_result = {
            "status": "PASS" if state.exit_code == 0 else "FAIL",
            "reason": state.decision_reason,
            "deterministic_hash": state.parity_metrics.deterministic_hash,
        }

    execution_status = state.data_stats.get("execution_status")
    if not isinstance(execution_status, str) or not execution_status.strip():
        execution_status = "PASS" if state.exit_code == 0 else "FAIL"

    drift_percentage = state.data_stats.get("drift_percentage")
    if not isinstance(drift_percentage, (int, float)):
        drift_percentage = state.parity_metrics.global_drift_pct

    identity_conflicts = state.data_stats.get("identity_conflicts")
    if not isinstance(identity_conflicts, list):
        identity_conflicts = list(state.parity_metrics.identity_conflicts)

    fail_closed_triggered = state.data_stats.get("fail_closed_triggered")
    if not isinstance(fail_closed_triggered, bool):
        fail_closed_triggered = bool(state.exit_code != 0)

    adapters_active = state.data_stats.get("adapters_active")
    if not isinstance(adapters_active, list):
        adapter_states = state.data_stats.get("adapter_states", {})
        if isinstance(adapter_states, dict):
            adapters_active = list(adapter_states.keys())
        else:
            adapters_active = []

    payload: Dict[str, Any] = {
        "run_id": state.run_id,
        "mode": state.data_stats.get("kernel_mode", ""),
        "phase": state.source_phase,
        "sst_owner": state.sst_source,
        "adapters_active": adapters_active,
        "parity_result": parity_result,
        "identity_conflicts": identity_conflicts,
        "drift_percentage": float(drift_percentage),
        "execution_status": execution_status,
        "fail_closed_triggered": fail_closed_triggered,
        "kernel_anchor": state.kernel_anchor,
        "run_metadata": {
            "run_id": state.run_id,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "fail_closed": state.fail_closed,
            "execution_mode": state.data_stats.get("execution_mode", ""),
            "kernel_mode": state.data_stats.get("kernel_mode", ""),
            "trace_id": trace_id,
            "total_duration_ms": total_duration_ms,
        },
        "phase_source": state.source_phase,
        "phase_target": state.target_phase,
        "sst_source": state.sst_source,
        "sst_target": state.sst_target,
        "drift_metrics": {
            "compared_records_total": state.parity_metrics.compared_records_total,
            "drifted_records_total": state.parity_metrics.drifted_records_total,
            "global_drift_pct": state.parity_metrics.global_drift_pct,
            "hard_drift_count": state.parity_metrics.hard_drift_count,
            "soft_drift_count": state.parity_metrics.soft_drift_count,
            "deterministic_hash": state.parity_metrics.deterministic_hash,
        },
        "deterministic_signature": state.data_stats.get(
            "deterministic_signature",
            state.parity_metrics.deterministic_hash,
        ),
        "adapter_states": state.data_stats.get("adapter_states", {}),
        "identity_conflict_count": state.parity_metrics.identity_conflicts_total,
        "gate_results": [gate.to_dict() for gate in state.gate_results],
        "tracker_summary": state.tracker_summary,
        "data_stats": state.data_stats,
        "final_decision": state.decision,
        "decision_reason": state.decision_reason,
        "exit_code": state.exit_code,
        "exit_code_reason": state.exit_code_reason,
        "errors": state.errors,
        "trace_id": trace_id,
        "execution_timeline": execution_timeline,
        "failure_point": failure_point,
        "external_failures": external_failures,
        "total_duration_ms": total_duration_ms,
    }
    return payload


def validate_audit_payload(payload: Dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in payload]
    if missing:
        raise ValueError(f"Audit payload missing required keys: {', '.join(missing)}")

    if not isinstance(payload.get("run_metadata"), dict):
        raise ValueError("run_metadata must be an object")
    if not isinstance(payload.get("gate_results"), list):
        raise ValueError("gate_results must be a list")
    if not isinstance(payload.get("exit_code"), int):
        raise ValueError("exit_code must be an integer")


def write_audit(state: RunState, audit_path: str | Path) -> Path:
    payload = build_audit_payload(state)
    validate_audit_payload(payload)

    output_path = _resolve_output_path(audit_path)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path
