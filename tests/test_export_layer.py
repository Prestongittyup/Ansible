"""Validation tests for Sprint 9 derived read-only export layer."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from legacy.api_layer.api_server import create_app
from legacy.export.metrics_exporter import compute_metrics, export_metrics_from_file
from legacy.export.otel_mapper import map_events_to_otel
from legacy.export.siem_exporter import export_siem_from_file, format_for_siem
from legacy.export.trace_exporter import export_replay_from_file, export_replay_sequences, normalize_events
from test_api_layer import SpyQueryService


class ExportLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp_dir.name) / "audit_events.jsonl"

        self.events = [
            {
                "event_id": "evt-003",
                "timestamp": "2026-05-01T22:10:03+00:00",
                "event_type": "AUTHZ_DECISION",
                "request_id": "req-001",
                "actor_role": "reader",
                "endpoint": "/evidence",
                "decision": "PASS",
                "reason_code": "AUTHZ_OK",
                "schema_version": "2.1",
                "latency_ms": 1.1,
                "trace_hash": "t3",
            },
            {
                "event_id": "evt-001",
                "timestamp": "2026-05-01T22:10:01+00:00",
                "event_type": "API_REQUEST",
                "request_id": "req-001",
                "actor_role": "reader",
                "endpoint": "/evidence",
                "decision": "PASS",
                "reason_code": "API_OK",
                "schema_version": "2.1",
                "latency_ms": 25.0,
                "trace_hash": "t1",
            },
            {
                "event_id": "evt-004",
                "timestamp": "2026-05-01T22:10:04+00:00",
                "event_type": "GOVERNANCE_DECISION",
                "request_id": "req-001",
                "actor_role": "reader",
                "endpoint": "/evidence",
                "decision": "PASS",
                "reason_code": "GOV_ALLOW",
                "schema_version": "2.1",
                "latency_ms": 1.0,
                "trace_hash": "t4",
            },
            {
                "event_id": "evt-002",
                "timestamp": "2026-05-01T22:10:02+00:00",
                "event_type": "AUTH_DECISION",
                "request_id": "req-001",
                "actor_role": "reader",
                "endpoint": "/evidence",
                "decision": "PASS",
                "reason_code": "AUTH_OK",
                "schema_version": "2.1",
                "latency_ms": 1.2,
                "trace_hash": "t2",
            },
            {
                "event_id": "evt-005",
                "timestamp": "2026-05-01T22:10:05+00:00",
                "event_type": "QUERY_EXECUTION",
                "request_id": "req-001",
                "actor_role": "reader",
                "endpoint": "get_validation_evidence",
                "decision": "PASS",
                "reason_code": "QUERY_OK",
                "schema_version": "2.1",
                "latency_ms": 8.0,
                "trace_hash": "t5",
            },
            {
                "event_id": "evt-006",
                "timestamp": "2026-05-01T22:11:01+00:00",
                "event_type": "API_REQUEST",
                "request_id": "req-002",
                "actor_role": "restricted",
                "endpoint": "/evidence",
                "decision": "BLOCKED",
                "reason_code": "HTTP_429",
                "schema_version": "2.1",
                "latency_ms": 11.0,
                "trace_hash": "t6",
            },
            {
                "event_id": "evt-007",
                "timestamp": "2026-05-01T22:11:02+00:00",
                "event_type": "AUTH_DECISION",
                "request_id": "req-002",
                "actor_role": "restricted",
                "endpoint": "/evidence",
                "decision": "FAIL",
                "reason_code": "AUTH_INVALID_TOKEN",
                "schema_version": "2.1",
                "latency_ms": 2.5,
                "trace_hash": "t7",
            },
            {
                "event_id": "evt-008",
                "timestamp": "2026-05-01T22:11:03+00:00",
                "event_type": "GOVERNANCE_DECISION",
                "request_id": "req-002",
                "actor_role": "restricted",
                "endpoint": "/evidence",
                "decision": "BLOCKED",
                "reason_code": "GOV_RATE_LIMIT_EXCEEDED",
                "schema_version": "2.1",
                "latency_ms": 3.0,
                "trace_hash": "t8",
            },
            {
                "event_id": "evt-009",
                "timestamp": "2026-05-01T22:12:01+00:00",
                "event_type": "EMV_RUN",
                "request_id": "sys-emv",
                "actor_role": "system",
                "endpoint": "ci/meta/ci_enforcement_meta_validator.py",
                "decision": "PASS",
                "reason_code": "EMV_OK",
                "schema_version": "2.1",
                "latency_ms": 140.0,
                "trace_hash": "t9",
            },
            {
                "event_id": "evt-010",
                "timestamp": "2026-05-01T22:12:02+00:00",
                "event_type": "CI_KERNEL_RUN",
                "request_id": "sys-ci",
                "actor_role": "system",
                "endpoint": "ci/run_ci_kernel.py",
                "decision": "PASS",
                "reason_code": "CI_KERNEL_OK",
                "schema_version": "2.1",
                "latency_ms": 210.0,
                "trace_hash": "t10",
            },
            {
                "event_id": "evt-011",
                "timestamp": "2026-05-01T22:12:03+00:00",
                "event_type": "INGESTION_EVENT",
                "request_id": "ing-001",
                "actor_role": "system",
                "endpoint": "persistence.run_ingestion",
                "decision": "FAIL",
                "reason_code": "INGEST_FAIL_STATUS",
                "schema_version": "2.1",
                "latency_ms": 19.0,
                "trace_hash": "t11",
            },
        ]

        self.audit_path.write_text("\n".join(json.dumps(event, sort_keys=True) for event in self.events) + "\n", encoding="utf-8")

        self.service = SpyQueryService()
        self.client = TestClient(create_app(query_service=self.service))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _auth() -> dict[str, str]:
        return {"Authorization": "Bearer tk_reader_reader_full"}

    def test_no_system_behavior_modification(self) -> None:
        first = self.client.get(
            "/devices",
            params={"vendor": "aruba", "limit": 10, "offset": 0},
            headers=self._auth(),
        )
        self.assertEqual(200, first.status_code)

        export_replay_from_file(self.audit_path)
        export_metrics_from_file(str(self.audit_path))
        export_siem_from_file(str(self.audit_path), target="splunk")

        second = self.client.get(
            "/devices",
            params={"vendor": "aruba", "limit": 10, "offset": 0},
            headers=self._auth(),
        )
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.json(), second.json())

    def test_deterministic_export_output(self) -> None:
        replay_one = export_replay_sequences(self.events)
        replay_two = export_replay_sequences(self.events)
        self.assertEqual(replay_one, replay_two)

        metrics_one = compute_metrics(self.events)
        metrics_two = compute_metrics(self.events)
        self.assertEqual(metrics_one, metrics_two)

        siem_one = format_for_siem(self.events, target="elastic")
        siem_two = format_for_siem(self.events, target="elastic")
        self.assertEqual(siem_one, siem_two)

    def test_trace_reconstruction_correctness(self) -> None:
        traces = export_replay_sequences(self.events)
        req_trace = next(trace for trace in traces if trace["request_id"] == "req-001")
        event_types = [event["event_type"] for event in req_trace["replay_sequence"]]

        self.assertEqual(
            ["API_REQUEST", "AUTH_DECISION", "AUTHZ_DECISION", "GOVERNANCE_DECISION", "QUERY_EXECUTION"],
            event_types,
        )
        self.assertEqual(5, req_trace["event_count"])

        otel = map_events_to_otel(self.events)
        spans = otel["spans"]
        api_span = next(span for span in spans if span["name"].startswith("api:/evidence"))
        self.assertEqual("PASS", api_span["attributes"]["decision"])
        span_event_names = [item["name"] for item in api_span["events"]]
        self.assertIn("AUTH_DECISION", span_event_names)
        self.assertIn("AUTHZ_DECISION", span_event_names)

    def test_schema_compliance_enforced(self) -> None:
        mixed = list(self.events) + [
            {
                "event_id": "bad-001",
                "timestamp": "2026-05-01T22:13:00+00:00",
                "event_type": "API_REQUEST",
                "request_id": "req-bad",
                "actor_role": "reader",
                "endpoint": "/devices",
                "decision": "PASS",
                "reason_code": "API_OK",
                "schema_version": "1.0",
                "latency_ms": 1.0,
                "trace_hash": "tb",
            }
        ]

        normalized = normalize_events(mixed)
        self.assertTrue(all(event["schema_version"] == "2.1" for event in normalized))

        replay = export_replay_sequences(mixed)
        for trace in replay:
            self.assertEqual("2.1", trace["schema_version"])

        siem = format_for_siem(mixed, target="splunk")
        for row in siem:
            self.assertEqual("2.1", row["event"]["schema_version"])

        metrics = compute_metrics(mixed)
        self.assertEqual("2.1", metrics["schema_version"])

    def test_zero_performance_impact_core_paths(self) -> None:
        before_start = time.perf_counter()
        before_response = self.client.get(
            "/device/10.0.0.1",
            headers=self._auth(),
        )
        before_elapsed = (time.perf_counter() - before_start) * 1000.0
        self.assertEqual(200, before_response.status_code)

        for _ in range(200):
            export_replay_sequences(self.events)
            compute_metrics(self.events)
            format_for_siem(self.events, target="datadog")

        after_start = time.perf_counter()
        after_response = self.client.get(
            "/device/10.0.0.1",
            headers=self._auth(),
        )
        after_elapsed = (time.perf_counter() - after_start) * 1000.0
        self.assertEqual(200, after_response.status_code)

        self.assertLess(after_elapsed, before_elapsed + 1000.0)

    def test_failure_isolation_export_layer(self) -> None:
        missing_path = Path(self.temp_dir.name) / "missing.jsonl"

        replay = export_replay_from_file(missing_path)
        metrics = export_metrics_from_file(str(missing_path))
        siem = export_siem_from_file(str(missing_path), target="splunk")

        self.assertEqual([], replay)
        self.assertEqual(0, metrics["request_volume"])
        self.assertEqual([], siem)

        response = self.client.get(
            "/devices",
            params={"vendor": "aruba", "limit": 10, "offset": 0},
            headers=self._auth(),
        )
        self.assertEqual(200, response.status_code)


if __name__ == "__main__":
    unittest.main()
