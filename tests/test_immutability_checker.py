"""Sprint 10 immutability enforcement checker tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy.hardening.drift_detection_engine import compute_current_layer_hashes
from legacy.hardening.immutability_enforcement_checker import run_immutability_check


class ImmutabilityCheckerTests(unittest.TestCase):
    def test_clean_on_current_frozen_layers(self) -> None:
        root = Path.cwd()
        baseline = root / "artifacts" / "baselines" / "sprint_21_refactor_baseline.json"

        report = run_immutability_check(
            root=root,
            baseline_path=baseline,
            frozen_layers=["enforcement", "persistence", "query", "api", "auth", "governance", "observability"],
        )
        self.assertTrue(report["is_clean"])

    def test_detects_synthetic_immutability_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            query_file = root / "query_layer" / "query_service.py"
            query_file.parent.mkdir(parents=True, exist_ok=True)
            query_file.write_text("def value():\n    return 1\n", encoding="utf-8")

            validation_file = root / "ansible_validation" / "outputs" / "validation_results.json"
            validation_file.parent.mkdir(parents=True, exist_ok=True)
            validation_file.write_text(
                json.dumps(
                    {
                        "metadata": {"schema_version": "2.1"},
                        "schema": {"schema_version": "2.1"},
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )

            layers = compute_current_layer_hashes(root, {"query": ["query_layer/**/*.py"]})
            baseline = {"schema_version": "2.1", "layers": {"query": layers["query"]}}
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")

            report_before = run_immutability_check(root=root, baseline_path=baseline_path, frozen_layers=["query"])
            self.assertTrue(report_before["is_clean"])

            query_file.write_text("def value():\n    return 2\n", encoding="utf-8")
            report_after = run_immutability_check(root=root, baseline_path=baseline_path, frozen_layers=["query"])
            self.assertFalse(report_after["is_clean"])
            self.assertGreater(report_after["hidden_behavioral_changes"]["violation_count"], 0)

    def test_detects_schema_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            query_file = root / "query_layer" / "query_service.py"
            query_file.parent.mkdir(parents=True, exist_ok=True)
            query_file.write_text("def value():\n    return 1\n", encoding="utf-8")

            validation_file = root / "ansible_validation" / "outputs" / "validation_results.json"
            validation_file.parent.mkdir(parents=True, exist_ok=True)
            validation_file.write_text(
                json.dumps(
                    {
                        "metadata": {"schema_version": "1.0"},
                        "schema": {"schema_version": "1.0"},
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )

            layers = compute_current_layer_hashes(root, {"query": ["query_layer/**/*.py"]})
            baseline = {"schema_version": "2.1", "layers": {"query": layers["query"]}}
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")

            report = run_immutability_check(root=root, baseline_path=baseline_path, frozen_layers=["query"])
            self.assertFalse(report["schema"]["all_consistent"])
            self.assertFalse(report["is_clean"])


if __name__ == "__main__":
    unittest.main()
