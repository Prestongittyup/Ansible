"""Fail-closed validation for query inputs and query output contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence, Tuple, cast

from legacy.query_layer.query_contracts import (
    ALLOWED_CONNECTIVITY_STATUS,
    ALLOWED_EXECUTION_STATES,
    ALLOWED_VALIDATION_STATUS,
    ALLOWED_VENDOR,
    DEVICE_KEYS,
    DEVICE_STATE_KEYS,
    EVIDENCE_KEYS,
    VALIDATION_STATE_KEYS,
    DeviceRecord,
    DeviceStateRecord,
    EvidenceRecord,
    ValidationStateRecord,
)


class QueryValidationError(RuntimeError):
    """Raised when query input or output fails contract validation."""


def _raise(message: str) -> None:
    raise QueryValidationError(message)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_ip_address(ip_address: Any) -> str:
    if not _is_non_empty_string(ip_address):
        _raise("ip_address must be a non-empty string")
    return str(ip_address).strip()


def validate_vendor(vendor: Any) -> str:
    if not _is_non_empty_string(vendor):
        _raise("vendor must be a non-empty string")
    normalized = str(vendor).strip()
    if normalized not in ALLOWED_VENDOR:
        _raise(f"invalid vendor: {normalized}")
    return normalized


def validate_execution_state(execution_state: Any) -> str:
    if not _is_non_empty_string(execution_state):
        _raise("execution_state must be a non-empty string")
    normalized = str(execution_state).strip()
    if normalized not in ALLOWED_EXECUTION_STATES:
        _raise(f"invalid execution_state: {normalized}")
    return normalized


def validate_run_id(run_id: Any) -> str:
    if not _is_non_empty_string(run_id):
        _raise("run_id must be a non-empty string")
    return str(run_id).strip()


def validate_pagination(limit: Any, offset: Any) -> Tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > 1000:
        _raise("limit must be an integer in range 1..1000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        _raise("offset must be a non-negative integer")
    return limit, offset


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not _is_non_empty_string(value):
        _raise(f"{field_name} must be a non-empty timestamp string")

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        _raise(f"{field_name} must be ISO-8601 timestamp: {exc}")


def validate_time_range(start_timestamp: Any, end_timestamp: Any) -> Tuple[str, str]:
    start_dt = _parse_timestamp(start_timestamp, "start_timestamp")
    end_dt = _parse_timestamp(end_timestamp, "end_timestamp")
    if start_dt > end_dt:
        _raise("start_timestamp must be <= end_timestamp")
    return start_dt.isoformat(), end_dt.isoformat()


def _validate_exact_keys(record: Dict[str, Any], expected: Iterable[str], label: str) -> None:
    expected_set = set(expected)
    actual_set = set(record.keys())
    if actual_set != expected_set:
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        _raise(f"{label} keys mismatch: missing={missing}, extra={extra}")


def validate_device_record(record: Dict[str, Any]) -> DeviceRecord:
    _validate_exact_keys(record, DEVICE_KEYS, "DeviceRecord")
    ip_address = validate_ip_address(record.get("ip_address"))
    vendor = validate_vendor(record.get("vendor"))
    _parse_timestamp(record.get("last_seen"), "last_seen")
    return cast(DeviceRecord, {"ip_address": ip_address, "vendor": vendor, "last_seen": str(record["last_seen"])})


def validate_device_state_record(record: Dict[str, Any]) -> DeviceStateRecord:
    _validate_exact_keys(record, DEVICE_STATE_KEYS, "DeviceStateRecord")
    ip_address = validate_ip_address(record.get("ip_address"))
    vendor = validate_vendor(record.get("vendor"))
    execution_state = validate_execution_state(record.get("execution_state"))
    _parse_timestamp(record.get("last_updated"), "last_updated")
    return cast(
        DeviceStateRecord,
        {
            "ip_address": ip_address,
            "vendor": vendor,
            "execution_state": execution_state,
            "last_updated": str(record["last_updated"]),
        },
    )


def validate_validation_state_record(record: Dict[str, Any]) -> ValidationStateRecord:
    _validate_exact_keys(record, VALIDATION_STATE_KEYS, "ValidationStateRecord")
    ip_address = validate_ip_address(record.get("ip_address"))
    execution_state = validate_execution_state(record.get("execution_state"))

    connectivity_status = record.get("connectivity_status")
    if connectivity_status not in ALLOWED_CONNECTIVITY_STATUS:
        _raise(f"invalid connectivity_status: {connectivity_status}")

    validation_status = record.get("validation_status")
    if validation_status not in ALLOWED_VALIDATION_STATUS:
        _raise(f"invalid validation_status: {validation_status}")

    facts_collected = record.get("facts_collected")
    if facts_collected is not None and not isinstance(facts_collected, dict):
        _raise("facts_collected must be object or null")

    attempt_count = record.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 0:
        _raise("attempt_count must be a non-negative integer")

    _parse_timestamp(record.get("last_updated"), "last_updated")

    return cast(
        ValidationStateRecord,
        {
            "ip_address": ip_address,
            "execution_state": execution_state,
            "connectivity_status": str(connectivity_status),
            "validation_status": str(validation_status),
            "facts_collected": facts_collected,
            "attempt_count": attempt_count,
            "last_updated": str(record["last_updated"]),
        },
    )


def validate_evidence_record(record: Dict[str, Any]) -> EvidenceRecord:
    _validate_exact_keys(record, EVIDENCE_KEYS, "EvidenceRecord")
    ip_address = validate_ip_address(record.get("ip_address"))
    run_id = validate_run_id(record.get("run_id"))
    execution_state = validate_execution_state(record.get("execution_state"))
    _parse_timestamp(record.get("timestamp"), "timestamp")

    raw_payload = record.get("raw_payload")
    if not isinstance(raw_payload, dict):
        _raise("raw_payload must be an object")

    return cast(
        EvidenceRecord,
        {
            "ip_address": ip_address,
            "run_id": run_id,
            "timestamp": str(record["timestamp"]),
            "execution_state": execution_state,
            "raw_payload": raw_payload,
        },
    )


def assert_ordered_by_ip(records: Sequence[Dict[str, Any]]) -> None:
    ips = [str(item.get("ip_address", "")) for item in records]
    if ips != sorted(ips):
        _raise("records are not deterministically ordered by ip_address")


def assert_ordered_evidence(records: Sequence[Dict[str, Any]]) -> None:
    keys: List[Tuple[datetime, str, str]] = [
        (
            _parse_timestamp(item.get("timestamp"), "timestamp"),
            str(item.get("ip_address", "")),
            str(item.get("run_id", "")),
        )
        for item in records
    ]
    if keys != sorted(keys):
        _raise("evidence records are not deterministically ordered")
