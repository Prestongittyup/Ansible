#!/usr/bin/env python3
"""Contract-driven import boundary validator."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping

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
        "validator": "import_guard",
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
        boundary_contract = resolve_contract("IMPORT_BOUNDARY_RULES")
        validate_contract("IMPORT_BOUNDARY_RULES", boundary_contract)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="IMPORT_GUARD_FAILURE",
                file_path="contract:IMPORT_BOUNDARY_RULES",
                rule_id="IMPORT_BOUNDARY_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    edges = boundary_contract.get("allowed_execution_contract_edges")
    if not isinstance(edges, Mapping) or not edges:
        violations.append(
            _violation(
                violation_type="IMPORT_BOUNDARY_VIOLATION",
                file_path="contract:IMPORT_BOUNDARY_RULES",
                rule_id="EXECUTION_CONTRACT_EDGE_MAP_MISSING",
                severity="CRITICAL",
            )
        )
    else:
        for source_module, targets in edges.items():
            if not str(source_module).strip():
                violations.append(
                    _violation(
                        violation_type="IMPORT_BOUNDARY_VIOLATION",
                        file_path="contract:IMPORT_BOUNDARY_RULES",
                        rule_id="EDGE_SOURCE_EMPTY",
                        severity="HIGH",
                    )
                )
            if not isinstance(targets, list):
                violations.append(
                    _violation(
                        violation_type="IMPORT_BOUNDARY_VIOLATION",
                        file_path="contract:IMPORT_BOUNDARY_RULES",
                        rule_id="EDGE_TARGETS_INVALID",
                        severity="HIGH",
                        details={"source_module": str(source_module)},
                    )
                )

    runtime_guard_import_allowed = boundary_contract.get("runtime_guard_import_allowed")
    if not isinstance(runtime_guard_import_allowed, list) or not runtime_guard_import_allowed:
        violations.append(
            _violation(
                violation_type="IMPORT_BOUNDARY_VIOLATION",
                file_path="contract:IMPORT_BOUNDARY_RULES",
                rule_id="RUNTIME_GUARD_IMPORT_POLICY_MISSING",
                severity="CRITICAL",
            )
        )

    runner_entry_allowed = boundary_contract.get("runner_entry_allowed")
    if not isinstance(runner_entry_allowed, list) or not runner_entry_allowed:
        violations.append(
            _violation(
                violation_type="IMPORT_BOUNDARY_VIOLATION",
                file_path="contract:IMPORT_BOUNDARY_RULES",
                rule_id="RUNNER_ENTRY_POLICY_MISSING",
                severity="CRITICAL",
            )
        )

    report = _report(violations)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(run())
