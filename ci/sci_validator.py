#!/usr/bin/env python3
"""SCI and governance hard gate validator for CI and runtime contexts."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract_registry import resolve_contract, validate_contract

CI_SCHEMA_VERSION = "1.0"

Decision = Dict[str, Optional[str]]


class JsonFormatter(logging.Formatter):
    """Simple JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


LOGGER = logging.getLogger("sci_validator")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_token(value: str) -> str:
    return str(value).strip().upper().replace(" ", "_")


def _decision_allow(run_id: str) -> Decision:
    return {
        "decision": "ALLOW",
        "reason_code": None,
        "violated_rule": None,
        "run_id": run_id,
    }


def _decision_block(run_id: str, reason_code: str, violated_rule: str) -> Decision:
    return {
        "decision": "BLOCK",
        "reason_code": reason_code,
        "violated_rule": violated_rule,
        "run_id": run_id,
    }


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
        "validator": "sci_validator",
        "status": "FAIL" if violations else "PASS",
        "schema_version": CI_SCHEMA_VERSION,
        "violations": violations,
        "file_path": first["file_path"] if first else "",
        "rule_id": first["rule_id"] if first else "",
        "severity": first["severity"] if first else "LOW",
        "timestamp": _utc_now(),
    }


def _keyword_violations(source_label: str, content: str) -> List[Dict[str, object]]:
    violations: List[Dict[str, object]] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        lowered = line.strip().lower()
        if not lowered:
            continue

        if "optional" in lowered and "non-optional" not in lowered:
            violations.append(
                _violation(
                    violation_type="SCI_KEYWORD_VIOLATION",
                    file_path=source_label,
                    rule_id="FORBIDDEN_KEYWORD_OPTIONAL",
                    severity="CRITICAL",
                    details={"line": line_number, "content": line.strip()},
                )
            )

        if "may bypass" in lowered:
            protective_context = ["must not", "no ", "cannot", "prohibit", "prohibited", "blocked"]
            if not any(token in lowered for token in protective_context):
                violations.append(
                    _violation(
                        violation_type="SCI_KEYWORD_VIOLATION",
                        file_path=source_label,
                        rule_id="FORBIDDEN_KEYWORD_MAY_BYPASS",
                        severity="CRITICAL",
                        details={"line": line_number, "content": line.strip()},
                    )
                )

        if "fallback authority" in lowered:
            violations.append(
                _violation(
                    violation_type="SCI_KEYWORD_VIOLATION",
                    file_path=source_label,
                    rule_id="FORBIDDEN_KEYWORD_FALLBACK_AUTHORITY",
                    severity="CRITICAL",
                    details={"line": line_number, "content": line.strip()},
                )
            )

        if "override" in lowered:
            protective_context = ["must not", "no ", "cannot", "prohibit", "prohibited", "blocked"]
            if not any(token in lowered for token in protective_context):
                violations.append(
                    _violation(
                        violation_type="SCI_KEYWORD_VIOLATION",
                        file_path=source_label,
                        rule_id="FORBIDDEN_KEYWORD_OVERRIDE",
                        severity="CRITICAL",
                        details={"line": line_number, "content": line.strip()},
                    )
                )

    return violations


def _load_sci_contract() -> Mapping[str, Any]:
    contract = resolve_contract("SCI_AUTHORITY")
    validate_contract("SCI_AUTHORITY", contract)
    return contract


def _runtime_rules_from_contract(contract: Mapping[str, Any]) -> Dict[str, Any]:
    field_type_tokens = contract.get("required_context_fields")
    if not isinstance(field_type_tokens, Mapping):
        raise ValueError("SCI_AUTHORITY required_context_fields must be a mapping")

    type_map = {
        "str": str,
        "string": str,
        "bool": bool,
        "int": int,
        "float": float,
        "dict": dict,
        "list": list,
    }

    required_context_fields: Dict[str, type] = {}
    for field, token in field_type_tokens.items():
        field_name = str(field).strip()
        type_token = str(token).strip().lower()
        if not field_name:
            raise ValueError("SCI_AUTHORITY required_context_fields contains empty field name")
        required_context_fields[field_name] = type_map.get(type_token, str)

    allowed_execution_modes = {
        _normalize_token(item)
        for item in (contract.get("allowed_execution_modes") or [])
        if str(item).strip()
    }
    allowed_data_classifications = {
        _normalize_token(item)
        for item in (contract.get("allowed_data_classifications") or [])
        if str(item).strip()
    }

    raw_aliases = contract.get("layer_aliases")
    if not isinstance(raw_aliases, Mapping):
        raise ValueError("SCI_AUTHORITY layer_aliases must be a mapping")
    layer_aliases = {
        _normalize_token(str(alias)): _normalize_token(str(canonical))
        for alias, canonical in raw_aliases.items()
        if str(alias).strip() and str(canonical).strip()
    }

    raw_actions = contract.get("allowed_layer_actions")
    if not isinstance(raw_actions, Mapping):
        raise ValueError("SCI_AUTHORITY allowed_layer_actions must be a mapping")
    allowed_layer_actions: Dict[str, set[str]] = {}
    for layer, actions in raw_actions.items():
        normalized_layer = _normalize_token(str(layer))
        if isinstance(actions, list):
            allowed_layer_actions[normalized_layer] = {
                _normalize_token(str(action)) for action in actions if str(action).strip()
            }

    state_actions = {
        _normalize_token(item)
        for item in (contract.get("state_actions") or [])
        if str(item).strip()
    }
    evidence_actions = {
        _normalize_token(item)
        for item in (contract.get("evidence_actions") or [])
        if str(item).strip()
    }
    forbidden_shortcut_tokens = {
        _normalize_token(item)
        for item in (contract.get("forbidden_shortcut_tokens") or [])
        if str(item).strip()
    }

    guard_policy = contract.get("guard_policy")
    if not isinstance(guard_policy, Mapping):
        raise ValueError("SCI_AUTHORITY guard_policy must be a mapping")

    return {
        "required_context_fields": required_context_fields,
        "allowed_execution_modes": allowed_execution_modes,
        "allowed_data_classifications": allowed_data_classifications,
        "layer_aliases": layer_aliases,
        "allowed_layer_actions": allowed_layer_actions,
        "state_actions": state_actions,
        "evidence_actions": evidence_actions,
        "forbidden_shortcut_tokens": forbidden_shortcut_tokens,
        "guard_policy": dict(guard_policy),
        "required_markers": [str(item) for item in (contract.get("required_markers") or [])],
        "authority_statements": [str(item) for item in (contract.get("authority_statements") or [])],
    }


def run() -> int:
    """Run contract-bound CI SCI validation."""

    violations: List[Dict[str, object]] = []

    try:
        contract = _load_sci_contract()
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="SCI_VALIDATION_FAILURE",
                file_path="contract:SCI_AUTHORITY",
                rule_id="SCI_CONTRACT_UNRESOLVED",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    try:
        rules = _runtime_rules_from_contract(contract)
    except Exception as exc:
        violations.append(
            _violation(
                violation_type="SCI_VALIDATION_FAILURE",
                file_path="contract:SCI_AUTHORITY",
                rule_id="SCI_CONTRACT_INVALID",
                severity="CRITICAL",
                details={"reason": str(exc)},
            )
        )
        report = _report(violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    combined_text = "\n".join(rules["authority_statements"])
    for marker in rules["required_markers"]:
        if marker not in combined_text:
            violations.append(
                _violation(
                    violation_type="SCI_AMBIGUITY",
                    file_path="contract:SCI_AUTHORITY",
                    rule_id="SCI_MARKER_MISSING",
                    severity="CRITICAL",
                    details={"missing_marker": marker},
                )
            )

    violations.extend(_keyword_violations("contract:SCI_AUTHORITY", combined_text))

    report = _report(violations)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if violations else 0


def _validate_sci_presence_and_readability(run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    markers = rules.get("required_markers")
    statements = rules.get("authority_statements")

    if not isinstance(markers, list) or not markers:
        return _decision_block(run_id, "SCI_CONTRACT_INVALID", "SCI_CLARITY")
    if not isinstance(statements, list) or not statements:
        return _decision_block(run_id, "SCI_CONTRACT_INVALID", "SCI_CLARITY")

    content = "\n".join(str(item) for item in statements)
    for marker in markers:
        if str(marker) not in content:
            return _decision_block(run_id, "SCI_AMBIGUOUS", "SCI_CLARITY")

    return None


def _validate_schema(context: Mapping[str, Any], run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    required_context_fields = rules.get("required_context_fields")
    if not isinstance(required_context_fields, Mapping):
        return _decision_block(run_id, "SCHEMA_CONTRACT_INVALID", "SCHEMA")

    for field, expected_type in required_context_fields.items():
        if field not in context:
            return _decision_block(run_id, "SCHEMA_MISSING_FIELD", f"REQUIRED_FIELD:{field}")
        value = context[field]
        if not isinstance(value, expected_type):
            return _decision_block(run_id, "SCHEMA_TYPE_ERROR", f"FIELD_TYPE:{field}")
        if isinstance(value, str) and not value.strip():
            return _decision_block(run_id, "SCHEMA_EMPTY_FIELD", f"FIELD_EMPTY:{field}")

    return None


def _validate_execution_mode(context: Mapping[str, Any], run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    mode = _normalize_token(str(context["execution_mode"]))
    allowed_execution_modes = rules.get("allowed_execution_modes")
    if not isinstance(allowed_execution_modes, set) or mode not in allowed_execution_modes:
        return _decision_block(run_id, "INVALID_EXECUTION_MODE", "EXECUTION_MODE_CONSTRAINT")
    return None


def _validate_device_ip(context: Mapping[str, Any], run_id: str) -> Optional[Decision]:
    device_ip = str(context["device_ip"]).strip()
    try:
        ipaddress.ip_address(device_ip)
    except ValueError:
        return _decision_block(run_id, "INVALID_DEVICE_IP", "IP_ONLY_IDENTITY")
    return None


def _normalize_layer(layer_raw: str, rules: Mapping[str, Any]) -> str:
    token = _normalize_token(layer_raw)
    layer_aliases = rules.get("layer_aliases")
    if isinstance(layer_aliases, Mapping):
        return str(layer_aliases.get(token, token))
    return token


def _validate_layer_action(context: Mapping[str, Any], run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    source_layer = _normalize_layer(str(context["source_layer"]), rules)
    target_action = _normalize_token(str(context["target_action"]))

    allowed_layer_actions = rules.get("allowed_layer_actions")
    if not isinstance(allowed_layer_actions, Mapping):
        return _decision_block(run_id, "LAYER_POLICY_INVALID", "LAYER_BOUNDARY")

    if source_layer not in allowed_layer_actions:
        return _decision_block(run_id, "UNKNOWN_SOURCE_LAYER", "LAYER_BOUNDARY")

    forbidden_shortcut_tokens = rules.get("forbidden_shortcut_tokens")
    if isinstance(forbidden_shortcut_tokens, set):
        for forbidden_token in forbidden_shortcut_tokens:
            if forbidden_token in target_action:
                return _decision_block(run_id, "CROSS_LAYER_SHORTCUT", "NO_LAYER_SKIPPING")

    layer_actions = allowed_layer_actions.get(source_layer)
    if not isinstance(layer_actions, set) or target_action not in layer_actions:
        return _decision_block(run_id, "LAYER_ACTION_VIOLATION", "LAYER_BOUNDARY")

    if source_layer == "INGESTION" and target_action in {"STORE_VALIDATED_STATE", "SSH_VALIDATION", "CONFIG_CHANGE"}:
        return _decision_block(run_id, "INGESTION_BYPASS", "NO_CROSS_LAYER_SHORTCUT")

    if source_layer == "EXTERNAL_SOURCES" and target_action in {
        "STORE_VALIDATED_STATE",
        "SSH_VALIDATION",
        "CONFIG_CHANGE",
    }:
        return _decision_block(run_id, "EXTERNAL_BYPASS", "NO_CROSS_LAYER_SHORTCUT")

    return None


def _validate_data_classification(context: Mapping[str, Any], run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    classification = _normalize_token(str(context["data_classification"]))
    target_action = _normalize_token(str(context["target_action"]))

    allowed_data_classifications = rules.get("allowed_data_classifications")
    if not isinstance(allowed_data_classifications, set) or classification not in allowed_data_classifications:
        return _decision_block(run_id, "INVALID_DATA_CLASSIFICATION", "EVIDENCE_STATE_CLASSIFICATION")

    state_actions = rules.get("state_actions")
    if isinstance(state_actions, set) and target_action in state_actions and classification != "STATE":
        return _decision_block(run_id, "CLASSIFICATION_MISMATCH", "STATE_ACTION_REQUIRES_STATE_CLASS")

    evidence_actions = rules.get("evidence_actions")
    if isinstance(evidence_actions, set) and target_action in evidence_actions and classification != "EVIDENCE":
        return _decision_block(run_id, "CLASSIFICATION_MISMATCH", "EVIDENCE_ACTION_REQUIRES_EVIDENCE_CLASS")

    return None


def _validate_guard(context: Mapping[str, Any], run_id: str, rules: Mapping[str, Any]) -> Optional[Decision]:
    mode = _normalize_token(str(context["execution_mode"]))

    guard_policy = rules.get("guard_policy")
    if not isinstance(guard_policy, Mapping):
        return _decision_block(run_id, "GUARD_POLICY_INVALID", "GLOBAL_GUARD_COMPLIANCE")

    guard_env_var = str(guard_policy.get("env_var", "GLOBAL_AUTOMATION_GUARD"))
    guard_value = context.get("global_automation_guard")
    if guard_value is None:
        guard_value = os.environ.get(guard_env_var)

    if not isinstance(guard_value, str) or not guard_value.strip():
        return _decision_block(run_id, "GUARD_STATE_MISSING", "GLOBAL_GUARD_COMPLIANCE")

    guard_state = _normalize_token(guard_value)
    allowed_guard_states = {
        _normalize_token(str(item))
        for item in (guard_policy.get("allowed_states") or [])
        if str(item).strip()
    }
    if guard_state not in allowed_guard_states:
        return _decision_block(run_id, "GUARD_STATE_INVALID", "GLOBAL_GUARD_COMPLIANCE")

    sci_permit = context.get("sci_permit", False)
    if mode == "CHANGE_ENABLED":
        if bool(guard_policy.get("change_enabled_requires_open", True)) and guard_state != "OPEN":
            return _decision_block(run_id, "CHANGE_MODE_GUARD_CLOSED", "GLOBAL_GUARD_COMPLIANCE")
        if bool(guard_policy.get("change_enabled_requires_sci_permit", True)):
            if not isinstance(sci_permit, bool) or not sci_permit:
                return _decision_block(run_id, "SCI_PERMIT_MISSING", "SCI_CHANGE_PERMIT")
    else:
        if bool(guard_policy.get("non_change_mode_forbids_open_guard", True)) and guard_state == "OPEN":
            return _decision_block(run_id, "GUARD_OPEN_IN_NON_CHANGE_MODE", "GLOBAL_GUARD_COMPLIANCE")

    return None


def SCI_VALIDATE(context: Mapping[str, Any]) -> Decision:
    """Validate a runtime context against SCI rules using fail-closed behavior."""

    run_id = str(context.get("run_id", "UNKNOWN")) if isinstance(context, Mapping) else "UNKNOWN"

    try:
        if not isinstance(context, Mapping):
            return _decision_block("UNKNOWN", "INVALID_CONTEXT", "SCHEMA")

        contract = _load_sci_contract()
        rules = _runtime_rules_from_contract(contract)

        checks = [
            _validate_sci_presence_and_readability(run_id, rules),
            _validate_schema(context, run_id, rules),
            _validate_execution_mode(context, run_id, rules),
            _validate_device_ip(context, run_id),
            _validate_layer_action(context, run_id, rules),
            _validate_data_classification(context, run_id, rules),
            _validate_guard(context, run_id, rules),
        ]

        for result in checks:
            if result is not None:
                LOGGER.info(
                    json.dumps(
                        {
                            "event": "SCI_VALIDATE",
                            "decision": result["decision"],
                            "reason_code": result["reason_code"],
                            "violated_rule": result["violated_rule"],
                            "run_id": result["run_id"],
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return result

        decision = _decision_allow(run_id)
        LOGGER.info(
            json.dumps(
                {
                    "event": "SCI_VALIDATE",
                    "decision": decision["decision"],
                    "reason_code": decision["reason_code"],
                    "violated_rule": decision["violated_rule"],
                    "run_id": decision["run_id"],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return decision

    except Exception:
        blocked = _decision_block(run_id, "VALIDATION_EXCEPTION", "FAIL_CLOSED")
        LOGGER.exception(
            json.dumps(
                {
                    "event": "SCI_VALIDATE",
                    "decision": blocked["decision"],
                    "reason_code": blocked["reason_code"],
                    "violated_rule": blocked["violated_rule"],
                    "run_id": blocked["run_id"],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return blocked


def _load_context_from_args(args: argparse.Namespace) -> Mapping[str, Any]:
    if args.context_json:
        loaded = json.loads(args.context_json)
        if not isinstance(loaded, dict):
            raise ValueError("context_json must decode to an object")
        return loaded

    if args.context_file:
        content = Path(args.context_file).read_text(encoding="utf-8")
        loaded = json.loads(content)
        if not isinstance(loaded, dict):
            raise ValueError("context_file must decode to an object")
        return loaded

    raise ValueError("Either --context-json or --context-file is required")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SCI validator")
    parser.add_argument(
        "--mode",
        choices=["auto", "ci", "runtime"],
        default="auto",
        help="Validation mode. 'auto' runs runtime validation when context is provided, otherwise CI mode.",
    )
    parser.add_argument("--context-json", help="Inline JSON context object")
    parser.add_argument("--context-file", help="Path to JSON context file")
    args = parser.parse_args(argv)

    runtime_requested = args.mode == "runtime" or bool(args.context_json or args.context_file)
    if not runtime_requested:
        return run()

    try:
        context = _load_context_from_args(args)
    except Exception:
        result = _decision_block("UNKNOWN", "CONTEXT_PARSE_ERROR", "SCHEMA")
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 2

    result = SCI_VALIDATE(context)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["decision"] == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
