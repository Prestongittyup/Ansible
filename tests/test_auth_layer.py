"""Tests for Sprint 6 auth layer integration at API boundary."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from legacy.api_layer.api_server import create_app
from test_api_layer import SpyQueryService


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class AuthLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SpyQueryService()
        self.client = TestClient(create_app(query_service=self.service))

    def test_valid_token_success(self) -> None:
        response = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth("tk_reader_reader_full"))
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIn("access_context", body)
        self.assertEqual("reader-user", body["access_context"]["user_id"])

    def test_missing_token_fail_closed(self) -> None:
        response = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0})
        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("AUTH", body["data"]["error"]["type"])

    def test_invalid_token_fail_closed(self) -> None:
        response = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth("not-a-token"))
        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("AUTH", body["data"]["error"]["type"])

    def test_unauthorized_role_fail_closed(self) -> None:
        response = self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth("tk_unknown_unknown_full"))
        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("AUTH", body["data"]["error"]["type"])

    def test_restricted_scope_enforcement(self) -> None:
        allowed_ip = self.client.get("/device/10.0.0.1", headers=_auth("tk_restricted_restricted_scope"))
        denied_ip = self.client.get("/device/10.0.0.2", headers=_auth("tk_restricted_restricted_scope"))
        allowed_run = self.client.get("/runs/run-002", headers=_auth("tk_restricted_restricted_scope"))
        denied_run = self.client.get("/runs/run-001", headers=_auth("tk_restricted_restricted_scope"))

        self.assertEqual(200, allowed_ip.status_code)
        self.assertEqual(403, denied_ip.status_code)
        self.assertEqual(200, allowed_run.status_code)
        self.assertEqual(403, denied_run.status_code)

    def test_repeated_request_deterministic_result(self) -> None:
        first = self.client.get("/evidence", params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0}, headers=_auth("tk_reader_reader_full"))
        second = self.client.get("/evidence", params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0}, headers=_auth("tk_reader_reader_full"))

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.json(), second.json())

    def test_no_query_layer_bypass(self) -> None:
        self.client.get("/device/10.0.0.1", headers=_auth("tk_reader_reader_full"))
        self.assertEqual(1, self.service.calls["get_device"])
        self.assertEqual(0, self.service.calls["list_devices_by_vendor"])
        self.assertEqual(0, self.service.calls["list_devices_by_execution_state"])
        self.assertEqual(0, self.service.calls["get_validation_evidence"])
        self.assertEqual(0, self.service.calls["get_evidence_by_run"])
        self.assertEqual(0, self.service.calls["get_evidence_by_time_range"])

    def test_no_db_writes(self) -> None:
        before = self.service.snapshot()
        self.client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0}, headers=_auth("tk_reader_reader_full"))
        self.client.get("/runs/run-002", params={"limit": 10, "offset": 0}, headers=_auth("tk_reader_reader_full"))
        after = self.service.snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
