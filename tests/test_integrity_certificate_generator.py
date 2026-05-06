"""Sprint 12 integrity certificate generator tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legacy.stabilization_layer.integrity_certificate_generator import (
    IntegrityCertificateError,
    enforce_certificate,
    generate_system_integrity_certificate,
)


class IntegrityCertificateGeneratorTests(unittest.TestCase):
    def test_certificate_generation_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            s10 = root / "s10.json"
            s11 = root / "s11.json"
            s12 = root / "s12.json"
            out = root / "certificate.json"

            minimal_baseline = {"schema_version": "2.1", "layers": {}, "determinism_baseline": {"reference_hash": "abc123"}}
            for path in (s10, s11, s12):
                path.write_text(json.dumps(minimal_baseline, indent=2, sort_keys=True), encoding="utf-8")

            drift_report = {
                "schema_version": "2.1",
                "schema_drift": False,
                "dependency_drift": {"violation_count": 0, "is_valid": True, "violations": []},
                "frozen_layer_drift_count": 0,
                "drift_score": 0,
            }
            deterministic_report = {
                "schema_version": "2.1",
                "is_deterministic": True,
                "deterministic_hash": "abc123",
                "mismatches": [],
            }
            replay_report = {
                "schema_version": "2.1",
                "is_replay_consistent": True,
                "mismatches": [],
            }

            with patch("legacy.stabilization_layer.integrity_certificate_generator.compare_cross_sprint_baselines", return_value=drift_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.certify_unified_runner_determinism", return_value=deterministic_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.validate_execution_replay", return_value=replay_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_zero_frozen_drift", return_value=None), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_determinism", return_value=None), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_replay_consistency", return_value=None):
                certificate = generate_system_integrity_certificate(
                    root=root,
                    sprint10_baseline=s10,
                    sprint11_baseline=s11,
                    sprint12_baseline=s12,
                    output_path=out,
                )

            self.assertEqual("PASS", certificate["certificate_status"])
            self.assertEqual(0, certificate["drift_score"])
            self.assertTrue(out.exists())
            enforce_certificate(certificate)

    def test_certificate_fail_closed_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            s10 = root / "s10.json"
            s11 = root / "s11.json"
            s12 = root / "s12.json"

            minimal_baseline = {"schema_version": "2.1", "layers": {}, "determinism_baseline": {"reference_hash": "abc123"}}
            for path in (s10, s11, s12):
                path.write_text(json.dumps(minimal_baseline, indent=2, sort_keys=True), encoding="utf-8")

            drift_report = {
                "schema_version": "2.1",
                "schema_drift": False,
                "dependency_drift": {"violation_count": 0, "is_valid": True, "violations": []},
                "frozen_layer_drift_count": 1,
                "drift_score": 1,
            }
            deterministic_report = {
                "schema_version": "2.1",
                "is_deterministic": True,
                "deterministic_hash": "abc123",
                "mismatches": [],
            }
            replay_report = {
                "schema_version": "2.1",
                "is_replay_consistent": True,
                "mismatches": [],
            }

            with patch("legacy.stabilization_layer.integrity_certificate_generator.compare_cross_sprint_baselines", return_value=drift_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.certify_unified_runner_determinism", return_value=deterministic_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.validate_execution_replay", return_value=replay_report), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_zero_frozen_drift", side_effect=RuntimeError("drift")), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_determinism", return_value=None), \
                patch("legacy.stabilization_layer.integrity_certificate_generator.enforce_replay_consistency", return_value=None):
                certificate = generate_system_integrity_certificate(
                    root=root,
                    sprint10_baseline=s10,
                    sprint11_baseline=s11,
                    sprint12_baseline=s12,
                )

            self.assertEqual("FAIL", certificate["certificate_status"])
            with self.assertRaises(IntegrityCertificateError):
                enforce_certificate(certificate)


if __name__ == "__main__":
    unittest.main()
