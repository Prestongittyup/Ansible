"""Unit tests for Sprint 4 read-only query layer."""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from legacy.query_layer.query_service import QueryService
from legacy.query_layer.query_validator import QueryValidationError


class FakeQueryClient:
    def __init__(self) -> None:
        self.devices = [
            {"ip_address": "10.0.0.1", "vendor": "aruba", "last_seen": datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)},
            {"ip_address": "10.0.0.2", "vendor": "meraki", "last_seen": datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc)},
            {"ip_address": "10.0.0.3", "vendor": "aruba", "last_seen": datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc)},
        ]
        self.validation_state = [
            {
                "ip_address": "10.0.0.1",
                "execution_state": "SUCCESS",
                "connectivity_status": "REACHABLE",
                "validation_status": "PASSED",
                "facts_collected": {"hostname": "sw-1"},
                "attempt_count": 1,
                "last_updated": datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
            },
            {
                "ip_address": "10.0.0.2",
                "execution_state": "FAIL_RUNTIME",
                "connectivity_status": "NOT_EXECUTED",
                "validation_status": "NOT_EXECUTED",
                "facts_collected": None,
                "attempt_count": 0,
                "last_updated": datetime(2026, 5, 1, 11, 1, tzinfo=timezone.utc),
            },
            {
                "ip_address": "10.0.0.3",
                "execution_state": "FAIL_RUNTIME",
                "connectivity_status": "NOT_EXECUTED",
                "validation_status": "NOT_EXECUTED",
                "facts_collected": None,
                "attempt_count": 0,
                "last_updated": datetime(2026, 5, 1, 11, 2, tzinfo=timezone.utc),
            },
        ]
        self.evidence = [
            {
                "ip_address": "10.0.0.1",
                "run_id": "run-001",
                "timestamp": datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                "execution_state": "SUCCESS",
                "raw_payload": {"ip_address": "10.0.0.1", "schema_version": "2.1"},
            },
            {
                "ip_address": "10.0.0.1",
                "run_id": "run-002",
                "timestamp": datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
                "execution_state": "SUCCESS",
                "raw_payload": {"ip_address": "10.0.0.1", "schema_version": "2.1"},
            },
            {
                "ip_address": "10.0.0.2",
                "run_id": "run-002",
                "timestamp": datetime(2026, 5, 1, 12, 6, tzinfo=timezone.utc),
                "execution_state": "FAIL_RUNTIME",
                "raw_payload": {"ip_address": "10.0.0.2", "schema_version": "2.1"},
            },
        ]

    def get_device(self, ip_address: str):
        for device in self.devices:
            if device["ip_address"] == ip_address:
                return copy.deepcopy(device)
        return None

    def list_devices_by_vendor(self, vendor: str):
        rows = [copy.deepcopy(device) for device in self.devices if device["vendor"] == vendor]
        rows.sort(key=lambda item: item["ip_address"])
        return rows

    def list_devices_by_execution_state(self, execution_state: str):
        matches = [row for row in self.validation_state if row["execution_state"] == execution_state]
        rows = []
        for state_row in matches:
            device = self.get_device(state_row["ip_address"])
            if device is None:
                continue
            rows.append(
                {
                    "ip_address": device["ip_address"],
                    "vendor": device["vendor"],
                    "execution_state": state_row["execution_state"],
                    "last_updated": state_row["last_updated"],
                }
            )
        rows.sort(key=lambda item: item["ip_address"])
        return rows

    def get_latest_validation_state(self, ip_address: str):
        for row in self.validation_state:
            if row["ip_address"] == ip_address:
                return copy.deepcopy(row)
        return None

    def get_validation_evidence(self, ip_address: str, limit: int, offset: int):
        rows = [copy.deepcopy(row) for row in self.evidence if row["ip_address"] == ip_address]
        rows.sort(key=lambda item: (item["timestamp"], item["run_id"]))
        return rows[offset : offset + limit]

    def get_evidence_by_run(self, run_id: str, limit: int, offset: int):
        rows = [copy.deepcopy(row) for row in self.evidence if row["run_id"] == run_id]
        rows.sort(key=lambda item: (item["timestamp"], item["ip_address"]))
        return rows[offset : offset + limit]

    def get_evidence_by_time_range(self, start_timestamp: str, end_timestamp: str, limit: int, offset: int):
        start = datetime.fromisoformat(start_timestamp)
        end = datetime.fromisoformat(end_timestamp)
        rows = [
            copy.deepcopy(row)
            for row in self.evidence
            if row["timestamp"] >= start and row["timestamp"] <= end
        ]
        rows.sort(key=lambda item: (item["timestamp"], item["ip_address"], item["run_id"]))
        return rows[offset : offset + limit]


class QueryLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = QueryService(FakeQueryClient())

    def test_get_device_by_ip_valid(self) -> None:
        record = self.service.get_device("10.0.0.1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual("10.0.0.1", record["ip_address"])
        self.assertEqual("aruba", record["vendor"])

    def test_get_device_by_ip_non_existent(self) -> None:
        record = self.service.get_device("10.0.0.99")
        self.assertIsNone(record)

    def test_filter_devices_by_vendor(self) -> None:
        records = self.service.list_devices_by_vendor("aruba")
        self.assertEqual(2, len(records))
        self.assertEqual(["10.0.0.1", "10.0.0.3"], [row["ip_address"] for row in records])

    def test_filter_devices_by_execution_state(self) -> None:
        records = self.service.list_devices_by_execution_state("FAIL_RUNTIME")
        self.assertEqual(2, len(records))
        self.assertEqual(["10.0.0.2", "10.0.0.3"], [row["ip_address"] for row in records])

    def test_latest_validation_state_retrieval(self) -> None:
        state = self.service.get_latest_validation_state("10.0.0.1")
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual("SUCCESS", state["execution_state"])
        self.assertEqual("REACHABLE", state["connectivity_status"])

    def test_evidence_pagination_limit_offset(self) -> None:
        rows = self.service.get_validation_evidence("10.0.0.1", limit=1, offset=1)
        self.assertEqual(1, len(rows))
        self.assertEqual("run-002", rows[0]["run_id"])

    def test_deterministic_ordering_same_query_twice(self) -> None:
        first = self.service.get_evidence_by_run("run-002", limit=50, offset=0)
        second = self.service.get_evidence_by_run("run-002", limit=50, offset=0)
        self.assertEqual(first, second)

    def test_evidence_by_time_range(self) -> None:
        rows = self.service.get_evidence_by_time_range(
            "2026-05-01T12:04:00+00:00",
            "2026-05-01T12:06:00+00:00",
            limit=10,
            offset=0,
        )
        self.assertEqual(2, len(rows))
        self.assertEqual("run-002", rows[0]["run_id"])
        self.assertEqual("10.0.0.1", rows[0]["ip_address"])
        self.assertEqual("10.0.0.2", rows[1]["ip_address"])

    def test_invalid_query_input_fail_closed(self) -> None:
        with self.assertRaises(QueryValidationError):
            self.service.get_device("")

        with self.assertRaises(QueryValidationError):
            self.service.list_devices_by_vendor("bad-vendor")

        with self.assertRaises(QueryValidationError):
            self.service.get_validation_evidence("10.0.0.1", limit=0, offset=0)


if __name__ == "__main__":
    unittest.main()
