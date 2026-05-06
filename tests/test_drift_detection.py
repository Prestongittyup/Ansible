"""Sprint 10 drift detection engine validation tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy.hardening.drift_detection_engine import (
    compare_with_baseline,
    compute_current_layer_hashes,
    validate_dependency_graph,
    validate_schema_versions,
)


class DriftDetectionTests(unittest.TestCase):
    def test_zero_false_positives_on_frozen_state(self) -> None:
        root = Path.cwd()
        baseline = json.loads((root / "artifacts" / "baselines" / "sprint_21_refactor_baseline.json").read_text(encoding="utf-8"))

        report = compare_with_baseline(
            root=root,
            baseline=baseline,
            layers_to_check=["enforcement", "persistence", "query", "api", "auth", "governance", "observability"],
        )
        self.assertTrue(report["all_unchanged"])

        schema = validate_schema_versions(root)
        self.assertTrue(schema["all_consistent"])

    def test_detects_synthetic_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            query_file = root / "query_layer" / "query_service.py"
            query_file.parent.mkdir(parents=True, exist_ok=True)
            query_file.write_text("def get_device():\n    return 'ok'\n", encoding="utf-8")

            layers = compute_current_layer_hashes(root, {"query": ["query_layer/**/*.py"]})
            baseline = {"layers": {"query": layers["query"]}}

            before = compare_with_baseline(root=root, baseline=baseline, layers_to_check=["query"])
            self.assertTrue(before["all_unchanged"])

            query_file.write_text("def get_device():\n    return 'changed'\n", encoding="utf-8")
            after = compare_with_baseline(root=root, baseline=baseline, layers_to_check=["query"])
            self.assertFalse(after["all_unchanged"])
            self.assertGreater(after["layers"]["query"]["mismatch_count"], 0)

    def test_detects_synthetic_dependency_drift(self) -> None:
        graph = {
            "query_layer.query_service": ["api_layer.api_routes"],
            "api_layer.api_routes": [],
        }
        forbidden = [("query_layer", "api_layer")]

        report = validate_dependency_graph(graph, forbidden)
        self.assertFalse(report["is_valid"])
        self.assertEqual(1, report["violation_count"])

    def test_deterministic_output_across_repeated_runs(self) -> None:
        root = Path.cwd()
        baseline = json.loads((root / "artifacts" / "baselines" / "sprint_21_refactor_baseline.json").read_text(encoding="utf-8"))

        first = compare_with_baseline(
            root=root,
            baseline=baseline,
            layers_to_check=["enforcement", "persistence", "query", "api", "auth", "governance", "observability"],
        )
        second = compare_with_baseline(
            root=root,
            baseline=baseline,
            layers_to_check=["enforcement", "persistence", "query", "api", "auth", "governance", "observability"],
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
