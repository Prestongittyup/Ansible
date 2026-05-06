"""Sprint 16 bootstrap and deployment runtime layer contract tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.bootstrap_runtime import CANONICAL_READY_STATE_PATH
from system_kernel import main


class BootstrapRuntimeLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd()
        logs_base = self.root / "logs"
        logs_base.mkdir(parents=True, exist_ok=True)
        self.logs_root = Path(tempfile.mkdtemp(prefix="sprint16_tests_", dir=str(logs_base)))

        canonical_state = (self.root / CANONICAL_READY_STATE_PATH).resolve()
        if canonical_state.exists():
            canonical_state.unlink()

    def _write_env(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_json_payload(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _load_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_mock_bootstrap_success(self) -> None:
        env_path = self.logs_root / "mock_success" / "mock.env"
        self._write_env(env_path, [])

        ready_path = self.logs_root / "mock_success" / "ready"
        exit_code = main(
            [
                "bootstrap",
                "--mode",
                "MOCK",
                "--phase",
                "PHASE_1",
                "--env-path",
                str(env_path),
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(ready_path),
                "--fail-closed",
                "true",
            ]
        )
        self.assertEqual(0, exit_code)

        state = self._load_json(ready_path / "runtime_ready_state.json")
        self.assertEqual("READY", state["execution_status"])
        self.assertTrue(state["system_ready"])
        self.assertFalse(state["fail_closed_triggered"])
        self.assertTrue(state["environment_valid"])

    def test_live_missing_env_hard_fail(self) -> None:
        env_path = self.logs_root / "live_missing_env" / "live.env"
        self._write_env(env_path, [])

        ready_path = self.logs_root / "live_missing_env" / "ready"
        exit_code = main(
            [
                "bootstrap",
                "--mode",
                "LIVE",
                "--phase",
                "PHASE_1",
                "--env-path",
                str(env_path),
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(ready_path),
                "--fail-closed",
                "true",
            ]
        )
        self.assertEqual(30, exit_code)

        state = self._load_json(ready_path / "runtime_ready_state.json")
        self.assertEqual("BLOCKED", state["execution_status"])
        self.assertFalse(state["system_ready"])
        self.assertTrue(state["fail_closed_triggered"])
        self.assertIn("Missing LIVE mode configuration", str(state.get("reason", "")))

    def test_adapter_failure_blocks_boot(self) -> None:
        scenario_root = self.logs_root / "adapter_failure"
        env_path = scenario_root / "live.env"

        logicmonitor_payload = scenario_root / "logicmonitor_payload.json"
        self._write_json_payload(
            logicmonitor_payload,
            [
                {
                    "ip_address": "10.0.0.10",
                    "hostname": "lm-device-1",
                }
            ],
        )

        nautobot_payload = scenario_root / "nautobot_payload.json"
        self._write_json_payload(
            nautobot_payload,
            [
                {
                    "ip_address": "10.0.0.20",
                    "hostname": "nb-device-1",
                }
            ],
        )

        missing_runner_path = scenario_root / "missing_runner"

        self._write_env(
            env_path,
            [
                "LOGICMONITOR_API_KEY=x",
                "LOGICMONITOR_ACCOUNT=test-account",
                "LOGICMONITOR_BASE_URL=http://127.0.0.1:9/santaba/rest",
                f"ANSIBLE_RUNNER_PATH={missing_runner_path}",
                "NAUTOBOT_TOKEN=x",
                "NAUTOBOT_URL=http://127.0.0.1:9",
                "POSTGRES_CONNECTION_STRING=postgresql://user:pass@127.0.0.1:1/bootstrap",
                "POSTGRES_DB=bootstrap",
                "POSTGRES_USER=user",
                "POSTGRES_PASSWORD=pass",
            ],
        )

        ready_path = scenario_root / "ready"
        exit_code = main(
            [
                "bootstrap",
                "--mode",
                "LIVE",
                "--phase",
                "PHASE_1",
                "--env-path",
                str(env_path),
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(ready_path),
                "--fail-closed",
                "true",
            ]
        )
        self.assertEqual(30, exit_code)

        state = self._load_json(ready_path / "runtime_ready_state.json")
        self.assertEqual("BLOCKED", state["execution_status"])
        self.assertIn("INTEGRATION_DEPENDENCIES_NOT_READY", str(state.get("reason", "")))

        integration_status = state.get("integration_status", {})
        self.assertIsInstance(integration_status, dict)
        self.assertEqual("FAIL", str(integration_status.get("AnsibleAdapter", {}).get("status", "")))

    def test_phase_mismatch_blocks_run(self) -> None:
        env_path = self.logs_root / "phase_mismatch" / "mock.env"
        self._write_env(env_path, [])

        ready_path = self.logs_root / "phase_mismatch" / "ready"
        bootstrap_exit = main(
            [
                "bootstrap",
                "--mode",
                "MOCK",
                "--phase",
                "PHASE_1",
                "--env-path",
                str(env_path),
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(ready_path),
                "--fail-closed",
                "true",
            ]
        )
        self.assertEqual(0, bootstrap_exit)

        audit_path = self.logs_root / "phase_mismatch" / "run_audit"
        run_exit = main(
            [
                "run",
                "--mode",
                "MOCK",
                "--phase",
                "PHASE_2",
                "--source",
                "mock",
                "--target",
                "mock",
                "--parity-window",
                "30d",
                "--fail-closed",
                "true",
                "--emit-audit",
                str(audit_path),
            ]
        )
        self.assertEqual(30, run_exit)

        audit = self._load_json(audit_path / "system_enforcement_audit.json")
        self.assertIn("BOOTSTRAP_PHASE_MISMATCH", " ".join(audit.get("errors", [])))

    def test_live_health_check_failure_blocks_boot(self) -> None:
        scenario_root = self.logs_root / "health_fail"
        env_path = scenario_root / "live.env"

        logicmonitor_payload = scenario_root / "logicmonitor_payload.json"
        ansible_inventory = scenario_root / "ansible_inventory.json"
        runner_path = scenario_root / "runner"
        runner_path.mkdir(parents=True, exist_ok=True)

        self._write_json_payload(
            logicmonitor_payload,
            [
                {
                    "ip_address": "10.1.0.10",
                    "hostname": "lm-health",
                }
            ],
        )
        self._write_json_payload(
            ansible_inventory,
            {
                "records": [
                    {
                        "ip_address": "10.1.0.20",
                        "hostname": "ansible-health",
                    }
                ]
            },
        )

        self._write_env(
            env_path,
            [
                "LOGICMONITOR_API_KEY=x",
                "LOGICMONITOR_ACCOUNT=test-account",
                "LOGICMONITOR_BASE_URL=http://127.0.0.1:9/santaba/rest",
                f"ANSIBLE_INVENTORY_SOURCE_PATH={ansible_inventory}",
                f"ANSIBLE_RUNNER_PATH={runner_path}",
                "ANSIBLE_PLAYBOOK_COMMAND=dummy_playbook.yml",
                "NAUTOBOT_TOKEN=x",
                "NAUTOBOT_URL=http://127.0.0.1:9",
                "POSTGRES_CONNECTION_STRING=postgresql://user:pass@127.0.0.1:1/bootstrap",
                "POSTGRES_DB=bootstrap",
                "POSTGRES_USER=user",
                "POSTGRES_PASSWORD=pass",
            ],
        )

        ready_path = scenario_root / "ready"
        exit_code = main(
            [
                "bootstrap",
                "--mode",
                "LIVE",
                "--phase",
                "PHASE_3",
                "--env-path",
                str(env_path),
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(ready_path),
                "--fail-closed",
                "true",
            ]
        )
        self.assertEqual(30, exit_code)

        state = self._load_json(ready_path / "runtime_ready_state.json")
        self.assertEqual("BLOCKED", state["execution_status"])
        self.assertIn("INTEGRATION_DEPENDENCIES_NOT_READY", str(state.get("reason", "")))
        integration_status = state.get("integration_status", {})
        self.assertEqual("FAIL", str(integration_status.get("NautobotAdapter", {}).get("status", "")))

    def test_ready_output_is_deterministic(self) -> None:
        env_path = self.logs_root / "deterministic" / "mock.env"
        self._write_env(env_path, [])

        ready_path = self.logs_root / "deterministic" / "ready_state.json"
        args = [
            "bootstrap",
            "--mode",
            "MOCK",
            "--phase",
            "PHASE_1",
            "--env-path",
            str(env_path),
            "--validate-env",
            "true",
            "--init-adapters",
            "true",
            "--health-check",
            "true",
            "--emit-ready-state",
            str(ready_path),
            "--fail-closed",
            "true",
        ]

        first_exit = main(args)
        first_state = self._load_json(ready_path)
        second_exit = main(args)
        second_state = self._load_json(ready_path)

        self.assertEqual(0, first_exit)
        self.assertEqual(0, second_exit)
        self.assertEqual(first_state, second_state)
        self.assertEqual(first_state.get("boot_id"), second_state.get("boot_id"))

    def test_run_requires_bootstrap_state(self) -> None:
        canonical_state = (self.root / CANONICAL_READY_STATE_PATH).resolve()
        if canonical_state.exists():
            canonical_state.unlink()

        audit_path = self.logs_root / "missing_bootstrap" / "run_audit"
        run_exit = main(
            [
                "run",
                "--mode",
                "MOCK",
                "--phase",
                "PHASE_1",
                "--source",
                "mock",
                "--target",
                "mock",
                "--parity-window",
                "30d",
                "--fail-closed",
                "true",
                "--emit-audit",
                str(audit_path),
            ]
        )
        self.assertEqual(30, run_exit)

        audit = self._load_json(audit_path / "system_enforcement_audit.json")
        self.assertIn("Boot state file missing", " ".join(audit.get("errors", [])))


if __name__ == "__main__":
    unittest.main()
