#!/usr/bin/env python3
"""Schema-version enforcement guard for CI using execution schema contracts."""

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
EXPECTED_RUNTIME_SCHEMA_VERSION = "2.1"


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
        "validator": "schema_version_guard",
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
        schema_contract = resolve_contract("EXECUTION_SCHEMA")
        validate_contract("EXECUTION_SCHEMA", schema_contract)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="SCHEMA_VERSION_VIOLATION",
                file_path="contract:EXECUTION_SCHEMA",
                rule_id="SCHEMA_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    schema_version = str(schema_contract.get("schema_version", ""))
    if schema_version != EXPECTED_RUNTIME_SCHEMA_VERSION:
        violations.append(
            _violation(
                violation_type="SCHEMA_VERSION_VIOLATION",
                file_path="contract:EXECUTION_SCHEMA",
                rule_id="SCHEMA_VERSION_NOT_2_1",
                severity="CRITICAL",
                details={"actual": schema_version or "unset"},
            )
        )

    result_fields = schema_contract.get("result_required_fields")
    if not isinstance(result_fields, list) or "schema_version" not in result_fields:
        violations.append(
            _violation(
                violation_type="SCHEMA_VERSION_VIOLATION",
                file_path="contract:EXECUTION_SCHEMA",
                rule_id="SCHEMA_VERSION_FIELD_NOT_REQUIRED",
                severity="CRITICAL",
            )
        )

    metadata_fields = schema_contract.get("metadata_required_fields")
    if not isinstance(metadata_fields, list) or "schema_version" not in metadata_fields:
        violations.append(
            _violation(
                violation_type="SCHEMA_VERSION_VIOLATION",
                file_path="contract:EXECUTION_SCHEMA",
                rule_id="METADATA_SCHEMA_VERSION_FIELD_NOT_REQUIRED",
                severity="HIGH",
            )
        )

    report = _report(violations)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(run())
