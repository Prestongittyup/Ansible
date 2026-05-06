"""Consumer-side contract validation for schema 2.1 validation results payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

REQUIRED_SCHEMA_VERSION = "2.1"
REQUIRED_FIELDS = (
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

ALLOWED_EXECUTION_STATES = frozenset(
    {
        "SUCCESS",
        "FAIL_RUNTIME",
        "FAIL_CONNECTIVITY",
        "FAIL_VALIDATION",
        "NOT_EXECUTED",
    }
)
ALLOWED_CONNECTIVITY_STATUS = frozenset({"REACHABLE", "UNREACHABLE", "NOT_EXECUTED"})
ALLOWED_VALIDATION_STATUS = frozenset({"PASSED", "FAILED", "NOT_EXECUTED"})
ALLOWED_VENDOR = frozenset({"aruba", "cisco", "fortinet", "juniper", "meraki", "unknown"})


class ContractValidationError(RuntimeError):
    """Raised when payload contract validation fails in a fail-closed way."""

    def __init__(self, code: str, message: str, details: List[Dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _raise(code: str, message: str, details: List[Dict[str, Any]] | None = None) -> None:
    raise ContractValidationError(code=code, message=message, details=details)


def _validate_enum(field: str, value: Any, allowed: frozenset[str], index: int) -> None:
    if not isinstance(value, str) or value not in allowed:
        _raise(
            code="INVALID_ENUM",
            message=f"Record at index {index} has invalid {field}",
            details=[
                {
                    "index": index,
                    "field": field,
                    "value": value,
                    "allowed": sorted(allowed),
                }
            ],
        )


def _validate_error_object(value: Any, index: int) -> None:
    if value is None:
        return

    if not isinstance(value, dict):
        _raise(
            code="MALFORMED_ERROR_OBJECT",
            message=f"Record at index {index} has malformed error object",
            details=[{"index": index, "error_type": type(value).__name__}],
        )

    required_error_fields = ("code", "message", "source")
    missing = [field for field in required_error_fields if field not in value]
    if missing:
        _raise(
            code="MALFORMED_ERROR_OBJECT",
            message=f"Record at index {index} error object is missing required fields",
            details=[{"index": index, "missing_fields": missing}],
        )

    for field in required_error_fields:
        field_value = value.get(field)
        if not isinstance(field_value, str) or not field_value:
            _raise(
                code="MALFORMED_ERROR_OBJECT",
                message=f"Record at index {index} error.{field} must be a non-empty string",
                details=[{"index": index, "field": field, "value": field_value}],
            )

    if "details" in value and value["details"] is not None and not isinstance(value["details"], dict):
        _raise(
            code="MALFORMED_ERROR_OBJECT",
            message=f"Record at index {index} error.details must be an object when present",
            details=[{"index": index, "field": "details", "value": value["details"]}],
        )


def validate_payload(payload: Dict[str, Any]) -> Tuple[List[Tuple[int, Dict[str, Any]]], int]:
    """Validate payload contract and return deterministic accepted records and drop count."""
    if not isinstance(payload, dict):
        _raise("INVALID_PAYLOAD", "Payload must be an object")

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        _raise("INVALID_METADATA", "metadata section is missing or invalid")

    metadata_schema = metadata.get("schema_version")
    if metadata_schema != REQUIRED_SCHEMA_VERSION:
        _raise(
            "INVALID_SCHEMA_VERSION",
            f"metadata.schema_version must be {REQUIRED_SCHEMA_VERSION}",
            details=[{"field": "metadata.schema_version", "value": metadata_schema}],
        )

    results = payload.get("results")
    if not isinstance(results, list):
        _raise("INVALID_RESULTS", "results section is missing or invalid")

    accepted: List[Tuple[int, Dict[str, Any]]] = []
    dropped = 0

    for index, record in enumerate(results):
        if not isinstance(record, dict):
            _raise(
                "INVALID_RECORD",
                f"Record at index {index} is not an object",
                details=[{"index": index, "record_type": type(record).__name__}],
            )

        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            if "ip_address" in missing:
                dropped += 1
                continue
            _raise(
                "MISSING_REQUIRED_FIELDS",
                f"Record at index {index} is missing required fields",
                details=[{"index": index, "missing_fields": missing}],
            )

        ip_address = record.get("ip_address")
        if not isinstance(ip_address, str) or not ip_address.strip():
            dropped += 1
            continue

        if record.get("schema_version") != REQUIRED_SCHEMA_VERSION:
            _raise(
                "INVALID_SCHEMA_VERSION",
                f"Record at index {index} has invalid schema_version",
                details=[{"index": index, "field": "schema_version", "value": record.get("schema_version")}],
            )

        vendor = record.get("vendor")
        if not isinstance(vendor, str) or vendor not in ALLOWED_VENDOR:
            _raise(
                "INVALID_ENUM",
                f"Record at index {index} has invalid vendor",
                details=[{"index": index, "field": "vendor", "value": vendor, "allowed": sorted(ALLOWED_VENDOR)}],
            )

        _validate_enum("execution_state", record.get("execution_state"), ALLOWED_EXECUTION_STATES, index)
        _validate_enum("connectivity_status", record.get("connectivity_status"), ALLOWED_CONNECTIVITY_STATUS, index)
        _validate_enum("validation_status", record.get("validation_status"), ALLOWED_VALIDATION_STATUS, index)

        facts_collected = record.get("facts_collected")
        if facts_collected is not None and not isinstance(facts_collected, dict):
            _raise(
                "INVALID_FACTS_COLLECTED",
                f"Record at index {index} has invalid facts_collected",
                details=[{"index": index, "field": "facts_collected", "value_type": type(facts_collected).__name__}],
            )

        attempt_count = record.get("attempt_count")
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 0:
            _raise(
                "INVALID_ATTEMPT_COUNT",
                f"Record at index {index} has invalid attempt_count",
                details=[{"index": index, "field": "attempt_count", "value": attempt_count}],
            )

        _validate_error_object(record.get("error"), index)

        accepted.append((index, dict(record)))

    accepted.sort(key=lambda item: (item[1]["ip_address"], item[0]))
    return accepted, dropped
