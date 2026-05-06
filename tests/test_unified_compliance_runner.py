"""Sprint 11 unified compliance runner tests."""

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


class UnifiedComplianceRunnerTests(unittest.TestCase):
    def test_unified_contract_shape_and_status(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")

        def executor(gate, _root, _timeout):
            return _pass_result(gate.name, gate.layer, gate.rule_id)

        runner = UnifiedComplianceRunner(
            root=Path.cwd(),
            gates=gates,
            executor=executor,
            now_provider=_fixed_now,
        )
        report = runner.run()

        self.assertEqual("COMPLIANT", report["compliance_status"])
        self.assertEqual(0, report["violation_count"])

        required_keys = {
            "compliance_status",
            "violation_count",
            "layer_results",
            "execution_trace",
            "timestamp",
            "schema_version",
            "deterministic_hash",
        }
        self.assertTrue(required_keys.issubset(report.keys()))

    def test_same_input_same_output_hash(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")

        def executor(gate, _root, _timeout):
            return _pass_result(gate.name, gate.layer, gate.rule_id)

        runner = UnifiedComplianceRunner(
            root=Path.cwd(),
            gates=gates,
            executor=executor,
            now_provider=_fixed_now,
        )

        first = runner.run()
        second = runner.run()

        self.assertEqual(first["deterministic_hash"], second["deterministic_hash"])
        self.assertEqual(first["compliance_status"], second["compliance_status"])


if __name__ == "__main__":
    unittest.main()
