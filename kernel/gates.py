from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any, Dict

from contract_registry import resolve_contract, validate_contract
from kernel.phase_resolver import PhaseInfo
from kernel.state import GateResult, ParityMetrics
from observability.logger import log_event


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> Dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    first = raw.find("{")
    last = raw.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None

    candidate = raw[first : last + 1]
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if isinstance(loaded, dict):
        return loaded
    return None


def _run_external_json_gate(
    command: list[str],
    pass_field: str,
    gate_name: str,
    root: Path,
) -> GateResult:
    timer = perf_counter()
    log_event(
        level="INFO",
        component="gate",
        operation=gate_name,
        status="START",
        command=command,
    )
    started = _utc_now()
    process = subprocess.run(command, cwd=root, capture_output=True, text=True)
    payload = _extract_json(process.stdout or "") or {}

    status_value = str(payload.get(pass_field, "")).upper()
    violations = int(payload.get("violation_count", 1))
    is_pass = process.returncode == 0 and status_value == "PASS" and violations == 0

    result = GateResult(
        gate=gate_name,
        status="PASS" if is_pass else "FAIL",
        reason="EXTERNAL_GATE_PASS" if is_pass else "EXTERNAL_GATE_FAIL",
        severity="SOFT" if is_pass else "HARD",
        details={
            "command": command,
            "exit_code": process.returncode,
            "status_field": pass_field,
            "status": status_value,
            "violation_count": violations,
            "stdout_excerpt": (process.stdout or "").strip()[:1200],
            "stderr_excerpt": (process.stderr or "").strip()[:1200],
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO" if is_pass else "ERROR",
        component="gate",
        operation=gate_name,
        status="SUCCESS" if is_pass else "FAIL",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
        error=None if is_pass else result.reason,
        exit_code=process.returncode,
        violation_count=violations,
    )
    return result


def run_sci_gate(root: Path) -> GateResult:
    timer = perf_counter()
    log_event(level="INFO", component="gate", operation="SCI", status="START")
    started = _utc_now()

    try:
        contract = resolve_contract("SCI_AUTHORITY")
        validate_contract("SCI_AUTHORITY", contract)
    except Exception as exc:
        result = GateResult(
            gate="SCI",
            status="FAIL",
            reason="SCI_CONTRACT_INVALID",
            severity="HARD",
            details={
                "contract": "SCI_AUTHORITY",
                "error": str(exc),
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="SCI",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
            details=result.details,
        )
        return result

    result = GateResult(
        gate="SCI",
        status="PASS",
        reason="SCI_CONTRACT_VALID",
        severity="SOFT",
        details={
            "contract": "SCI_AUTHORITY",
            "schema_version": str(contract.get("schema_version", "")),
            "authority": str(contract.get("authority", "")),
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO",
        component="gate",
        operation="SCI",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
    )
    return result


def run_emv_gate(root: Path, python_executable: str) -> GateResult:
    try:
        kernel_contract = resolve_contract("CI_KERNEL_RUNTIME")
        validate_contract("CI_KERNEL_RUNTIME", kernel_contract)
    except Exception as exc:
        result = GateResult(
            gate="EMV",
            status="FAIL",
            reason="EMV_CONTRACT_INVALID",
            severity="HARD",
            details={"contract": "CI_KERNEL_RUNTIME", "error": str(exc)},
            started_at=_utc_now(),
            finished_at=_utc_now(),
        )
        log_event(level="ERROR", component="gate", operation="EMV", status="FAIL", error=result.reason)
        return result

    return _run_external_json_gate(
        [python_executable, "-m", "ci.meta.ci_enforcement_meta_validator"],
        "emv_status",
        "EMV",
        root,
    )


def run_ci_gate(root: Path, python_executable: str) -> GateResult:
    try:
        governance_contract = resolve_contract("SCI_RULES")
        validate_contract("SCI_RULES", governance_contract)
    except Exception as exc:
        result = GateResult(
            gate="CI",
            status="FAIL",
            reason="CI_CONTRACT_INVALID",
            severity="HARD",
            details={"contract": "SCI_RULES", "error": str(exc)},
            started_at=_utc_now(),
            finished_at=_utc_now(),
        )
        log_event(level="ERROR", component="gate", operation="CI", status="FAIL", error=result.reason)
        return result

    return _run_external_json_gate(
        [python_executable, "-m", "ci.run_ci_kernel"],
        "ci_status",
        "CI",
        root,
    )


def run_auth_gate(phase_info: PhaseInfo) -> GateResult:
    timer = perf_counter()
    log_event(level="INFO", component="gate", operation="AUTH", status="START")
    started = _utc_now()

    try:
        contract = resolve_contract("ADAPTER_PHASE_OWNERSHIP")
        validate_contract("ADAPTER_PHASE_OWNERSHIP", contract)
    except Exception as exc:
        result = GateResult(
            gate="AUTH",
            status="FAIL",
            reason="AUTH_CONTRACT_INVALID",
            severity="HARD",
            details={"contract": "ADAPTER_PHASE_OWNERSHIP", "error": str(exc)},
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="AUTH",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    source_of_truth = contract.get("phase_source_of_truth")
    writable_map = contract.get("phase_writable_systems")
    expected = ""
    if isinstance(source_of_truth, dict):
        expected = str(source_of_truth.get(phase_info.phase, ""))

    expected_writable: list[str] = []
    if isinstance(writable_map, dict):
        raw = writable_map.get(phase_info.phase)
        if isinstance(raw, list):
            expected_writable = [str(item) for item in raw]

    if not expected or phase_info.sst_source != expected:
        result = GateResult(
            gate="AUTH",
            status="FAIL",
            reason="AUTHORITY_MODEL_MISMATCH",
            severity="HARD",
            details={
                "phase": phase_info.phase,
                "expected_sst": expected,
                "actual_sst": phase_info.sst_source,
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="AUTH",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    result = GateResult(
        gate="AUTH",
        status="PASS",
        reason="AUTHORITY_MODEL_VALID",
        severity="SOFT",
        details={
            "phase": phase_info.phase,
            "sst_source": phase_info.sst_source,
            "writable_systems": list(phase_info.writable_systems),
            "expected_writable_systems": expected_writable,
            "contract": "ADAPTER_PHASE_OWNERSHIP",
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO",
        component="gate",
        operation="AUTH",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
    )
    return result


def run_governance_gate(
    *,
    phase_info: PhaseInfo,
    execution_mode: str,
    governance_approved: bool,
) -> GateResult:
    timer = perf_counter()
    log_event(level="INFO", component="gate", operation="GOVERNANCE", status="START")
    started = _utc_now()
    mode = str(execution_mode).strip().upper()

    try:
        policy = resolve_contract("GOVERNANCE_POLICY")
        validate_contract("GOVERNANCE_POLICY", policy)
    except Exception as exc:
        result = GateResult(
            gate="GOVERNANCE",
            status="FAIL",
            reason="GOVERNANCE_CONTRACT_INVALID",
            severity="HARD",
            details={"contract": "GOVERNANCE_POLICY", "error": str(exc)},
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="GOVERNANCE",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    read_only_mode = str(policy.get("read_only_mode", "READ_ONLY")).strip().upper()
    mutation_allowed_phase = str(policy.get("mutation_allowed_phase", "PHASE_3")).strip().upper()
    approval_required_for_mutation = bool(policy.get("approval_required_for_mutation", True))

    if mode == read_only_mode:
        result = GateResult(
            gate="GOVERNANCE",
            status="PASS",
            reason="READ_ONLY_MODE_ENFORCED",
            severity="SOFT",
            details={
                "execution_mode": mode,
                "phase": phase_info.phase,
                "governance_approved": governance_approved,
                "contract": "GOVERNANCE_POLICY",
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="INFO",
            component="gate",
            operation="GOVERNANCE",
            status="SUCCESS",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
        )
        return result

    if phase_info.phase != mutation_allowed_phase:
        result = GateResult(
            gate="GOVERNANCE",
            status="FAIL",
            reason="MUTATION_NOT_ALLOWED_PRE_CUTOVER",
            severity="HARD",
            details={
                "execution_mode": mode,
                "phase": phase_info.phase,
                "mutation_allowed_phase": mutation_allowed_phase,
                "contract": "GOVERNANCE_POLICY",
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="GOVERNANCE",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    if approval_required_for_mutation and not governance_approved:
        result = GateResult(
            gate="GOVERNANCE",
            status="FAIL",
            reason="GOVERNANCE_APPROVAL_REQUIRED",
            severity="HARD",
            details={
                "execution_mode": mode,
                "phase": phase_info.phase,
                "governance_approved": governance_approved,
                "contract": "GOVERNANCE_POLICY",
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="GOVERNANCE",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    result = GateResult(
        gate="GOVERNANCE",
        status="PASS",
        reason="GOVERNANCE_APPROVED",
        severity="SOFT",
        details={
            "execution_mode": mode,
            "phase": phase_info.phase,
            "governance_approved": governance_approved,
            "contract": "GOVERNANCE_POLICY",
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO",
        component="gate",
        operation="GOVERNANCE",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
    )
    return result


def run_drift_gate(metrics: ParityMetrics, max_global_drift: float) -> GateResult:
    timer = perf_counter()
    log_event(level="INFO", component="gate", operation="DRIFT", status="START")
    started = _utc_now()
    drift_ok = metrics.global_drift_pct < max_global_drift
    if not drift_ok:
        result = GateResult(
            gate="DRIFT",
            status="FAIL",
            reason="GLOBAL_DRIFT_THRESHOLD_EXCEEDED",
            severity="SOFT",
            details={
                "global_drift_pct": metrics.global_drift_pct,
                "max_global_drift": max_global_drift,
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="WARN",
            component="gate",
            operation="DRIFT",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
            global_drift_pct=metrics.global_drift_pct,
        )
        return result

    result = GateResult(
        gate="DRIFT",
        status="PASS",
        reason="GLOBAL_DRIFT_WITHIN_THRESHOLD",
        severity="SOFT",
        details={
            "global_drift_pct": metrics.global_drift_pct,
            "max_global_drift": max_global_drift,
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO",
        component="gate",
        operation="DRIFT",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
        global_drift_pct=metrics.global_drift_pct,
    )
    return result


def run_parity_gate(
    *,
    metrics: ParityMetrics,
    max_global_drift: float,
    tracker_summary: Dict[str, Any],
    critical_events_180d: int,
    enforce_cutover_stability: bool,
) -> GateResult:
    timer = perf_counter()
    log_event(level="INFO", component="gate", operation="PARITY", status="START")
    started = _utc_now()

    if metrics.identity_conflicts_total > 0:
        result = GateResult(
            gate="PARITY",
            status="FAIL",
            reason="IDENTITY_CONFLICTS_PRESENT",
            severity="HARD",
            details={
                "identity_conflicts_total": metrics.identity_conflicts_total,
            },
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="ERROR",
            component="gate",
            operation="PARITY",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
            identity_conflicts_total=metrics.identity_conflicts_total,
        )
        return result

    drift_gate = run_drift_gate(metrics, max_global_drift)
    if drift_gate.status == "FAIL":
        result = GateResult(
            gate="PARITY",
            status="FAIL",
            reason=drift_gate.reason,
            severity="SOFT",
            details=drift_gate.details,
            started_at=started,
            finished_at=_utc_now(),
        )
        log_event(
            level="WARN",
            component="gate",
            operation="PARITY",
            status="FAIL",
            duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
            error=result.reason,
        )
        return result

    if enforce_cutover_stability:
        if int(critical_events_180d) > 0:
            result = GateResult(
                gate="PARITY",
                status="FAIL",
                reason="CRITICAL_INSTABILITY_EVENTS_PRESENT",
                severity="SOFT",
                details={"critical_events_180d": int(critical_events_180d)},
                started_at=started,
                finished_at=_utc_now(),
            )
            log_event(
                level="WARN",
                component="gate",
                operation="PARITY",
                status="FAIL",
                duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
                error=result.reason,
            )
            return result

        if not bool(tracker_summary.get("pass", False)):
            result = GateResult(
                gate="PARITY",
                status="FAIL",
                reason="PARITY_STABILITY_NOT_MET",
                severity="SOFT",
                details=tracker_summary,
                started_at=started,
                finished_at=_utc_now(),
            )
            log_event(
                level="WARN",
                component="gate",
                operation="PARITY",
                status="FAIL",
                duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
                error=result.reason,
            )
            return result

    result = GateResult(
        gate="PARITY",
        status="PASS",
        reason="PARITY_GATES_PASSED",
        severity="SOFT",
        details={
            "global_drift_pct": metrics.global_drift_pct,
            "identity_conflicts_total": metrics.identity_conflicts_total,
            "tracker": tracker_summary,
            "critical_events_180d": int(critical_events_180d),
        },
        started_at=started,
        finished_at=_utc_now(),
    )
    log_event(
        level="INFO",
        component="gate",
        operation="PARITY",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - timer) * 1000.0, 0)),
        global_drift_pct=metrics.global_drift_pct,
        identity_conflicts_total=metrics.identity_conflicts_total,
    )
    return result
