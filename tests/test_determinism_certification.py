"""Sprint 12 determinism certification engine tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from legacy.stabilization_layer.determinism_certification_engine import (
    DeterminismCertificationError,
    certify_unified_runner_determinism,
    enforce_determinism,
)


def _runner_from_payloads(payloads: list[dict]):
    queue = list(payloads)

    def _runner(_root: Path, _python: str, _timeout: int) -> dict:
        if queue:
            return queue.pop(0)
        return payloads[-1]

    return _runner


class DeterminismCertificationTests(unittest.TestCase):
    def test_deterministic_payloads_pass(self) -> None:
        payload = {
            "compliance_status": "COMPLIANT",
            "deterministic_hash": "abc123",
            "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "EMV"}],
        }

        report = certify_unified_runner_determinism(
            root=Path.cwd(),
            run_count=3,
            runner=_runner_from_payloads([payload, payload, payload]),
        )

        self.assertTrue(report["is_deterministic"])
        self.assertEqual("abc123", report["deterministic_hash"])
        enforce_determinism(report)

    def test_hash_mismatch_fails_closed(self) -> None:
        payload_a = {
            "compliance_status": "COMPLIANT",
            "deterministic_hash": "abc123",
            "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "EMV"}],
        }
        payload_b = {
            "compliance_status": "COMPLIANT",
            "deterministic_hash": "xyz999",
            "execution_trace": [{"gate": "SCI_AUTHORITY"}, {"gate": "EMV"}],
        }

        report = certify_unified_runner_determinism(
            root=Path.cwd(),
            run_count=2,
            runner=_runner_from_payloads([payload_a, payload_b]),
        )

        self.assertFalse(report["is_deterministic"])
        with self.assertRaises(DeterminismCertificationError):
            enforce_determinism(report)


if __name__ == "__main__":
    unittest.main()
