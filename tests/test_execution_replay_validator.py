"""Sprint 12 execution replay validator tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from legacy.stabilization_layer.execution_replay_validator import (
    ExecutionReplayError,
    enforce_replay_consistency,
    validate_execution_replay,
)


def _command_name(command: list[str]) -> str:
    if len(command) > 1 and command[1].endswith("ci_enforcement_meta_validator.py"):
        return "emv"
    if len(command) > 1 and command[1].endswith("run_ci_kernel.py"):
        return "ci_kernel"
    return "unified_runner"


class ExecutionReplayValidatorTests(unittest.TestCase):
    def test_replay_consistent_with_volatile_changes(self) -> None:
        counters = {"emv": 0, "ci_kernel": 0, "unified_runner": 0}

        def runner(command: list[str], _root: Path, _timeout: int) -> dict:
            name = _command_name(command)
            counters[name] += 1
            tick = counters[name]
            if name == "emv":
                return {"emv_status": "PASS", "timestamp": f"t-{tick}"}
            if name == "ci_kernel":
                return {"ci_status": "PASS", "started_at": f"s-{tick}", "finished_at": f"f-{tick}"}
            return {
                "compliance_status": "COMPLIANT",
                "captured_at": f"c-{tick}",
                "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "EMV"}],
            }

        report = validate_execution_replay(
            root=Path.cwd(),
            replay_count=2,
            command_runner=runner,
        )

        self.assertTrue(report["is_replay_consistent"])
        enforce_replay_consistency(report)

    def test_detects_injected_replay_drift(self) -> None:
        counters = {"emv": 0, "ci_kernel": 0, "unified_runner": 0}

        def runner(command: list[str], _root: Path, _timeout: int) -> dict:
            name = _command_name(command)
            counters[name] += 1
            tick = counters[name]
            if name == "emv":
                return {"emv_status": "PASS", "timestamp": f"t-{tick}"}
            if name == "ci_kernel":
                return {"ci_status": "PASS", "timestamp": f"t-{tick}"}
            if tick == 1:
                return {
                    "compliance_status": "COMPLIANT",
                    "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "EMV"}],
                }
            return {
                "compliance_status": "COMPLIANT",
                "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "CI_KERNEL"}],
            }

        report = validate_execution_replay(
            root=Path.cwd(),
            replay_count=2,
            command_runner=runner,
        )

        self.assertFalse(report["is_replay_consistent"])
        with self.assertRaises(ExecutionReplayError):
            enforce_replay_consistency(report)


if __name__ == "__main__":
    unittest.main()
