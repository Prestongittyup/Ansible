#!/usr/bin/env python3
"""Single CI enforcement kernel entrypoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
CI_SCHEMA_VERSION = "1.0"


def _fail_closed(reason: str) -> int:
    payload = {
        "ci_status": "FAIL",
        "schema_version": CI_SCHEMA_VERSION,
        "rule_results": [
            {
                "rule_id": "CI-KERNEL-BOOT",
                "name": "CI Kernel Bootstrap Guard",
                "status": "FAIL",
                "violation_count": 1,
                "details": {
                    "reason": reason,
                },
            }
        ],
        "violation_count": 1,
        "execution_mode": "CI_ENFORCEMENT_KERNEL",
        "sprint_blocking": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1


def _resolve_kernel_contracts() -> Dict[str, Any]:
    from contract_registry import resolve_contract, validate_contract

    sci_rules = resolve_contract("SCI_RULES")
    validate_contract("SCI_RULES", sci_rules)

    kernel_runtime = resolve_contract("CI_KERNEL_RUNTIME")
    validate_contract("CI_KERNEL_RUNTIME", kernel_runtime)

    execution_mode = str(kernel_runtime.get("execution_mode", "")).strip().upper()
    if execution_mode != "CI_ENFORCEMENT_KERNEL":
        raise RuntimeError("CI_KERNEL_RUNTIME execution_mode must be CI_ENFORCEMENT_KERNEL")
    if not bool(kernel_runtime.get("meta_validation_required", False)):
        raise RuntimeError("CI_KERNEL_RUNTIME must require meta validation")
    if not bool(kernel_runtime.get("fail_closed", False)):
        raise RuntimeError("CI_KERNEL_RUNTIME must enforce fail-closed")

    return {
        "sci_rules": sci_rules,
        "kernel_runtime": kernel_runtime,
    }


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        contracts = _resolve_kernel_contracts()
    except Exception as exc:
        return _fail_closed(f"Unable to resolve CI contracts: {exc}")

    try:
        from ci.meta.ci_enforcement_meta_validator import run_ci_enforcement_meta_validation
    except Exception as exc:  # pragma: no cover
        return _fail_closed(f"Unable to load CI enforcement meta-validator: {exc}")

    try:
        emv_report = run_ci_enforcement_meta_validation()
    except Exception as exc:  # pragma: no cover
        return _fail_closed(f"CI enforcement meta-validation execution failure: {exc}")

    if not isinstance(emv_report, dict):
        return _fail_closed("CI enforcement meta-validator returned malformed output")

    emv_status = str(emv_report.get("emv_status", "")).upper()
    if emv_status != "PASS":
        print(json.dumps(emv_report, indent=2, sort_keys=True))
        return 1

    try:
        from ci.core.ci_policy_engine import CIPolicyEngine, CIPolicyEngineError
    except Exception as exc:  # pragma: no cover
        return _fail_closed(f"Unable to load CI policy engine: {exc}")

    try:
        decision = CIPolicyEngine().run()
    except CIPolicyEngineError as exc:
        return _fail_closed(f"CI policy engine fail-closed: {exc}")
    except Exception as exc:  # pragma: no cover
        return _fail_closed(f"Unhandled CI kernel execution failure: {exc}")

    decision["emv_status"] = "PASS"
    decision["emv_violation_count"] = 0
    decision["schema_version"] = str(contracts["kernel_runtime"].get("schema_version", CI_SCHEMA_VERSION))

    print(json.dumps(decision, indent=2, sort_keys=True))

    if str(decision.get("ci_status", "")).upper() != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
