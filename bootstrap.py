#!/usr/bin/env python3
"""Sprint 2 bootstrap entrypoint enforcing pre-import runtime boundary."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from contract_registry import resolve_contract, validate_contract


def _emit_fail_closed(*, error_code: str, message: str, source: str, details: dict | None = None) -> int:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "bootstrap_blocked",
        "status": "FAIL",
        "execution_state": "FAIL_RUNTIME",
        "error": {
            "code": error_code,
            "message": message,
            "source": source,
            "details": details or {},
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 2


def _resolve_attribute(module_name: str, attribute_name: str) -> Any:
    module = importlib.import_module(module_name)
    if not hasattr(module, attribute_name):
        raise AttributeError(f"{module_name} missing attribute: {attribute_name}")
    return getattr(module, attribute_name)


def _resolve_callable(module_name: str, attribute_name: str) -> Callable[..., Any]:
    candidate = _resolve_attribute(module_name, attribute_name)
    if not callable(candidate):
        raise TypeError(f"{module_name}.{attribute_name} must be callable")
    return candidate


def main() -> int:
    try:
        legacy_bridge = resolve_contract("LEGACY_BRIDGE")
        validate_contract("LEGACY_BRIDGE", legacy_bridge)

        execution_authority = resolve_contract("EXECUTION_AUTHORITY")
        validate_contract("EXECUTION_AUTHORITY", execution_authority)
    except Exception as exc:
        return _emit_fail_closed(
            error_code="CONTRACT_RESOLUTION_FAILURE",
            message="unable to resolve bootstrap contracts",
            source="bootstrap",
            details={"reason": str(exc)},
        )

    if not bool(legacy_bridge.get("locked", False)) or not bool(legacy_bridge.get("immutable", False)):
        return _emit_fail_closed(
            error_code="LEGACY_BRIDGE_UNLOCKED",
            message="legacy bridge contract is not locked",
            source="bootstrap",
            details={"contract": "LEGACY_BRIDGE"},
        )

    if not str(legacy_bridge.get("lock_marker", "")).strip():
        return _emit_fail_closed(
            error_code="LEGACY_LOCK_MARKER_MISSING",
            message="legacy bridge lock marker is missing",
            source="bootstrap",
            details={"contract": "LEGACY_BRIDGE"},
        )

    if not bool(execution_authority.get("bootstrap_runtime_guard_required", True)):
        return _emit_fail_closed(
            error_code="RUNTIME_GUARD_CONTRACT_DISABLED",
            message="runtime guard requirement cannot be disabled",
            source="bootstrap",
            details={"contract": "EXECUTION_AUTHORITY"},
        )

    try:
        runtime_guard_cfg = legacy_bridge.get("runtime_guard", {})
        if not isinstance(runtime_guard_cfg, dict):
            raise ValueError("runtime_guard configuration is missing")

        runtime_guard_module = str(runtime_guard_cfg.get("module", "")).strip()
        if not runtime_guard_module:
            raise ValueError("runtime_guard module is missing")

        runtime_standard_name = str(runtime_guard_cfg.get("runtime_standard_constant", "")).strip()
        runtime_error_type_name = str(runtime_guard_cfg.get("error_type", "")).strip()
        enforce_callable_name = str(runtime_guard_cfg.get("enforce_callable", "")).strip()
        current_runtime_callable_name = str(runtime_guard_cfg.get("python_runtime_callable", "")).strip()

        EXECUTION_LAYER_RUNTIME_STANDARD = _resolve_attribute(runtime_guard_module, runtime_standard_name)
        RuntimeGuardError = _resolve_attribute(runtime_guard_module, runtime_error_type_name)
        enforce_preimport_runtime_311 = _resolve_callable(runtime_guard_module, enforce_callable_name)
        current_python_runtime = _resolve_callable(runtime_guard_module, current_runtime_callable_name)
    except Exception as exc:
        return _emit_fail_closed(
            error_code="RUNTIME_GUARD_IMPORT_FAILURE",
            message="unable to resolve runtime guard bridge",
            source="bootstrap",
            details={"reason": str(exc)},
        )

    try:
        runtime_context = enforce_preimport_runtime_311()
    except Exception as exc:
        if isinstance(exc, RuntimeGuardError):
            payload = exc.to_payload() if hasattr(exc, "to_payload") else {"error": str(exc)}
            payload["runtime_standard"] = EXECUTION_LAYER_RUNTIME_STANDARD
            payload["python_runtime"] = current_python_runtime()
            payload["event"] = "bootstrap_blocked"
            print(json.dumps(payload, sort_keys=True))
            return 2
        return _emit_fail_closed(
            error_code="RUNTIME_GUARD_EXECUTION_FAILURE",
            message="runtime guard raised an unexpected exception",
            source="bootstrap",
            details={"reason": str(exc)},
        )

    try:
        inventory_cfg = legacy_bridge.get("inventory", {})
        runner_cfg = legacy_bridge.get("runner", {})
        schema_cfg = legacy_bridge.get("schema", {})
        if not isinstance(inventory_cfg, dict) or not isinstance(runner_cfg, dict) or not isinstance(schema_cfg, dict):
            raise ValueError("legacy bridge sub-contracts are invalid")

        inventory_module = str(inventory_cfg.get("module", "")).strip()
        inventory_error_name = str(inventory_cfg.get("error_type", "")).strip()
        runner_module = str(runner_cfg.get("module", "")).strip()
        runner_error_name = str(runner_cfg.get("error_type", "")).strip()
        build_parser_name = str(runner_cfg.get("build_argument_parser", "")).strip()
        run_orchestration_name = str(runner_cfg.get("run_orchestration", "")).strip()
        schema_module = str(schema_cfg.get("module", "")).strip()
        schema_error_name = str(schema_cfg.get("error_type", "")).strip()
        schema_version_name = str(schema_cfg.get("schema_version_callable", "")).strip()

        InventoryLoadError = _resolve_attribute(inventory_module, inventory_error_name)
        ValidationSystemError = _resolve_attribute(runner_module, runner_error_name)
        build_argument_parser = _resolve_callable(runner_module, build_parser_name)
        run_orchestration = _resolve_callable(runner_module, run_orchestration_name)
        SchemaContractError = _resolve_attribute(schema_module, schema_error_name)
        schema_version = _resolve_callable(schema_module, schema_version_name)
    except Exception as exc:
        return _emit_fail_closed(
            error_code="LEGACY_BRIDGE_RESOLUTION_FAILURE",
            message="unable to resolve legacy execution bridge",
            source="bootstrap",
            details={"reason": str(exc)},
        )

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        result = run_orchestration(
            args,
            runtime_standard=runtime_context.runtime_standard,
            python_runtime=runtime_context.python_runtime,
        )
    except Exception as exc:
        if isinstance(exc, InventoryLoadError):
            return _emit_fail_closed(
                error_code="INVENTORY_LOAD_FAILURE",
                message=str(exc),
                source="bootstrap",
                details={},
            )

        if isinstance(exc, ValidationSystemError):
            details = {
                "stage": getattr(exc, "stage", ""),
            }
            raw_details = getattr(exc, "details", {})
            if isinstance(raw_details, dict):
                details.update(raw_details)
            return _emit_fail_closed(
                error_code=str(getattr(exc, "code", "VALIDATION_SYSTEM_ERROR")),
                message=str(exc),
                source="bootstrap",
                details=details,
            )

        if isinstance(exc, SchemaContractError):
            raw_details = getattr(exc, "details", {})
            return _emit_fail_closed(
                error_code=str(getattr(exc, "code", "SCHEMA_CONTRACT_ERROR")),
                message=str(exc),
                source="bootstrap",
                details=dict(raw_details) if isinstance(raw_details, dict) else {},
            )

        return _emit_fail_closed(
            error_code="BOOTSTRAP_UNHANDLED_EXCEPTION",
            message="unhandled bootstrap failure",
            source="bootstrap",
            details={"reason": str(exc)},
        )

    output = {
        "status": result["status"],
        "run_id": result["run_id"],
        "output_file": result["output_file"],
        "schema_version": schema_version(),
    }
    print(json.dumps(output, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
