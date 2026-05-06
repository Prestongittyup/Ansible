"""Sprint 10 system integrity auditor tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from legacy.hardening.system_integrity_auditor import (
    check_gate_consistency,
    detect_unauthorized_dependency_emergence,
    run_system_integrity_audit,
)


class SystemIntegrityTests(unittest.TestCase):
    def test_gate_consistency_pass(self) -> None:
        old = {"authority_gate": {"emv": {"status": "PASS"}, "ci_kernel": {"status": "PASS"}}}
        new = {"execution_timestamps": {"emv": {"status": "PASS"}, "ci_kernel": {"status": "PASS"}}}

        report = check_gate_consistency(old_baseline=old, new_baseline=new)
        self.assertTrue(report["consistent"])

    def test_gate_consistency_fail(self) -> None:
        old = {"authority_gate": {"emv": {"status": "PASS"}, "ci_kernel": {"status": "PASS"}}}
        new = {"execution_timestamps": {"emv": {"status": "PASS"}, "ci_kernel": {"status": "FAIL"}}}

        report = check_gate_consistency(old_baseline=old, new_baseline=new)
        self.assertFalse(report["consistent"])

    def test_detects_unauthorized_dependency_emergence(self) -> None:
        old_graph = {"query_layer.query_service": [], "api_layer.api_routes": []}
        new_graph = {"query_layer.query_service": ["api_layer.api_routes"], "api_layer.api_routes": []}

        report = detect_unauthorized_dependency_emergence(
            old_graph=old_graph,
            new_graph=new_graph,
            forbidden_edges=[("query_layer", "api_layer")],
        )
        self.assertFalse(report["is_clean"])
        self.assertEqual(1, len(report["new_violations"]))

    def test_run_system_integrity_clean_on_current_state(self) -> None:
        root = Path.cwd()
        old_baseline = root / "artifacts" / "baselines" / "sprint_9_pre_edit_baseline.json"
        new_baseline = root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json"

        self.assertTrue(old_baseline.exists())
        self.assertTrue(new_baseline.exists())

        report = run_system_integrity_audit(root, old_baseline, new_baseline)
        self.assertTrue(report["is_clean"])
        self.assertFalse(report["frozen_layer_changed"])


if __name__ == "__main__":
    unittest.main()
