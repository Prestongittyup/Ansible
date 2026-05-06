"""Sprint 10 baseline comparison toolkit tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from legacy.hardening.baseline_comparison_toolkit import compare_baselines


class BaselineComparisonTests(unittest.TestCase):
    def test_compare_baselines_deterministic(self) -> None:
        root = Path.cwd()
        old_baseline = root / "artifacts" / "baselines" / "sprint_9_pre_edit_baseline.json"
        new_baseline = root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json"

        first = compare_baselines(old_baseline, new_baseline)
        second = compare_baselines(old_baseline, new_baseline)

        self.assertEqual(first, second)

    def test_expected_layer_change_profile(self) -> None:
        root = Path.cwd()
        report = compare_baselines(
            root / "artifacts" / "baselines" / "sprint_9_pre_edit_baseline.json",
            root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json",
        )

        self.assertTrue(report["has_changes"])

        frozen = ["enforcement", "persistence", "query", "api", "auth", "governance", "observability"]
        for layer in frozen:
            if layer in report["layer_diffs"]:
                self.assertFalse(report["layer_diffs"][layer]["layer_changed"])

        self.assertIn("export", report["layer_diffs"])
        self.assertTrue(report["layer_diffs"]["export"]["layer_changed"])


if __name__ == "__main__":
    unittest.main()
