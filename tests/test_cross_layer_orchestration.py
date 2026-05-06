"""Sprint 11 cross-layer orchestration consistency tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from legacy.compliance_layer.gate_sequencer import default_gate_definitions, expected_gate_sequence
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


class CrossLayerOrchestrationTests(unittest.TestCase):
    def test_no_hidden_bypass_paths(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")
        gate_names = [gate.name for gate in gates]

        self.assertEqual(expected_gate_sequence(), gate_names)
        self.assertEqual(len(gate_names), len(set(gate_names)))

    def test_unified_runner_matches_independent_gate_statuses(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")
        independent = {gate.name: "PASS" for gate in gates}

        def executor(gate, _root, _timeout):
            return _pass_result(gate.name, gate.layer, gate.rule_id)

        runner = UnifiedComplianceRunner(
            root=Path.cwd(),
            gates=gates,
            executor=executor,
            now_provider=_fixed_now,
        )
        report = runner.run()

        observed = {name: detail["status"] for name, detail in report["layer_results"].items()}
        self.assertEqual(independent, observed)
        self.assertEqual("COMPLIANT", report["compliance_status"])

    def test_no_race_condition_variance(self) -> None:
        gates = default_gate_definitions("C:/Program Files/Python313/python.exe")

        def executor(gate, _root, _timeout):
            return _pass_result(gate.name, gate.layer, gate.rule_id)

        runner = UnifiedComplianceRunner(
            root=Path.cwd(),
            gates=gates,
            executor=executor,
            now_provider=_fixed_now,
        )

        hashes = {runner.run()["deterministic_hash"] for _ in range(3)}
        self.assertEqual(1, len(hashes))


if __name__ == "__main__":
    unittest.main()
