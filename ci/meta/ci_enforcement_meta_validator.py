#!/usr/bin/env python3
"""CI Enforcement Meta-Validator (EMV).

EMV validates governance contracts before CI kernel policy execution.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract_registry import resolve_contract, validate_contract

EMV_SCHEMA_VERSION = "1.0"
EXPECTED_RUNTIME_SCHEMA_VERSION = "2.1"

ALLOWED_SEVERITY = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ALLOWED_ENFORCEMENT_TYPES = {"CI_VALIDATION", "RUNTIME_GUARD", "IMPORT_GUARD", "SCHEMA_GUARD"}

EXPECTED_RULE_TYPES = {
    "SCI-001": "CI_VALIDATION",
    "SCI-002": "IMPORT_GUARD",
    "SCI-003": "RUNTIME_GUARD",
    "SCI-004": "SCHEMA_GUARD",
    "SCI-005": "IMPORT_GUARD",
    "SCI-006": "CI_VALIDATION",
}


def _violation(kind: str, rule_id: str, severity: str, location: str, details: str) -> Dict[str, str]:
    return {
        "type": kind,
        "rule_id": rule_id,
        "severity": severity,
        "file": location,
        "details": details,
    }


def _load_governance_contract(violations: List[Dict[str, str]]) -> Mapping[str, Any] | None:
    try:
        payload = resolve_contract("SCI_RULES")
        validate_contract("SCI_RULES", payload)
        return payload
    except Exception as exc:
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details=f"Unable to resolve SCI_RULES contract: {exc}",
            )
        )
        return None


def _validate_kernel_contract(violations: List[Dict[str, str]]) -> None:
    try:
        payload = resolve_contract("CI_KERNEL_RUNTIME")
        validate_contract("CI_KERNEL_RUNTIME", payload)
    except Exception as exc:
        violations.append(
            _violation(
                kind="BINDING_MISMATCH",
                rule_id="",
                severity="CRITICAL",
                location="contract:CI_KERNEL_RUNTIME",
                details=f"Unable to resolve CI kernel runtime contract: {exc}",
            )
        )
        return

    if str(payload.get("execution_mode", "")).upper() != "CI_ENFORCEMENT_KERNEL":
        violations.append(
            _violation(
                kind="BINDING_MISMATCH",
                rule_id="",
                severity="CRITICAL",
                location="contract:CI_KERNEL_RUNTIME",
                details="execution_mode must be CI_ENFORCEMENT_KERNEL.",
            )
        )

    if not bool(payload.get("meta_validation_required", False)):
        violations.append(
            _violation(
                kind="BINDING_MISMATCH",
                rule_id="",
                severity="CRITICAL",
                location="contract:CI_KERNEL_RUNTIME",
                details="meta_validation_required must be true.",
            )
        )

    if not bool(payload.get("fail_closed", False)):
        violations.append(
            _violation(
                kind="BINDING_MISMATCH",
                rule_id="",
                severity="CRITICAL",
                location="contract:CI_KERNEL_RUNTIME",
                details="fail_closed must be true.",
            )
        )


def _validate_runtime_schema_contract(violations: List[Dict[str, str]], rules_by_id: Mapping[str, Mapping[str, Any]]) -> None:
    try:
        payload = resolve_contract("EXECUTION_SCHEMA")
        validate_contract("EXECUTION_SCHEMA", payload)
    except Exception as exc:
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="SCI-004",
                severity="CRITICAL",
                location="contract:EXECUTION_SCHEMA",
                details=f"Unable to resolve execution schema contract: {exc}",
            )
        )
        return

    runtime_schema_version = str(payload.get("schema_version", ""))
    if runtime_schema_version != EXPECTED_RUNTIME_SCHEMA_VERSION:
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="SCI-004",
                severity="CRITICAL",
                location="contract:EXECUTION_SCHEMA",
                details=(
                    "Runtime schema drift detected: "
                    f"expected {EXPECTED_RUNTIME_SCHEMA_VERSION}, found {runtime_schema_version or 'unset'}."
                ),
            )
        )

    schema_rule = rules_by_id.get("SCI-004")
    if not isinstance(schema_rule, Mapping):
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="SCI-004",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="SCI-004 rule is missing from SCI_RULES.",
            )
        )
    else:
        descriptor = f"{schema_rule.get('name', '')} {schema_rule.get('description', '')}"
        if EXPECTED_RUNTIME_SCHEMA_VERSION not in descriptor:
            violations.append(
                _violation(
                    kind="SCHEMA_DRIFT",
                    rule_id="SCI-004",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="SCI-004 rule does not declare runtime schema version 2.1.",
                )
            )


def _validate_rule_structure(payload: Mapping[str, Any], violations: List[Dict[str, str]]) -> Dict[str, Mapping[str, Any]]:
    schema_version = str(payload.get("schema_version", "")).strip()
    registry_version = str(payload.get("registry_version", "")).strip()

    if schema_version != EMV_SCHEMA_VERSION:
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details=f"schema_version must be {EMV_SCHEMA_VERSION}, found {schema_version or 'unset'}.",
            )
        )

    if registry_version != schema_version:
        violations.append(
            _violation(
                kind="SCHEMA_DRIFT",
                rule_id="",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="registry_version and schema_version must match.",
            )
        )

    rules = payload.get("rules")
    if not isinstance(rules, list):
        violations.append(
            _violation(
                kind="ORPHAN_RULE",
                rule_id="",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="rules must be a list.",
            )
        )
        return {}

    validator_bindings = payload.get("validator_bindings")
    if not isinstance(validator_bindings, Mapping):
        violations.append(
            _violation(
                kind="BINDING_MISMATCH",
                rule_id="",
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="validator_bindings must be a mapping.",
            )
        )
        validator_bindings = {}

    rule_ids_seen: Set[str] = set()
    execution_orders: Set[int] = set()
    rules_by_id: Dict[str, Mapping[str, Any]] = {}
    used_validator_ids: Set[str] = set()

    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            violations.append(
                _violation(
                    kind="ORPHAN_RULE",
                    rule_id="",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details=f"Rule at index {index} is not an object.",
                )
            )
            continue

        rule_id = str(rule.get("rule_id", "")).strip()
        if not rule_id:
            violations.append(
                _violation(
                    kind="ORPHAN_RULE",
                    rule_id="",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details=f"Rule at index {index} is missing rule_id.",
                )
            )
            continue

        if rule_id in rule_ids_seen:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="Duplicate rule_id detected.",
                )
            )
        rule_ids_seen.add(rule_id)
        rules_by_id[rule_id] = rule

        severity = str(rule.get("severity", ""))
        if severity not in ALLOWED_SEVERITY:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details=f"Invalid severity value: {severity}",
                )
            )

        enforcement = rule.get("enforcement")
        if not isinstance(enforcement, Mapping):
            violations.append(
                _violation(
                    kind="ORPHAN_RULE",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="enforcement object is missing.",
                )
            )
        else:
            enforcement_type = str(enforcement.get("type", ""))
            if enforcement_type not in ALLOWED_ENFORCEMENT_TYPES:
                violations.append(
                    _violation(
                        kind="BINDING_MISMATCH",
                        rule_id=rule_id,
                        severity="CRITICAL",
                        location="contract:SCI_RULES",
                        details=f"Invalid enforcement type: {enforcement_type or 'unset'}",
                    )
                )

            expected_type = EXPECTED_RULE_TYPES.get(rule_id)
            if expected_type and enforcement_type != expected_type:
                violations.append(
                    _violation(
                        kind="BINDING_MISMATCH",
                        rule_id=rule_id,
                        severity="CRITICAL",
                        location="contract:SCI_RULES",
                        details=f"Execution type mismatch. Expected {expected_type}, found {enforcement_type}.",
                    )
                )

        binding = rule.get("validator_binding")
        if not isinstance(binding, Mapping):
            violations.append(
                _violation(
                    kind="ORPHAN_RULE",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="validator_binding is missing.",
                )
            )
        else:
            validator_id = str(binding.get("validator_id", "")).strip()
            if not validator_id:
                violations.append(
                    _violation(
                        kind="ORPHAN_RULE",
                        rule_id=rule_id,
                        severity="CRITICAL",
                        location="contract:SCI_RULES",
                        details="validator_binding.validator_id is required.",
                    )
                )
            else:
                used_validator_ids.add(validator_id)
                if validator_id not in validator_bindings:
                    violations.append(
                        _violation(
                            kind="BINDING_MISMATCH",
                            rule_id=rule_id,
                            severity="CRITICAL",
                            location="contract:SCI_RULES",
                            details=f"Unknown validator_id: {validator_id}",
                        )
                    )

        if str(rule.get("fail_behavior", "")) != "FAIL_CLOSED":
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="fail_behavior must be FAIL_CLOSED.",
                )
            )

        dependencies = rule.get("dependencies")
        if not isinstance(dependencies, list) or "SCI" not in dependencies or "EXECUTION_PRECEDENCE_CONTRACT" not in dependencies:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="dependencies must include SCI and EXECUTION_PRECEDENCE_CONTRACT.",
                )
            )

        if str(rule.get("version", "")) != schema_version:
            violations.append(
                _violation(
                    kind="SCHEMA_DRIFT",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="Rule version must match schema_version.",
                )
            )

        try:
            execution_order = int(rule.get("execution_order"))
        except Exception:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id=rule_id,
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="execution_order must be an integer.",
                )
            )
        else:
            if execution_order in execution_orders:
                violations.append(
                    _violation(
                        kind="BINDING_MISMATCH",
                        rule_id=rule_id,
                        severity="CRITICAL",
                        location="contract:SCI_RULES",
                        details=f"Duplicate execution_order detected: {execution_order}",
                    )
                )
            execution_orders.add(execution_order)

    expected_rule_ids = set(EXPECTED_RULE_TYPES.keys())
    missing_expected = sorted(expected_rule_ids - rule_ids_seen)
    unexpected_rules = sorted(rule_ids_seen - expected_rule_ids)

    for rule_id in missing_expected:
        violations.append(
            _violation(
                kind="ORPHAN_RULE",
                rule_id=rule_id,
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="Expected rule is missing from governance contract.",
            )
        )

    for rule_id in unexpected_rules:
        violations.append(
            _violation(
                kind="ORPHAN_RULE",
                rule_id=rule_id,
                severity="CRITICAL",
                location="contract:SCI_RULES",
                details="Unexpected rule detected outside approved SCI set.",
            )
        )

    if execution_orders:
        expected_order = set(range(1, len(execution_orders) + 1))
        if execution_orders != expected_order:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id="",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details="execution_order must be contiguous and deterministic starting at 1.",
                )
            )

    for validator_id, spec in validator_bindings.items():
        if not isinstance(spec, Mapping):
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id="",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details=f"Validator binding {validator_id} must be an object.",
                )
            )
            continue

        module_name = str(spec.get("module", "")).strip()
        callable_name = str(spec.get("callable", "")).strip()
        if not module_name or not callable_name:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id="",
                    severity="CRITICAL",
                    location="contract:SCI_RULES",
                    details=f"Validator binding {validator_id} requires module and callable.",
                )
            )
            continue

        try:
            module = importlib.import_module(module_name)
            bound = getattr(module, callable_name, None)
            if not callable(bound):
                violations.append(
                    _violation(
                        kind="BINDING_MISMATCH",
                        rule_id="",
                        severity="CRITICAL",
                        location=f"contract:SCI_RULES:{validator_id}",
                        details="Bound validator function is not callable.",
                    )
                )
        except Exception as exc:
            violations.append(
                _violation(
                    kind="BINDING_MISMATCH",
                    rule_id="",
                    severity="CRITICAL",
                    location=f"contract:SCI_RULES:{validator_id}",
                    details=f"Validator import check failed: {exc}",
                )
            )

    orphan_validators = sorted(set(validator_bindings.keys()) - used_validator_ids)
    for validator_id in orphan_validators:
        violations.append(
            _violation(
                kind="ORPHAN_VALIDATOR",
                rule_id="",
                severity="CRITICAL",
                location=f"contract:SCI_RULES:{validator_id}",
                details="Validator binding is defined but not referenced by any rule.",
            )
        )

    return rules_by_id


def run_ci_enforcement_meta_validation(registry_path: str | Path | None = None) -> Dict[str, Any]:
    del registry_path  # Legacy parameter retained for CLI compatibility.

    violations: List[Dict[str, str]] = []

    payload = _load_governance_contract(violations)
    if payload is None:
        return {
            "emv_status": "FAIL",
            "violation_count": len(violations),
            "violations": violations,
            "schema_version": EMV_SCHEMA_VERSION,
            "execution_blocking": True,
        }

    rules_by_id = _validate_rule_structure(payload, violations)
    _validate_runtime_schema_contract(violations, rules_by_id)
    _validate_kernel_contract(violations)

    ordered_violations = sorted(
        violations,
        key=lambda item: (
            item.get("type", ""),
            item.get("rule_id", ""),
            item.get("file", ""),
            item.get("details", ""),
        ),
    )

    emv_status = "FAIL" if ordered_violations else "PASS"
    return {
        "emv_status": emv_status,
        "violation_count": len(ordered_violations),
        "violations": ordered_violations,
        "schema_version": str(payload.get("schema_version", EMV_SCHEMA_VERSION)) or EMV_SCHEMA_VERSION,
        "execution_blocking": emv_status == "FAIL",
    }


def run() -> int:
    parser = argparse.ArgumentParser(description="Run CI Enforcement Meta-Validator")
    parser.add_argument("--registry-path", dest="registry_path", default=None, help="Reserved for compatibility")
    args = parser.parse_args()

    report = run_ci_enforcement_meta_validation(registry_path=args.registry_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("execution_blocking") else 0


if __name__ == "__main__":
    raise SystemExit(run())
