"""Validation tests for Sprint 8 non-invasive observability layer."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from legacy.api_layer.api_server import create_app
from observability.audit_logger import AuditLogger
from observability.decision_recorder import DecisionRecorder
from observability.event_collector import EventCollector
from observability.trace_context import correlation_snapshot, reset_correlation_map
from legacy.persistence import ingest_validation
from test_api_layer import SpyQueryService


class _FailingAuditLogger(AuditLogger):
    def append_event(self, event):  # type: ignore[override]
        raise RuntimeError("forced logger failure")


class ObservabilityLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp_dir.name) / "audit_events.jsonl"
        self.audit_logger = AuditLogger(file_path=self.audit_path)
        self.collector = EventCollector(audit_logger=self.audit_logger)
        self.recorder = DecisionRecorder(collector=self.collector)

        self.service = SpyQueryService()
        observed_service = self.recorder.wrap_query_service(self.service)
        app = create_app(query_service=observed_service)
        self.recorder.install_api_hooks(app)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _auth(self) -> dict[str, str]:
        return {"Authorization": "Bearer tk_reader_reader_full"}

    def test_event_capture_for_each_layer(self) -> None:
        response = self.client.get(
            "/evidence",
            params={"ip_address": "10.0.0.1", "limit": 2, "offset": 0},
            headers=self._auth(),
        )
        self.assertEqual(200, response.status_code)

        self.assertTrue(self.recorder.install_ingestion_hook(ingest_validation))
        ingest_validation.run_ingestion(
            input_path=Path("ansible_validation/outputs/validation_results.json"),
            schema_sql_path=Path("persistence/schema.sql"),
            database_url="",
            run_id="obs-ingest",
        )

        emv_code, emv_payload = self.recorder.run_emv_with_hook()
        ci_code, ci_payload = self.recorder.run_ci_kernel_with_hook()

        self.assertEqual(0, emv_code)
        self.assertEqual("PASS", str(emv_payload.get("emv_status", "")))
        self.assertEqual(0, ci_code)
        self.assertEqual("PASS", str(ci_payload.get("ci_status", "")))

        events = self.audit_logger.read_events()
        event_types = {event["event_type"] for event in events}

        self.assertIn("API_REQUEST", event_types)
        self.assertIn("AUTH_DECISION", event_types)
        self.assertIn("AUTHZ_DECISION", event_types)
        self.assertIn("GOVERNANCE_DECISION", event_types)
        self.assertIn("QUERY_EXECUTION", event_types)
        self.assertIn("INGESTION_EVENT", event_types)
        self.assertIn("EMV_RUN", event_types)
        self.assertIn("CI_KERNEL_RUN", event_types)

    def test_deterministic_trace_reconstruction(self) -> None:
        reset_correlation_map()

        first = self.client.get(
            "/runs/run-002",
            params={"limit": 10, "offset": 0},
            headers=self._auth(),
        )
        second = self.client.get(
            "/runs/run-002",
            params={"limit": 10, "offset": 0},
            headers=self._auth(),
        )

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)

        events = self.audit_logger.read_events()
        api_events = [event for event in events if event["event_type"] == "API_REQUEST"]
        self.assertGreaterEqual(len(api_events), 2)

        request_ids = {event["request_id"] for event in api_events}
        self.assertEqual(1, len(request_ids))

        trace_hashes = {event["trace_hash"] for event in api_events}
        self.assertEqual(1, len(trace_hashes))

        snapshot = correlation_snapshot()
        request_id = next(iter(request_ids))
        self.assertIn(request_id, snapshot)
        self.assertGreater(len(snapshot[request_id]), 0)

    def test_schema_validation_enforcement_drops_malformed_events(self) -> None:
        malformed = {
            "event_type": "API_REQUEST",
            "schema_version": "1.0",
        }
        accepted = self.collector.emit(malformed)

        self.assertFalse(accepted)
        stats = self.collector.stats()
        self.assertEqual(1, stats["dropped"])
        self.assertEqual(0, stats["emitted"])
        self.assertEqual([], self.audit_logger.read_events())

    def test_no_performance_blocking_behavior(self) -> None:
        started = time.perf_counter()
        response = self.client.get(
            "/device/10.0.0.1",
            headers=self._auth(),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        self.assertEqual(200, response.status_code)
        self.assertLess(elapsed_ms, 1000.0)

    def test_failure_isolation_observability_cannot_break_system(self) -> None:
        failing_collector = EventCollector(audit_logger=_FailingAuditLogger(file_path=Path(self.temp_dir.name) / "failing.jsonl"))
        failing_recorder = DecisionRecorder(collector=failing_collector)

        isolated_service = SpyQueryService()
        isolated_observed_service = failing_recorder.wrap_query_service(isolated_service)
        isolated_app = create_app(query_service=isolated_observed_service)
        failing_recorder.install_api_hooks(isolated_app)
        isolated_client = TestClient(isolated_app)

        response = isolated_client.get(
            "/devices",
            params={"vendor": "aruba", "limit": 10, "offset": 0},
            headers=self._auth(),
        )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIn("data", body)


if __name__ == "__main__":
    unittest.main()
