"""Versioned runtime schema gate for Sprint 2 validation outputs."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from legacy.execution_contract.execution_state import ExecutionState, allowed_execution_states, require_execution_state
from legacy.execution_contract.failure_taxonomy import ALLOWED_CONNECTIVITY_STATUS, ALLOWED_VALIDATION_STATUS

SCHEMA_VERSION = "2.1"
EXECUTION_CONTRACT_SCHEMA_VERSION = SCHEMA_VERSION
SCHEMA_AUTHORITY_DOMAIN = "execution_contract_validation_output"
ALLOWED_VENDORS: Tuple[str, ...] = ("aruba", "cisco", "fortinet", "juniper", "meraki", "unknown")

RESULT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "ip_address",
    "vendor",
    "execution_state",
    "connectivity_status",
    "validation_status",
    "facts_collected",
    "error",
    "attempt_count",
    "schema_version",
)

VALIDATION_RESULT_SCHEMA: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "required_fields": list(RESULT_REQUIRED_FIELDS),
    "vendor_enum": list(ALLOWED_VENDORS),
    "execution_state_enum": list(allowed_execution_states()),
    "connectivity_status_enum": list(ALLOWED_CONNECTIVITY_STATUS),
    "validation_status_enum": list(ALLOWED_VALIDATION_STATUS),
    "facts_collected_type": "object_or_null",
    "error_type": "object_or_null",
    "attempt_count_type": "integer",
}


class SchemaContractError(ValueError):
    """Raised when output cannot satisfy runtime schema contract enforcement."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "RESULT_SCHEMA_MISMATCH",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _trim_text(value: str, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def schema_version() -> str:
    """Return active runtime schema version."""
    return EXECUTION_CONTRACT_SCHEMA_VERSION


def build_error(
    *,
    code: str,
    message: str,
    source: str,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build structured error object for schema-compliant failures."""
    return {
        "code": str(code).strip(),
        "message": _trim_text(message),
        "source": str(source).strip() or "unknown",
        "details": dict(details or {}),
    }


def _validate_error(error: Mapping[str, Any]) -> None:
    required = {"code", "message", "source", "details"}
    keys = set(error.keys())
    if keys != required:
        raise SchemaContractError("error object schema mismatch", code="MALFORMED_ERROR_OBJECT")

    if not isinstance(error["code"], str) or not error["code"].strip():
        raise SchemaContractError("error.code must be a non-empty string", code="MALFORMED_ERROR_OBJECT")
    if not isinstance(error["message"], str) or not error["message"].strip():
        raise SchemaContractError("error.message must be a non-empty string", code="MALFORMED_ERROR_OBJECT")
    if not isinstance(error["source"], str) or not error["source"].strip():
        raise SchemaContractError("error.source must be a non-empty string", code="MALFORMED_ERROR_OBJECT")
    if not isinstance(error["details"], Mapping):
        raise SchemaContractError("error.details must be an object", code="MALFORMED_ERROR_OBJECT")


def validate_result(result: Mapping[str, Any]) -> None:
    """Validate a single result object against runtime schema gate."""
    expected_fields = set(RESULT_REQUIRED_FIELDS)
    keys = set(result.keys())

    if "schema_version" not in keys:
        raise SchemaContractError("result.schema_version missing", code="SCHEMA_VERSION_MISSING")
    if keys != expected_fields:
        raise SchemaContractError(
            "result schema field mismatch",
            code="RESULT_SCHEMA_MISMATCH",
            details={"expected": sorted(expected_fields), "actual": sorted(keys)},
        )

    try:
        ipaddress.ip_address(str(result["ip_address"]))
    except ValueError as exc:
        raise SchemaContractError("result.ip_address must be a valid IP address", code="RESULT_SCHEMA_MISMATCH") from exc

    vendor = result["vendor"]
    if vendor not in ALLOWED_VENDORS:
        raise SchemaContractError("result.vendor is not in allowed enum", code="RESULT_SCHEMA_MISMATCH")

    try:
        execution_state = require_execution_state(str(result["execution_state"]))
    except ValueError as exc:
        raise SchemaContractError("result.execution_state is invalid", code="EXECUTION_STATE_INVALID") from exc

    connectivity_status = result["connectivity_status"]
    if connectivity_status not in ALLOWED_CONNECTIVITY_STATUS:
        raise SchemaContractError("result.connectivity_status is not in allowed enum", code="RESULT_SCHEMA_MISMATCH")

    validation_status = result["validation_status"]
    if validation_status not in ALLOWED_VALIDATION_STATUS:
        raise SchemaContractError("result.validation_status is not in allowed enum", code="RESULT_SCHEMA_MISMATCH")

    if not isinstance(result["attempt_count"], int) or result["attempt_count"] < 0:
        raise SchemaContractError("result.attempt_count must be a non-negative integer", code="RESULT_SCHEMA_MISMATCH")

    if result["schema_version"] != SCHEMA_VERSION:
        raise SchemaContractError("result.schema_version mismatch", code="SCHEMA_VERSION_INVALID")

    facts_collected = result["facts_collected"]
    error = result["error"]

    if execution_state == ExecutionState.SUCCESS.value:
        if not isinstance(facts_collected, Mapping):
            raise SchemaContractError("SUCCESS result requires facts_collected object", code="RESULT_SCHEMA_MISMATCH")
        if error is not None:
            raise SchemaContractError("SUCCESS result must not include error", code="RESULT_SCHEMA_MISMATCH")
    else:
        if facts_collected is not None and not isinstance(facts_collected, Mapping):
            raise SchemaContractError("facts_collected must be object or null", code="RESULT_SCHEMA_MISMATCH")
        if not isinstance(error, Mapping):
            raise SchemaContractError("failed/not-executed result requires structured error", code="MALFORMED_ERROR_OBJECT")
        _validate_error(error)


def validate_results(results: Sequence[Mapping[str, Any]]) -> None:
    """Validate all result objects as a strict runtime gate."""
    for result in results:
        validate_result(result)


def build_result(
    *,
    ip_address: str,
    vendor: str,
    execution_state: str,
    connectivity_status: str,
    validation_status: str,
    facts_collected: Optional[Mapping[str, Any]],
    error: Optional[Mapping[str, Any]],
    attempt_count: int,
) -> Dict[str, Any]:
    """Build and validate a result object for the active schema version."""
    result: Dict[str, Any] = {
        "ip_address": str(ipaddress.ip_address(str(ip_address).strip())),
        "vendor": str(vendor or "unknown").strip().lower() or "unknown",
        "execution_state": require_execution_state(str(execution_state).strip()),
        "connectivity_status": str(connectivity_status).strip().upper(),
        "validation_status": str(validation_status).strip().upper(),
        "facts_collected": dict(facts_collected) if isinstance(facts_collected, Mapping) else None,
        "error": dict(error) if isinstance(error, Mapping) else None,
        "attempt_count": int(attempt_count),
        "schema_version": SCHEMA_VERSION,
    }
    validate_result(result)
    return result


def write_validation_output(
    *,
    output_path: Path,
    run_id: str,
    execution_mode: str,
    runtime_standard: str,
    python_runtime: str,
    processed_targets: int,
    aruba_targets: int,
    non_aruba_targets: int,
    max_concurrency: int,
    max_retries: int,
    results: Sequence[Mapping[str, Any]],
) -> None:
    """Write output only after strict runtime schema validation passes."""
    validate_results(results)

    payload = {
        "metadata": {
            "run_id": str(run_id),
            "execution_mode": str(execution_mode),
            "runtime_standard": str(runtime_standard),
            "python_runtime": str(python_runtime),
            "schema_version": SCHEMA_VERSION,
            "processed_targets": int(processed_targets),
            "aruba_targets": int(aruba_targets),
            "non_aruba_targets": int(non_aruba_targets),
            "max_concurrency": int(max_concurrency),
            "max_retries": int(max_retries),
        },
        "schema": VALIDATION_RESULT_SCHEMA,
        "results": [dict(item) for item in results],
    }

    if payload["metadata"]["schema_version"] != SCHEMA_VERSION:
        raise SchemaContractError("metadata.schema_version mismatch", code="SCHEMA_VERSION_INVALID")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
