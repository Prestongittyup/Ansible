#!/usr/bin/env python3
"""CI workflow ordering and isolation guard via governance contracts."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract_registry import resolve_contract, validate_contract

CI_SCHEMA_VERSION = "1.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _violation(
    *,
    violation_type: str,
    file_path: str,
    rule_id: str,
    severity: str,
    details: Dict[str, object] | None = None,
) -> Dict[str, object]:
    return {
        "violation_type": violation_type,
        "file_path": file_path,
        "rule_id": rule_id,
        "rule_broken": rule_id,
        "severity": severity,
        "timestamp": _utc_now(),
        "details": details or {},
    }


def _report(violations: List[Dict[str, object]]) -> Dict[str, object]:
    first = violations[0] if violations else None
    return {
        "validator": "ci_pipeline_order_guard",
        "status": "FAIL" if violations else "PASS",
        "schema_version": CI_SCHEMA_VERSION,
        "violations": violations,
        "file_path": first["file_path"] if first else "",
        "rule_id": first["rule_id"] if first else "",
        "severity": first["severity"] if first else "LOW",
        "timestamp": _utc_now(),
    }


def run() -> int:
    violations: List[Dict[str, object]] = []

    try:
        pipeline_contract = resolve_contract("CI_PIPELINE_ORDER")
        validate_contract("CI_PIPELINE_ORDER", pipeline_contract)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_PIPELINE_ORDER",
                rule_id="CI_PIPELINE_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    try:
        kernel_contract = resolve_contract("CI_KERNEL_RUNTIME")
        validate_contract("CI_KERNEL_RUNTIME", kernel_contract)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_KERNEL_RUNTIME",
                rule_id="CI_KERNEL_RUNTIME_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    required_entrypoint = str(pipeline_contract.get("required_entrypoint", "")).strip().upper()
    if required_entrypoint != "CI_KERNEL":
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_PIPELINE_ORDER",
                rule_id="CI_KERNEL_ENTRYPOINT_MISSING",
                severity="CRITICAL",
                details={"required_entrypoint": "CI_KERNEL", "actual": required_entrypoint or "unset"},
            )
        )

    required_execution_mode = str(pipeline_contract.get("required_execution_mode", "")).strip().upper()
    actual_execution_mode = str(kernel_contract.get("execution_mode", "")).strip().upper()
    if required_execution_mode != actual_execution_mode:
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_PIPELINE_ORDER",
                rule_id="CI_KERNEL_EXECUTION_MODE_MISMATCH",
                severity="CRITICAL",
                details={
                    "required_execution_mode": required_execution_mode or "unset",
                    "actual_execution_mode": actual_execution_mode or "unset",
                },
            )
        )

    if bool(pipeline_contract.get("require_emv_before_policy_engine", False)) and not bool(
        kernel_contract.get("meta_validation_required", False)
    ):
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_PIPELINE_ORDER",
                rule_id="EMV_ORDERING_NOT_ENFORCED",
                severity="CRITICAL",
            )
        )

    if bool(pipeline_contract.get("forbid_direct_validator_execution", False)) and not bool(
        kernel_contract.get("fail_closed", False)
    ):
        violations.append(
            _violation(
                violation_type="CI_PIPELINE_VIOLATION",
                file_path="contract:CI_KERNEL_RUNTIME",
                rule_id="DIRECT_VALIDATOR_EXECUTION_NOT_BLOCKED",
                severity="CRITICAL",
            )
        )

    report = _report(violations)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(run())
