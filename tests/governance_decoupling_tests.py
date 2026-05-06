"""Sprint 22 governance decoupling tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from ci.core.ci_policy_engine import CIPolicyEngine
from contract_registry import resolve_contract
from kernel import gates


class GovernanceDecouplingTests(unittest.TestCase):
    def test_ci_policy_engine_returns_contract_bindings(self) -> None:
        decision = CIPolicyEngine().run()
        self.assertIn(str(decision.get("ci_status", "")).upper(), {"PASS", "FAIL"})
        self.assertIsInstance(decision.get("rule_results"), list)

        for rule_result in decision.get("rule_results", []):
            self.assertIsInstance(rule_result, dict)
            binding = rule_result.get("validator_binding", {})
            self.assertIsInstance(binding, dict)
            self.assertIn("validator_id", binding)
            self.assertIn("callable", binding)
            self.assertNotIn("file", binding)

    def test_sci_gate_uses_contract_resolution(self) -> None:
        result = gates.run_sci_gate(Path.cwd())
        self.assertEqual("SCI", result.gate)
        self.assertEqual("PASS", result.status)
        self.assertEqual("SCI_CONTRACT_VALID", result.reason)
        self.assertEqual("SCI_AUTHORITY", str(result.details.get("contract", "")))

    @patch("kernel.gates.subprocess.run")
    def test_emv_gate_uses_module_entrypoint(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"emv_status":"PASS","violation_count":0}',
            stderr="",
        )

        result = gates.run_emv_gate(Path.cwd(), sys.executable)

        self.assertEqual("PASS", result.status)
        command = mock_run.call_args[0][0]
        self.assertEqual("-m", command[1])
        self.assertEqual("ci.meta.ci_enforcement_meta_validator", command[2])

    @patch("kernel.gates.subprocess.run")
    def test_ci_gate_uses_module_entrypoint(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"ci_status":"PASS","violation_count":0}',
            stderr="",
        )

        result = gates.run_ci_gate(Path.cwd(), sys.executable)

        self.assertEqual("PASS", result.status)
        command = mock_run.call_args[0][0]
        self.assertEqual("-m", command[1])
        self.assertEqual("ci.run_ci_kernel", command[2])

    def test_legacy_bridge_has_explicit_lock_marker(self) -> None:
        legacy_bridge = resolve_contract("LEGACY_BRIDGE")
        self.assertTrue(str(legacy_bridge.get("lock_marker", "")).strip())
        self.assertTrue(bool(legacy_bridge.get("locked", False)))
        self.assertTrue(bool(legacy_bridge.get("immutable", False)))


if __name__ == "__main__":
    unittest.main()
