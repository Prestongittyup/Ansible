"""Sprint 12 cross-sprint drift comparator tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy.stabilization_layer.cross_sprint_drift_comparator import (
    CrossSprintDriftError,
    compare_cross_sprint_baselines,
    enforce_zero_frozen_drift,
)


class CrossSprintDriftComparatorTests(unittest.TestCase):
    def test_same_inputs_produce_identical_results(self) -> None:
        root = Path.cwd()

        first = compare_cross_sprint_baselines(
            sprint10_baseline=root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json",
            sprint11_baseline=root / "artifacts" / "baselines" / "sprint_11_pre_edit_baseline.json",
            sprint12_baseline=root / "artifacts" / "baselines" / "sprint_12_pre_edit_baseline.json",
            root=root,
        )
        second = compare_cross_sprint_baselines(
            sprint10_baseline=root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json",
            sprint11_baseline=root / "artifacts" / "baselines" / "sprint_11_pre_edit_baseline.json",
            sprint12_baseline=root / "artifacts" / "baselines" / "sprint_12_pre_edit_baseline.json",
            root=root,
        )

        self.assertEqual(first, second)

    def test_detects_injected_drift_fail_closed(self) -> None:
        root = Path.cwd()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            injected_s12 = temp_path / "sprint_12_injected.json"

            payload = json.loads((root / "artifacts" / "baselines" / "sprint_12_pre_edit_baseline.json").read_text(encoding="utf-8"))
            enforcement_files = payload["layers"]["enforcement"]["files"]
            changed_file = sorted(enforcement_files.keys())[0]
            enforcement_files[changed_file] = "0" * 64

            injected_s12.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

            report = compare_cross_sprint_baselines(
                sprint10_baseline=root / "artifacts" / "baselines" / "sprint_10_pre_edit_baseline.json",
                sprint11_baseline=root / "artifacts" / "baselines" / "sprint_11_pre_edit_baseline.json",
                sprint12_baseline=injected_s12,
                root=root,
            )

            self.assertGreater(
                report["per_layer"]["enforcement"]["sprint11_to_sprint12_mismatch_count"],
                0,
            )
            with self.assertRaises(CrossSprintDriftError):
                enforce_zero_frozen_drift(report)


if __name__ == "__main__":
    unittest.main()
