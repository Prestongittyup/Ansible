#!/usr/bin/env python3
"""Execution contract integrity validator."""

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
        "validator": "execution_contract_integrity_guard",
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
        execution_authority = resolve_contract("EXECUTION_AUTHORITY")
        validate_contract("EXECUTION_AUTHORITY", execution_authority)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:EXECUTION_AUTHORITY",
                rule_id="EXECUTION_AUTHORITY_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    try:
        legacy_bridge = resolve_contract("LEGACY_BRIDGE")
        validate_contract("LEGACY_BRIDGE", legacy_bridge)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:LEGACY_BRIDGE",
                rule_id="LEGACY_BRIDGE_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    required_kernel_commands = execution_authority.get("required_kernel_commands")
    if not isinstance(required_kernel_commands, list) or "bootstrap" not in required_kernel_commands or "run" not in required_kernel_commands:
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:EXECUTION_AUTHORITY",
                rule_id="CANONICAL_KERNEL_COMMANDS_MISSING",
                severity="CRITICAL",
                details={"required_commands": ["bootstrap", "run"]},
            )
        )

    if bool(execution_authority.get("legacy_kernel_entrypoint_enabled", True)):
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:EXECUTION_AUTHORITY",
                rule_id="LEGACY_KERNEL_ENTRYPOINT_PRESENT",
                severity="CRITICAL",
            )
        )

    if not bool(legacy_bridge.get("locked", False)) or not bool(legacy_bridge.get("immutable", False)):
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:LEGACY_BRIDGE",
                rule_id="LEGACY_BRIDGE_NOT_IMMUTABLE",
                severity="CRITICAL",
            )
        )

    if not str(legacy_bridge.get("lock_marker", "")).strip():
        violations.append(
            _violation(
                violation_type="EXECUTION_CONTRACT_VIOLATION",
                file_path="contract:LEGACY_BRIDGE",
                rule_id="LEGACY_LOCK_MARKER_MISSING",
                severity="CRITICAL",
            )
        )

    report = _report(violations)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(run())
