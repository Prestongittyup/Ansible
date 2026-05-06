#!/usr/bin/env python3
"""Policy-driven CI enforcement kernel engine.

This engine executes SCI governance rules from logical contract bindings,
without path-based registry or filesystem module resolution.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract_registry import resolve_contract, validate_contract

CI_SCHEMA_VERSION = "1.0"

ALLOWED_SEVERITY = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ALLOWED_ENFORCEMENT_TYPES = {"CI_VALIDATION", "RUNTIME_GUARD", "IMPORT_GUARD", "SCHEMA_GUARD"}


class CIPolicyEngineError(RuntimeError):
    """Raised when the CI policy engine cannot enforce contract-driven execution."""


@dataclass(frozen=True)
class RuleBinding:
    """Execution-ready representation of a governance rule."""

    rule_id: str
    name: str
    description: str
    severity: str
    enforcement_type: str
    validator_id: str
    validator_module: str
    validator_callable: str
    fail_behavior: str
    dependencies: List[str]
    version: str
    execution_order: int


class CIPolicyEngine:
    """Executes validators strictly from SCI governance contracts."""

    def run(self) -> Dict[str, Any]:
        rules = self._load_contract_rules()

        rule_results: List[Dict[str, Any]] = []
        violation_count = 0

        for rule in rules:
            result = self._execute_rule(rule)
            rule_results.append(result)
            violation_count += int(result.get("violation_count", 0))

            if result.get("status") == "FAIL":
                return {
                    "ci_status": "FAIL",
                    "schema_version": CI_SCHEMA_VERSION,
                    "rule_results": rule_results,
                    "violation_count": violation_count,
                    "execution_mode": "CI_ENFORCEMENT_KERNEL",
                    "sprint_blocking": True,
                }

        return {
            "ci_status": "PASS",
            "schema_version": CI_SCHEMA_VERSION,
            "rule_results": rule_results,
            "violation_count": violation_count,
            "execution_mode": "CI_ENFORCEMENT_KERNEL",
            "sprint_blocking": False,
        }

    def _load_contract_rules(self) -> List[RuleBinding]:
        governance_contract = resolve_contract("SCI_RULES")
        validate_contract("SCI_RULES", governance_contract)

        schema_version = str(governance_contract.get("schema_version", "")).strip()
        if schema_version != CI_SCHEMA_VERSION:
            raise CIPolicyEngineError(
                f"SCI_RULES schema_version must be {CI_SCHEMA_VERSION}, got {schema_version or 'unset'}"
            )

        rules = governance_contract.get("rules")
        if not isinstance(rules, list) or not rules:
            raise CIPolicyEngineError("SCI_RULES has no executable rules")

        validator_bindings = governance_contract.get("validator_bindings")
        if not isinstance(validator_bindings, Mapping) or not validator_bindings:
            raise CIPolicyEngineError("SCI_RULES validator_bindings must be a non-empty mapping")

        parsed_rules = [
            self._to_rule_binding(rule_payload=rule, validator_bindings=validator_bindings, schema_version=schema_version)
            for rule in rules
        ]
        parsed_rules.sort(key=lambda item: item.execution_order)
        return parsed_rules

    def _to_rule_binding(
        self,
        *,
        rule_payload: Mapping[str, Any],
        validator_bindings: Mapping[str, Any],
        schema_version: str,
    ) -> RuleBinding:
        required_keys = {
            "rule_id",
            "name",
            "description",
            "severity",
            "enforcement",
            "validator_binding",
            "fail_behavior",
            "dependencies",
            "version",
            "execution_order",
        }
        missing = sorted(required_keys - set(rule_payload.keys()))
        if missing:
            raise CIPolicyEngineError(f"Rule missing required fields: {missing}")

        rule_id = str(rule_payload.get("rule_id", "")).strip()
        severity = str(rule_payload.get("severity", "")).strip()
        if severity not in ALLOWED_SEVERITY:
            raise CIPolicyEngineError(f"Invalid severity for rule {rule_id}: {severity}")

        enforcement = rule_payload.get("enforcement")
        if not isinstance(enforcement, Mapping):
            raise CIPolicyEngineError(f"Invalid enforcement object for rule {rule_id}")
        enforcement_type = str(enforcement.get("type", "")).strip()
        if enforcement_type not in ALLOWED_ENFORCEMENT_TYPES:
            raise CIPolicyEngineError(f"Invalid enforcement type for rule {rule_id}: {enforcement_type}")

        binding = rule_payload.get("validator_binding")
        if not isinstance(binding, Mapping):
            raise CIPolicyEngineError(f"Invalid validator binding for rule {rule_id}")

        validator_id = str(binding.get("validator_id", "")).strip()
        if not validator_id:
            raise CIPolicyEngineError(f"Rule {rule_id} missing validator_id")

        validator_spec = validator_bindings.get(validator_id)
        if not isinstance(validator_spec, Mapping):
            raise CIPolicyEngineError(f"Rule {rule_id} references unknown validator_id: {validator_id}")

        validator_module = str(validator_spec.get("module", "")).strip()
        validator_callable = str(validator_spec.get("callable", "")).strip()
        if not validator_module or not validator_callable:
            raise CIPolicyEngineError(
                f"Validator {validator_id} requires module and callable fields"
            )

        fail_behavior = str(rule_payload.get("fail_behavior", "")).strip()
        if fail_behavior != "FAIL_CLOSED":
            raise CIPolicyEngineError(
                f"Rule {rule_id} fail behavior must be FAIL_CLOSED, got {fail_behavior}"
            )

        dependencies = rule_payload.get("dependencies")
        if not isinstance(dependencies, list):
            raise CIPolicyEngineError(f"Rule {rule_id} dependencies must be a list")
        if "SCI" not in dependencies or "EXECUTION_PRECEDENCE_CONTRACT" not in dependencies:
            raise CIPolicyEngineError(
                f"Rule {rule_id} must declare SCI and EXECUTION_PRECEDENCE_CONTRACT dependencies"
            )

        version = str(rule_payload.get("version", "")).strip()
        if version != schema_version:
            raise CIPolicyEngineError(
                f"Rule {rule_id} version {version or 'unset'} must match schema_version {schema_version}"
            )

        try:
            execution_order = int(rule_payload.get("execution_order"))
        except Exception as exc:
            raise CIPolicyEngineError(f"Rule {rule_id} execution_order must be an integer") from exc

        return RuleBinding(
            rule_id=rule_id,
            name=str(rule_payload.get("name", "")).strip(),
            description=str(rule_payload.get("description", "")).strip(),
            severity=severity,
            enforcement_type=enforcement_type,
            validator_id=validator_id,
            validator_module=validator_module,
            validator_callable=validator_callable,
            fail_behavior=fail_behavior,
            dependencies=[str(item) for item in dependencies],
            version=version,
            execution_order=execution_order,
        )

    def _execute_rule(self, rule: RuleBinding) -> Dict[str, Any]:
        try:
            module = importlib.import_module(rule.validator_module)
            validator = getattr(module, rule.validator_callable, None)
            if not callable(validator):
                return self._fail_result(
                    rule,
                    "VALIDATOR_FUNCTION_MISSING",
                    f"Function '{rule.validator_callable}' is not callable in {rule.validator_module}",
                )

            stream = io.StringIO()
            with redirect_stdout(stream):
                function_result = validator()
            validator_stdout = stream.getvalue().strip()

            validator_payload = self._parse_validator_output(validator_stdout)
            payload_schema_version = str(validator_payload.get("schema_version", ""))
            if payload_schema_version != CI_SCHEMA_VERSION:
                validator_payload = {
                    "status": "FAIL",
                    "schema_version": payload_schema_version,
                    "violations": [
                        {
                            "violation_type": "VALIDATOR_SCHEMA_VERSION_MISMATCH",
                            "severity": "CRITICAL",
                            "details": {
                                "expected": CI_SCHEMA_VERSION,
                                "actual": payload_schema_version or "unset",
                            },
                        }
                    ],
                }
                function_result = 1

            status = str(validator_payload.get("status", "")).upper()
            violations = validator_payload.get("violations", [])
            if not isinstance(violations, list):
                violations = []
            violation_count = len(violations)

            if status not in {"PASS", "FAIL"}:
                status = "FAIL" if int(function_result or 0) != 0 else "PASS"

            if int(function_result or 0) != 0 and status != "FAIL":
                status = "FAIL"

            return {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "severity": rule.severity,
                "status": status,
                "violation_count": violation_count,
                "validator_binding": {
                    "validator_id": rule.validator_id,
                    "callable": f"{rule.validator_module}.{rule.validator_callable}",
                },
                "validator_result": validator_payload,
            }
        except Exception as exc:  # pragma: no cover
            return self._fail_result(rule, "VALIDATOR_EXECUTION_ERROR", str(exc))

    def _parse_validator_output(self, output: str) -> Dict[str, Any]:
        if not output:
            return {}
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        return {
            "status": "FAIL",
            "schema_version": CI_SCHEMA_VERSION,
            "violations": [
                {
                    "violation_type": "VALIDATOR_OUTPUT_PARSE_ERROR",
                    "details": {
                        "output": output,
                    },
                }
            ],
        }

    def _fail_result(self, rule: RuleBinding, reason: str, message: str) -> Dict[str, Any]:
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "severity": rule.severity,
            "status": "FAIL",
            "violation_count": 1,
            "validator_binding": {
                "validator_id": rule.validator_id,
                "callable": f"{rule.validator_module}.{rule.validator_callable}",
            },
            "validator_result": {
                "status": "FAIL",
                "schema_version": CI_SCHEMA_VERSION,
                "violations": [
                    {
                        "violation_type": reason,
                        "rule_id": rule.rule_id,
                        "severity": "CRITICAL",
                        "details": {
                            "message": message,
                        },
                    }
                ],
            },
        }
