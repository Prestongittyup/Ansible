"""Sprint 11 deterministic gate sequencing tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from legacy.compliance_layer.gate_sequencer import (
    default_gate_definitions,
    expected_gate_sequence,
    validate_gate_sequence,
)
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


class GateSequencingTests(unittest.TestCase):
    def test_expected_sequence_contract(self) -> None:
        expected = [
            "SCI_AUTHORITY",
            "EMV",
            "CI_KERNEL",
            "AUTH",
            "GOVERNANCE",
            "API",
            "QUERY",
            "PERSISTENCE",
            "OBSERVABILITY",
            "EXPORT",
            "DRIFT_HARDENING",
        ]
        self.assertEqual(expected, expected_gate_sequence())

    def test_validate_sequence_detects_order_drift(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")
        gates[3], gates[4] = gates[4], gates[3]

        is_valid, issues = validate_gate_sequence(gates)
        self.assertFalse(is_valid)
        self.assertGreater(len(issues), 0)

    def test_runner_execution_trace_matches_contract_order(self) -> None:
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

        observed_trace = [item["gate"] for item in report["execution_trace"]]
        self.assertEqual(expected_gate_sequence(), observed_trace)


if __name__ == "__main__":
    unittest.main()
