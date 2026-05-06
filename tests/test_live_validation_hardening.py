"""Sprint 19 failure classification and hardening contract tests."""

from __future__ import annotations

import unittest

from operations.failure_classifier import classify_failure, flatten_integration_failures


class LiveValidationHardeningTests(unittest.TestCase):
    def test_classify_failure_connectivity(self) -> None:
        token = classify_failure(root_cause="urlopen error connection refused")
        self.assertEqual("CONNECTIVITY_FAILURE", token)

    def test_classify_failure_auth(self) -> None:
        token = classify_failure(root_cause="HTTP 401 unauthorized")
        self.assertEqual("AUTH_FAILURE", token)

    def test_classify_failure_schema(self) -> None:
        token = classify_failure(root_cause="schema handshake failed")
        self.assertEqual("SCHEMA_MISMATCH", token)

    def test_classify_failure_rate_limit(self) -> None:
        token = classify_failure(root_cause="HTTP 429 rate limit exceeded")
        self.assertEqual("RATE_LIMIT_EXCEEDED", token)

    def test_flatten_integration_failures_structure(self) -> None:
        integration_status = {
            "LogicMonitorAdapter": {
                "status": "FAIL",
                "operation": "logicmonitor_bootstrap_probe",
                "root_cause": "urlopen error connection refused",
                "failure_class": "CONNECTIVITY_FAILURE",
            },
            "AnsibleAdapter": {
                "status": "PASS",
            },
        }

        failures = flatten_integration_failures(
            integration_status=integration_status,
            phase="PHASE_2",
        )

        self.assertEqual(1, len(failures))
        self.assertEqual("LogicMonitorAdapter", failures[0]["adapter"])
        self.assertEqual("PHASE_2", failures[0]["phase"])
        self.assertEqual("logicmonitor_bootstrap_probe", failures[0]["operation"])
        self.assertEqual("CONNECTIVITY_FAILURE", failures[0]["failure_class"])


if __name__ == "__main__":
    unittest.main()
