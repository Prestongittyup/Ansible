"""Strict output contracts for Sprint 4 read-only query responses."""

from __future__ import annotations

from typing import Any, Dict, TypedDict

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

DEVICE_KEYS = frozenset({"ip_address", "vendor", "last_seen"})
DEVICE_STATE_KEYS = frozenset({"ip_address", "vendor", "execution_state", "last_updated"})
VALIDATION_STATE_KEYS = frozenset(
    {
        "ip_address",
        "execution_state",
        "connectivity_status",
        "validation_status",
        "facts_collected",
        "attempt_count",
        "last_updated",
    }
)
EVIDENCE_KEYS = frozenset({"ip_address", "run_id", "timestamp", "execution_state", "raw_payload"})


class DeviceRecord(TypedDict):
    ip_address: str
    vendor: str
    last_seen: str


class DeviceStateRecord(TypedDict):
    ip_address: str
    vendor: str
    execution_state: str
    last_updated: str


class ValidationStateRecord(TypedDict):
    ip_address: str
    execution_state: str
    connectivity_status: str
    validation_status: str
    facts_collected: Dict[str, Any] | None
    attempt_count: int
    last_updated: str


class EvidenceRecord(TypedDict):
    ip_address: str
    run_id: str
    timestamp: str
    execution_state: str
    raw_payload: Dict[str, Any]
