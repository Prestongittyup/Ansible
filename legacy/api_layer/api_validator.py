"""Fail-closed API boundary validation for requests and responses."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Sequence, Tuple, cast

from legacy.api_layer.api_contracts import API_SCHEMA_VERSION
from legacy.query_layer.query_validator import (
    QueryValidationError,
    assert_ordered_by_ip,
    assert_ordered_evidence,
    validate_device_record,
    validate_device_state_record,
    validate_evidence_record,
    validate_execution_state,
    validate_ip_address,
    validate_pagination,
    validate_run_id,
    validate_time_range,
)


class ApiValidationError(RuntimeError):
    """Raised when API request or response violates boundary contract."""


def _raise(message: str) -> None:
    raise ApiValidationError(message)


def _raise_from_query_error(exc: QueryValidationError) -> None:
    raise ApiValidationError(str(exc)) from exc


def validate_devices_request(
    vendor: str | None,
    execution_state: str | None,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    try:
        page_limit, page_offset = validate_pagination(limit, offset)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)

    has_vendor = vendor is not None and vendor.strip() != ""
    has_state = execution_state is not None and execution_state.strip() != ""
    if has_vendor == has_state:
        _raise("exactly one of vendor or execution_state must be provided")

    if has_vendor:
        from legacy.query_layer.query_validator import validate_vendor

        try:
            normalized_vendor = validate_vendor(vendor)
        except QueryValidationError as exc:
            _raise_from_query_error(exc)
        return {
            "mode": "vendor",
            "vendor": normalized_vendor,
            "limit": page_limit,
            "offset": page_offset,
        }

    try:
        normalized_state = validate_execution_state(execution_state)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)
    return {
        "mode": "execution_state",
        "execution_state": normalized_state,
        "limit": page_limit,
        "offset": page_offset,
    }


def validate_device_request(ip_address: str) -> str:
    try:
        return validate_ip_address(ip_address)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)


def validate_evidence_request(
    ip_address: str | None,
    start_timestamp: str | None,
    end_timestamp: str | None,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    try:
        page_limit, page_offset = validate_pagination(limit, offset)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)

    has_ip = ip_address is not None and ip_address.strip() != ""
    has_start = start_timestamp is not None and start_timestamp.strip() != ""
    has_end = end_timestamp is not None and end_timestamp.strip() != ""

    if has_ip:
        if has_start or has_end:
            _raise("ip_address cannot be combined with start_timestamp/end_timestamp")
        try:
            normalized_ip = validate_ip_address(ip_address)
        except QueryValidationError as exc:
            _raise_from_query_error(exc)
        return {
            "mode": "ip",
            "ip_address": normalized_ip,
            "limit": page_limit,
            "offset": page_offset,
        }

    if has_start != has_end:
        _raise("start_timestamp and end_timestamp must both be provided")

    if not has_start and not has_end:
        _raise("either ip_address or (start_timestamp and end_timestamp) is required")

    try:
        normalized_start, normalized_end = validate_time_range(start_timestamp, end_timestamp)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)
    return {
        "mode": "time_range",
        "start_timestamp": normalized_start,
        "end_timestamp": normalized_end,
        "limit": page_limit,
        "offset": page_offset,
    }


def validate_run_request(run_id: str, limit: int, offset: int) -> Dict[str, Any]:
    try:
        page_limit, page_offset = validate_pagination(limit, offset)
        normalized_run_id = validate_run_id(run_id)
    except QueryValidationError as exc:
        _raise_from_query_error(exc)
    return {
        "run_id": normalized_run_id,
        "limit": page_limit,
        "offset": page_offset,
    }


def validate_devices_response(
    records: Sequence[Dict[str, Any]],
    mode: Literal["vendor", "execution_state"],
) -> List[Dict[str, Any]]:
    try:
        validated: List[Dict[str, Any]] = []
        if mode == "vendor":
            for record in records:
                validated.append(cast(Dict[str, Any], validate_device_record(dict(record))))
            assert_ordered_by_ip(validated)
            return validated

        for record in records:
            validated.append(cast(Dict[str, Any], validate_device_state_record(dict(record))))
        assert_ordered_by_ip(validated)
        return validated
    except QueryValidationError as exc:
        _raise_from_query_error(exc)


def validate_device_response(record: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if record is None:
        return None
    try:
        return cast(Dict[str, Any], validate_device_record(dict(record)))
    except QueryValidationError as exc:
        _raise_from_query_error(exc)


def validate_evidence_response(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        validated: List[Dict[str, Any]] = []
        for record in records:
            validated.append(cast(Dict[str, Any], validate_evidence_record(dict(record))))
        assert_ordered_evidence(validated)
        return validated
    except QueryValidationError as exc:
        _raise_from_query_error(exc)


def validate_response_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = {"request_id", "timestamp", "schema_version", "access_context", "data"}
    actual_keys = set(envelope.keys())
    if actual_keys != required_keys:
        missing = sorted(required_keys - actual_keys)
        extra = sorted(actual_keys - required_keys)
        _raise(f"response envelope keys mismatch: missing={missing}, extra={extra}")

    request_id = envelope.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        _raise("response request_id must be a non-empty string")

    timestamp = envelope.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        _raise("response timestamp must be a non-empty string")

    schema_version = envelope.get("schema_version")
    if schema_version != API_SCHEMA_VERSION:
        _raise(f"response schema_version must be {API_SCHEMA_VERSION}")

    access_context = envelope.get("access_context")
    if not isinstance(access_context, dict):
        _raise("response access_context must be an object")

    required_context = {"user_id", "role", "policy_applied"}
    context_keys = set(access_context.keys())
    if context_keys != required_context:
        missing = sorted(required_context - context_keys)
        extra = sorted(context_keys - required_context)
        _raise(f"response access_context keys mismatch: missing={missing}, extra={extra}")

    for key in ("user_id", "role", "policy_applied"):
        value = access_context.get(key)
        if not isinstance(value, str) or not value.strip():
            _raise(f"response access_context.{key} must be a non-empty string")

    return envelope
