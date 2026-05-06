"""Sprint 11 fail-closed aggregation and failure model tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from legacy.compliance_layer.gate_sequencer import default_gate_definitions
from legacy.compliance_layer.unified_compliance_runner import UnifiedComplianceRunner


def _fixed_now() -> datetime:
    return datetime(2026, 5, 1, 23, 0, 0, tzinfo=timezone.utc)


def _pass_result(gate_name: str, layer: str, rule_id: str) -> dict:
    return {
        "layer": layer,
        "rule_id": rule_id,
        "status": "PASS",
        "violation_count": 0,
        "violations": [],
        "started_at": "2026-05-01T23:00:00+00:00",
        "finished_at": "2026-05-01T23:00:00+00:00",
        "command": ["mock", gate_name],
        "exit_code": 0,
        "output_hash": f"pass-{gate_name.lower()}",
        "stdout_excerpt": "",
        "stderr_excerpt": "",
        "note": "mock-pass",
    }


def _fail_result(gate_name: str, layer: str, rule_id: str) -> dict:
    return {
        "layer": layer,
        "rule_id": rule_id,
        "status": "FAIL",
        "violation_count": 1,
        "violations": [
            {
                "rule_id": rule_id,
                "code": "MOCK_FAILURE",
                "layer": layer,
                "message": f"forced failure at {gate_name}",
            }
        ],
        "started_at": "2026-05-01T23:00:00+00:00",
        "finished_at": "2026-05-01T23:00:00+00:00",
        "command": ["mock", gate_name],
        "exit_code": 1,
        "output_hash": f"fail-{gate_name.lower()}",
        "stdout_excerpt": "",
        "stderr_excerpt": "",
        "note": "mock-fail",
    }


class FailureAggregationTests(unittest.TestCase):
    def test_fail_closed_on_partial_subsystem_failure(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")
        executed: list[str] = []

        def executor(gate, _root, _timeout):
            executed.append(gate.name)
            if gate.name == "GOVERNANCE":
                return _fail_result(gate.name, gate.layer, gate.rule_id)
            return _pass_result(gate.name, gate.layer, gate.rule_id)

        runner = UnifiedComplianceRunner(
            root=Path.cwd(),
            gates=gates,
            executor=executor,
            now_provider=_fixed_now,
        )
        report = runner.run()

        self.assertEqual("GATE_FAILED", report["compliance_status"])
        self.assertEqual("governance", report["failing_layer"])
        self.assertFalse(report["partial_success_allowed"])

        self.assertEqual(
            ["SCI_AUTHORITY", "EMV", "CI_KERNEL", "AUTH", "GOVERNANCE"],
            executed,
        )
        self.assertEqual("SKIPPED", report["layer_results"]["API"]["status"])
        self.assertEqual("SKIPPED", report["layer_results"]["DRIFT_HARDENING"]["status"])
        self.assertGreaterEqual(report["violation_count"], 1)
        self.assertEqual("GOV-GATE-001", report["violations"][0]["rule_id"])


if __name__ == "__main__":
    unittest.main()
