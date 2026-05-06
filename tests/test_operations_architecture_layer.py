"""Sprint 18 deployment and operations architecture layer tests."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from operations.lifecycle import LifecycleTransitionError, OperationsLifecycleManager
from operations.service_runtime import OperationsConfig, OperationsRuntimeController


class OperationsArchitectureLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd()
        logs_root = self.root / "logs"
        logs_root.mkdir(parents=True, exist_ok=True)
        self.temp_root = Path(tempfile.mkdtemp(prefix="sprint18_tests_", dir=str(logs_root)))

        env_path = self.temp_root / "mock.env"
        env_path.write_text("\n", encoding="utf-8")

        self.config = OperationsConfig(
            root_path=str(self.root),
            env_path=str(env_path),
            mode="MOCK",
            phase="PHASE_1",
            source="mock",
            target="mock",
            parity_window="30d",
            fail_closed=True,
            scheduler_enabled=False,
            scheduler_interval_seconds=300,
            health_host="127.0.0.1",
            health_port=18088,
            state_path=str(self.temp_root / "operations_state.json"),
            event_log_path=str(self.temp_root / "operations_events.jsonl"),
            pid_path=str(self.temp_root / "operations_service.pid"),
            bootstrap_emit_ready_state=str(self.temp_root / "bootstrap"),
            run_audit_root=str(self.temp_root / "runs"),
            python_executable="C:/Users/fb002895/AppData/Local/Programs/Python/Python311/python.exe",
        )

    def test_lifecycle_rejects_invalid_transition(self) -> None:
        lifecycle = OperationsLifecycleManager(initial_state="STARTING")
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition(to_state="RUNNING", reason="invalid_shortcut")

    def test_bootstrap_persists_required_state_contract(self) -> None:
        controller = OperationsRuntimeController(self.config)
        bootstrap_result = controller.bootstrap_once(trigger="test")

        self.assertEqual("PASS", bootstrap_result["status"])
        state = controller.get_status()

        self.assertEqual("READY", state["lifecycle_state"])
        self.assertFalse(state["require_bootstrap"])
        self.assertIn("last_bootstrap_result", state)
        self.assertIn("integration_readiness_status", state)
        self.assertIn("phase_state", state)
        self.assertIn("adapter_health_snapshot", state)

    def test_run_once_updates_last_successful_run_and_audit(self) -> None:
        controller = OperationsRuntimeController(self.config)
        self.assertEqual("PASS", controller.bootstrap_once(trigger="test")["status"])

        run_result = controller.run_once(trigger="manual")
        self.assertEqual("PASS", run_result["status"])

        state = controller.get_status()
        self.assertEqual("RUNNING", state["lifecycle_state"])
        self.assertTrue(str(state.get("last_successful_run_id", "")).strip())

        audit_path = str(state.get("last_audit_path", "")).strip()
        self.assertTrue(audit_path)
        audit_file = Path(audit_path)
        if not audit_file.is_absolute():
            audit_file = (self.root / audit_file).resolve()
        self.assertTrue(audit_file.exists())

    def test_health_payload_reports_ready_after_bootstrap(self) -> None:
        controller = OperationsRuntimeController(self.config)
        self.assertEqual("PASS", controller.bootstrap_once(trigger="test")["status"])

        health = controller.get_health_payload()
        self.assertEqual("READY", str(health.get("status", "")))


if __name__ == "__main__":
    unittest.main()
