"""Read-only query orchestration layer for canonical persistence state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from legacy.query_layer.query_client import QueryClient
from legacy.query_layer.query_contracts import DeviceRecord, DeviceStateRecord, EvidenceRecord, ValidationStateRecord
from legacy.query_layer.query_validator import (
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
    validate_validation_state_record,
    validate_vendor,
)


class QueryService:
    """Service layer that validates query input/output without altering data semantics."""

    def __init__(self, query_client: QueryClient) -> None:
        self._query_client = query_client

    @staticmethod
    def _isoformat(value: Any) -> str:
        if isinstance(value, datetime):
            dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return str(value)

    def get_device(self, ip_address: str) -> DeviceRecord | None:
        ip = validate_ip_address(ip_address)
        row = self._query_client.get_device(ip)
        if row is None:
            return None

        contract = {
            "ip_address": str(row.get("ip_address", "")),
            "vendor": str(row.get("vendor", "")),
            "last_seen": self._isoformat(row.get("last_seen")),
        }
        return validate_device_record(contract)

    def list_devices_by_vendor(self, vendor: str) -> List[DeviceRecord]:
        normalized_vendor = validate_vendor(vendor)
        rows = self._query_client.list_devices_by_vendor(normalized_vendor)
        records: List[DeviceRecord] = []
        for row in rows:
            records.append(
                validate_device_record(
                    {
                        "ip_address": str(row.get("ip_address", "")),
                        "vendor": str(row.get("vendor", "")),
                        "last_seen": self._isoformat(row.get("last_seen")),
                    }
                )
            )
        assert_ordered_by_ip(records)
        return records

    def list_devices_by_execution_state(self, execution_state: str) -> List[DeviceStateRecord]:
        normalized_state = validate_execution_state(execution_state)
        rows = self._query_client.list_devices_by_execution_state(normalized_state)
        records: List[DeviceStateRecord] = []
        for row in rows:
            records.append(
                validate_device_state_record(
                    {
                        "ip_address": str(row.get("ip_address", "")),
                        "vendor": str(row.get("vendor", "")),
                        "execution_state": str(row.get("execution_state", "")),
                        "last_updated": self._isoformat(row.get("last_updated")),
                    }
                )
            )
        assert_ordered_by_ip(records)
        return records

    def get_latest_validation_state(self, ip_address: str) -> ValidationStateRecord | None:
        ip = validate_ip_address(ip_address)
        row = self._query_client.get_latest_validation_state(ip)
        if row is None:
            return None

        record = {
            "ip_address": str(row.get("ip_address", "")),
            "execution_state": str(row.get("execution_state", "")),
            "connectivity_status": str(row.get("connectivity_status", "")),
            "validation_status": str(row.get("validation_status", "")),
            "facts_collected": row.get("facts_collected"),
            "attempt_count": row.get("attempt_count"),
            "last_updated": self._isoformat(row.get("last_updated")),
        }
        return validate_validation_state_record(record)

    def get_validation_evidence(self, ip_address: str, limit: int = 100, offset: int = 0) -> List[EvidenceRecord]:
        ip = validate_ip_address(ip_address)
        page_limit, page_offset = validate_pagination(limit, offset)
        rows = self._query_client.get_validation_evidence(ip, page_limit, page_offset)

        records: List[EvidenceRecord] = []
        for row in rows:
            records.append(
                validate_evidence_record(
                    {
                        "ip_address": str(row.get("ip_address", "")),
                        "run_id": str(row.get("run_id", "")),
                        "timestamp": self._isoformat(row.get("timestamp")),
                        "execution_state": str(row.get("execution_state", "")),
                        "raw_payload": row.get("raw_payload"),
                    }
                )
            )

        assert_ordered_evidence(records)
        return records

    def get_evidence_by_run(self, run_id: str, limit: int = 100, offset: int = 0) -> List[EvidenceRecord]:
        normalized_run_id = validate_run_id(run_id)
        page_limit, page_offset = validate_pagination(limit, offset)
        rows = self._query_client.get_evidence_by_run(normalized_run_id, page_limit, page_offset)

        records: List[EvidenceRecord] = []
        for row in rows:
            records.append(
                validate_evidence_record(
                    {
                        "ip_address": str(row.get("ip_address", "")),
                        "run_id": str(row.get("run_id", "")),
                        "timestamp": self._isoformat(row.get("timestamp")),
                        "execution_state": str(row.get("execution_state", "")),
                        "raw_payload": row.get("raw_payload"),
                    }
                )
            )

        assert_ordered_evidence(records)
        return records

    def get_evidence_by_time_range(
        self,
        start_timestamp: str,
        end_timestamp: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[EvidenceRecord]:
        normalized_start, normalized_end = validate_time_range(start_timestamp, end_timestamp)
        page_limit, page_offset = validate_pagination(limit, offset)
        rows = self._query_client.get_evidence_by_time_range(
            normalized_start,
            normalized_end,
            page_limit,
            page_offset,
        )

        records: List[EvidenceRecord] = []
        for row in rows:
            records.append(
                validate_evidence_record(
                    {
                        "ip_address": str(row.get("ip_address", "")),
                        "run_id": str(row.get("run_id", "")),
                        "timestamp": self._isoformat(row.get("timestamp")),
                        "execution_state": str(row.get("execution_state", "")),
                        "raw_payload": row.get("raw_payload"),
                    }
                )
            )

        assert_ordered_evidence(records)
        return records
