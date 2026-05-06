"""Tests for Sprint 7 governance layer integration at API boundary."""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from legacy.api_layer.api_server import create_app
from legacy.governance_layer.query_bounds_validator import QueryBoundsValidator
from legacy.governance_layer.quota_manager import QuotaManager, RoleQuota
from legacy.governance_layer.rate_limiter import RateLimiter
from legacy.governance_layer.request_policy_engine import RequestPolicyEngine
from test_api_layer import SpyQueryService


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakeClock:
    def __init__(self) -> None:
        self.current = 1_000_000.0

    def now(self) -> float:
        return self.current


class GovernanceLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SpyQueryService()

    def _build_client(self, engine: RequestPolicyEngine | None = None) -> TestClient:
        app = create_app(query_service=self.service, policy_engine=engine)
        return TestClient(app)

    def _low_rate_engine(self) -> RequestPolicyEngine:
        clock = _FakeClock()
        quotas = {
            "admin": RoleQuota(
                name="admin_high_quota",
                role="admin",
                per_minute_limit=100,
                per_hour_limit=1000,
                endpoint_per_minute={"/evidence": 100},
                endpoint_per_hour={"/evidence": 1000},
                max_result_limit=200,
                max_offset=1000,
                max_time_range_minutes=1440,
            ),
            "reader": RoleQuota(
                name="reader_low_rate",
                role="reader",
                per_minute_limit=2,
                per_hour_limit=10,
                endpoint_per_minute={"/evidence": 2, "/devices": 2, "/runs/{run_id}": 2, "/device/{ip_address}": 2},
                endpoint_per_hour={"/evidence": 10, "/devices": 10, "/runs/{run_id}": 10, "/device/{ip_address}": 10},
                max_result_limit=200,
                max_offset=1000,
                max_time_range_minutes=720,
            ),
            "restricted": RoleQuota(
                name="restricted_low_quota",
                role="restricted",
                per_minute_limit=2,
                per_hour_limit=10,
                endpoint_per_minute={"/evidence": 2, "/runs/{run_id}": 2, "/device/{ip_address}": 2},
                endpoint_per_hour={"/evidence": 10, "/runs/{run_id}": 10, "/device/{ip_address}": 10},
                max_result_limit=50,
                max_offset=200,
                max_time_range_minutes=60,
            ),
        }
        return RequestPolicyEngine(
            rate_limiter=RateLimiter(time_source=clock.now),
            quota_manager=QuotaManager(quotas=quotas),
            bounds_validator=QueryBoundsValidator(default_max_limit=100, default_max_offset=1000),
        )

    def _quota_engine(self) -> RequestPolicyEngine:
        quotas = {
            "admin": RoleQuota(
                name="admin_high_quota",
                role="admin",
                per_minute_limit=200,
                per_hour_limit=2000,
                endpoint_per_minute={"/evidence": 150},
                endpoint_per_hour={"/evidence": 1500},
                max_result_limit=300,
                max_offset=5000,
                max_time_range_minutes=1440,
            ),
            "reader": RoleQuota(
                name="reader_medium_quota",
                role="reader",
                per_minute_limit=100,
                per_hour_limit=1000,
                endpoint_per_minute={"/evidence": 80},
                endpoint_per_hour={"/evidence": 800},
                max_result_limit=200,
                max_offset=2000,
                max_time_range_minutes=720,
            ),
            "restricted": RoleQuota(
                name="restricted_quota_test",
                role="restricted",
                per_minute_limit=30,
                per_hour_limit=300,
                endpoint_per_minute={"/evidence": 20, "/runs/{run_id}": 20, "/device/{ip_address}": 20},
                endpoint_per_hour={"/evidence": 160, "/runs/{run_id}": 160, "/device/{ip_address}": 160},
                max_result_limit=50,
                max_offset=200,
                max_time_range_minutes=60,
            ),
        }
        return RequestPolicyEngine(
            quota_manager=QuotaManager(quotas=quotas),
            bounds_validator=QueryBoundsValidator(default_max_limit=500, default_max_offset=5000),
        )

    def test_valid_request_under_limit_passes(self) -> None:
        client = self._build_client()
        response = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("reader", body["access_context"]["role"])

    def test_exceeding_rate_limit_fails_closed(self) -> None:
        engine = self._low_rate_engine()
        client = self._build_client(engine=engine)

        first = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )
        second = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )
        third = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(429, third.status_code)
        self.assertEqual("GOV_RATE_LIMIT_EXCEEDED", third.json()["data"]["error"]["code"])

    def test_exceeding_quota_fails_closed(self) -> None:
        client = self._build_client(engine=self._quota_engine())
        response = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 60, "offset": 0},
            headers=_auth("tk_restricted_restricted_scope"),
        )

        self.assertEqual(429, response.status_code)
        self.assertIn(response.json()["data"]["error"]["code"], ("GOV_QUOTA_EXCEEDED", "GOV_BOUNDS_EXCEEDED"))

    def test_excessive_query_limit_fails_closed(self) -> None:
        client = self._build_client()
        response = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 101, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(429, response.status_code)
        self.assertEqual("GOV_BOUNDS_EXCEEDED", response.json()["data"]["error"]["code"])

    def test_repeated_identical_requests_deterministic(self) -> None:
        client = self._build_client()
        first = client.get(
            "/runs/run-002",
            params={"limit": 10, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )
        second = client.get(
            "/runs/run-002",
            params={"limit": 10, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.json(), second.json())

    def test_auth_and_governance_combined_enforcement(self) -> None:
        client = self._build_client()

        missing_token = client.get("/devices", params={"vendor": "aruba", "limit": 10, "offset": 0})
        governance_block = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 101, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(401, missing_token.status_code)
        self.assertEqual("AUTH", missing_token.json()["data"]["error"]["type"])
        self.assertEqual(429, governance_block.status_code)
        self.assertEqual("AUTHZ", governance_block.json()["data"]["error"]["type"])

    def test_no_query_service_execution_on_blocked_requests(self) -> None:
        client = self._build_client()
        response = client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 101, "offset": 0},
            headers=_auth("tk_reader_reader_full"),
        )

        self.assertEqual(429, response.status_code)
        self.assertEqual(0, self.service.calls["get_validation_evidence"])
        self.assertEqual(0, self.service.calls["get_evidence_by_run"])
        self.assertEqual(0, self.service.calls["get_evidence_by_time_range"])
        self.assertEqual(0, self.service.calls["get_device"])
        self.assertEqual(0, self.service.calls["list_devices_by_vendor"])
        self.assertEqual(0, self.service.calls["list_devices_by_execution_state"])

    def test_no_db_access_in_governance_layer(self) -> None:
        root = Path("governance_layer")
        forbidden_markers = (
            "psycopg",
            "SELECT ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "query_layer.query_client",
            "query_layer.query_service",
        )

        for file_path in root.rglob("*.py"):
            text = file_path.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                self.assertNotIn(marker, text, f"forbidden marker {marker} in {file_path}")


if __name__ == "__main__":
    unittest.main()
