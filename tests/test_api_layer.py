"""Tests for Sprint 5 read-only API access layer."""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from legacy.api_layer.api_server import create_app


def _auth(token: str = "tk_reader_reader_full") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class SpyQueryService:
    def __init__(self) -> None:
        self.calls = {
            "get_device": 0,
            "list_devices_by_vendor": 0,
            "list_devices_by_execution_state": 0,
            "get_validation_evidence": 0,
            "get_evidence_by_run": 0,
            "get_evidence_by_time_range": 0,
        }
        self._devices = {
            "10.0.0.1": {
                "ip_address": "10.0.0.1",
                "vendor": "aruba",
                "last_seen": "2026-05-01T10:00:00+00:00",
            },
            "10.0.0.2": {
                "ip_address": "10.0.0.2",
                "vendor": "meraki",
                "last_seen": "2026-05-01T10:01:00+00:00",
            },
            "10.0.0.3": {
                "ip_address": "10.0.0.3",
                "vendor": "aruba",
                "last_seen": "2026-05-01T10:02:00+00:00",
            },
        }
        self._device_states = [
            {
                "ip_address": "10.0.0.2",
                "vendor": "meraki",
                "execution_state": "FAIL_RUNTIME",
                "last_updated": "2026-05-01T11:00:00+00:00",
            },
            {
                "ip_address": "10.0.0.3",
                "vendor": "aruba",
                "execution_state": "FAIL_RUNTIME",
                "last_updated": "2026-05-01T11:01:00+00:00",
            },
        ]
        self._evidence = [
            {
                "ip_address": "10.0.0.1",
                "run_id": "run-001",
                "timestamp": "2026-05-01T12:00:00+00:00",
                "execution_state": "SUCCESS",
                "raw_payload": {"ip_address": "10.0.0.1", "schema_version": "2.1"},
            },
            {
                "ip_address": "10.0.0.1",
                "run_id": "run-002",
                "timestamp": "2026-05-01T12:05:00+00:00",
                "execution_state": "SUCCESS",
                "raw_payload": {"ip_address": "10.0.0.1", "schema_version": "2.1"},
            },
            {
                "ip_address": "10.0.0.2",
                "run_id": "run-002",
                "timestamp": "2026-05-01T12:06:00+00:00",
                "execution_state": "FAIL_RUNTIME",
                "raw_payload": {"ip_address": "10.0.0.2", "schema_version": "2.1"},
            },
        ]

    def snapshot(self):
        return {
            "devices": copy.deepcopy(self._devices),
            "device_states": copy.deepcopy(self._device_states),
            "evidence": copy.deepcopy(self._evidence),
        }

    def get_device(self, ip_address: str):
        self.calls["get_device"] += 1
        row = self._devices.get(ip_address)
        return copy.deepcopy(row) if row is not None else None

    def list_devices_by_vendor(self, vendor: str):
        self.calls["list_devices_by_vendor"] += 1
        rows = [copy.deepcopy(row) for row in self._devices.values() if row["vendor"] == vendor]
        rows.sort(key=lambda item: item["ip_address"])
        return rows

    def list_devices_by_execution_state(self, execution_state: str):
        self.calls["list_devices_by_execution_state"] += 1
        rows = [copy.deepcopy(row) for row in self._device_states if row["execution_state"] == execution_state]
        rows.sort(key=lambda item: item["ip_address"])
        return rows

    def get_validation_evidence(self, ip_address: str, limit: int = 100, offset: int = 0):
        self.calls["get_validation_evidence"] += 1
        rows = [copy.deepcopy(row) for row in self._evidence if row["ip_address"] == ip_address]
        rows.sort(key=lambda item: (item["timestamp"], item["run_id"]))
        return rows[offset : offset + limit]

    def get_evidence_by_run(self, run_id: str, limit: int = 100, offset: int = 0):
        self.calls["get_evidence_by_run"] += 1
        rows = [copy.deepcopy(row) for row in self._evidence if row["run_id"] == run_id]
        rows.sort(key=lambda item: (item["timestamp"], item["ip_address"]))
        return rows[offset : offset + limit]

    def get_evidence_by_time_range(self, start_timestamp: str, end_timestamp: str, limit: int = 100, offset: int = 0):
        self.calls["get_evidence_by_time_range"] += 1
        start = datetime.fromisoformat(start_timestamp)
        end = datetime.fromisoformat(end_timestamp)
        rows = [
            copy.deepcopy(row)
            for row in self._evidence
            if datetime.fromisoformat(row["timestamp"]) >= start and datetime.fromisoformat(row["timestamp"]) <= end
        ]
        rows.sort(key=lambda item: (item["timestamp"], item["ip_address"], item["run_id"]))
        return rows[offset : offset + limit]


class ApiLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SpyQueryService()
        self.client = TestClient(create_app(query_service=self.service))

    def test_valid_request_returns_deterministic_output(self) -> None:
        first = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth())
        second = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth())

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.json(), second.json())
        self.assertIn("access_context", first.json())

    def test_invalid_query_params_fail_closed(self) -> None:
        bad_limit = self.client.get(
            "/devices",
            params={"vendor": "aruba", "limit": 0, "offset": 0},
            headers=_auth(),
        )
        bad_enum = self.client.get("/devices", params={"execution_state": "BROKEN"}, headers=_auth())

        self.assertIn(bad_limit.status_code, (400, 403, 429))
        self.assertEqual(400, bad_enum.status_code)

    def test_missing_parameters_fail_closed(self) -> None:
        missing_filter = self.client.get("/devices", headers=_auth())
        missing_evidence_selector = self.client.get("/evidence", headers=_auth())

        self.assertIn(missing_filter.status_code, (400, 403))
        self.assertIn(missing_evidence_selector.status_code, (400, 403))

    def test_repeated_request_identical_output(self) -> None:
        first = self.client.get("/runs/run-002", params={"limit": 50, "offset": 0}, headers=_auth())
        second = self.client.get("/runs/run-002", params={"limit": 50, "offset": 0}, headers=_auth())

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.json(), second.json())

    def test_large_limit_boundary_handling(self) -> None:
        allowed = self.client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 100, "offset": 0},
            headers=_auth(),
        )
        blocked = self.client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 101, "offset": 0},
            headers=_auth(),
        )

        self.assertEqual(200, allowed.status_code)
        self.assertIn(blocked.status_code, (403, 429))

    def test_api_does_not_mutate_database(self) -> None:
        before = self.service.snapshot()
        self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth())
        self.client.get("/runs/run-002", params={"limit": 10, "offset": 0}, headers=_auth())
        self.client.get(
            "/evidence",
            params={
                "start_timestamp": "2026-05-01T12:00:00+00:00",
                "end_timestamp": "2026-05-01T12:06:00+00:00",
                "limit": 10,
                "offset": 0,
            },
            headers=_auth(),
        )
        after = self.service.snapshot()

        self.assertEqual(before, after)

    def test_api_does_not_bypass_query_layer(self) -> None:
        self.client.get("/device/10.0.0.1", headers=_auth())
        self.assertEqual(1, self.service.calls["get_device"])
        self.assertEqual(0, self.service.calls["list_devices_by_vendor"])
        self.assertEqual(0, self.service.calls["list_devices_by_execution_state"])
        self.assertEqual(0, self.service.calls["get_validation_evidence"])
        self.assertEqual(0, self.service.calls["get_evidence_by_run"])
        self.assertEqual(0, self.service.calls["get_evidence_by_time_range"])


if __name__ == "__main__":
    unittest.main()
