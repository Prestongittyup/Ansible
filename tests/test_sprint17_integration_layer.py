"""Sprint 17 production integration layer contract tests."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from adapters.ansible_adapter import AnsibleAdapter, AnsibleConfig
from adapters.base import AdapterError
from adapters.nautobot_adapter import NautobotAdapter, NautobotConfig
from config.env_loader import ConfigError, load_app_config
from core.integration_runtime import IntegrationRuntimeEvaluator


class _StaticJsonHandler(BaseHTTPRequestHandler):
    routes: dict[tuple[str, str], tuple[int, object]] = {}

    def _handle(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        key = (method.upper(), path)
        status_code, payload = self.routes.get(key, (404, {"status": "NOT_FOUND"}))

        if method.upper() == "POST":
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))

        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(int(status_code))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _json_server(routes: dict[tuple[str, str], tuple[int, object]]):
    _StaticJsonHandler.routes = dict(routes)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StaticJsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class Sprint17IntegrationLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd()
        logs_root = self.root / "logs"
        logs_root.mkdir(parents=True, exist_ok=True)
        self.temp_root = Path(tempfile.mkdtemp(prefix="sprint17_tests_", dir=str(logs_root)))

    def _write_env(self, env_lines: list[str]) -> Path:
        env_path = self.temp_root / "test.env"
        env_path.write_text("\n".join(env_lines), encoding="utf-8")
        return env_path

    def _build_minimum_live_env(self, *, overrides: dict[str, str] | None = None) -> Path:
        runner_path = self.temp_root / "runner"
        runner_path.mkdir(parents=True, exist_ok=True)

        entries = {
            "KERNEL_MODE": "LIVE",
            "LOGICMONITOR_API_KEY": "integration-key",
            "LOGICMONITOR_ACCOUNT": "integration-account",
            "LOGICMONITOR_BASE_URL": "http://127.0.0.1:9/santaba/rest",
            "ANSIBLE_RUNNER_PATH": str(runner_path),
            "NAUTOBOT_TOKEN": "integration-token",
            "NAUTOBOT_URL": "http://127.0.0.1:9",
            "POSTGRES_CONNECTION_STRING": "postgresql://user:pass@127.0.0.1:1/integration",
            "POSTGRES_DB": "integration",
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": "pass",
        }
        if overrides:
            entries.update(overrides)

        lines = [f"{key}={value}" for key, value in entries.items()]
        return self._write_env(lines)

    def _load_live_config(self, env_path: Path):
        return load_app_config(
            root_path=self.root,
            env_path_override=str(env_path),
            mode_override="LIVE",
            phase_override="PHASE_3",
            target_phase_override="PHASE_3",
            fail_closed_override=True,
            execution_mode_override="READ_ONLY",
            governance_approved_override=False,
            validate_env=True,
        )

    def test_live_full_integration_real_endpoints_conditionally_executable(self) -> None:
        env_path = os.environ.get("SPRINT17_LIVE_ENV_PATH", "").strip()
        if not env_path:
            self.skipTest("SPRINT17_LIVE_ENV_PATH is not configured")

        app_config = load_app_config(
            root_path=self.root,
            env_path_override=env_path,
            mode_override="LIVE",
            phase_override="PHASE_3",
            target_phase_override="PHASE_3",
            fail_closed_override=True,
            execution_mode_override="READ_ONLY",
            governance_approved_override=False,
            validate_env=True,
        )

        result = IntegrationRuntimeEvaluator().evaluate(
            app_config=app_config,
            mode="LIVE",
            phase="PHASE_3",
            active_adapters=[
                "LogicMonitorAdapter",
                "AnsibleAdapter",
                "PostgresAdapter",
                "NautobotAdapter",
            ],
        )
        self.assertTrue(result.external_dependencies_ready)
        self.assertTrue(result.live_execution_safe)

    def test_unreachable_api_forced_failure(self) -> None:
        env_path = self._build_minimum_live_env(
            overrides={
                "LOGICMONITOR_BASE_URL": "http://127.0.0.1:1/santaba/rest",
            }
        )
        app_config = self._load_live_config(env_path)

        result = IntegrationRuntimeEvaluator().evaluate(
            app_config=app_config,
            mode="LIVE",
            phase="PHASE_1",
            active_adapters=["LogicMonitorAdapter"],
        )

        self.assertFalse(result.external_dependencies_ready)
        self.assertFalse(result.live_execution_safe)
        self.assertEqual("FAIL", result.integration_status["LogicMonitorAdapter"]["status"])

    def test_missing_credentials_forced_failure(self) -> None:
        env_path = self._write_env(["KERNEL_MODE=LIVE", "LOGICMONITOR_API_KEY=only-key"])

        with self.assertRaises(ConfigError):
            load_app_config(
                root_path=self.root,
                env_path_override=str(env_path),
                mode_override="LIVE",
                phase_override="PHASE_1",
                target_phase_override="PHASE_1",
                fail_closed_override=True,
                execution_mode_override="READ_ONLY",
                governance_approved_override=False,
                validate_env=True,
            )

    def test_postgres_transaction_failure(self) -> None:
        env_path = self._build_minimum_live_env(
            overrides={
                "POSTGRES_CONNECTION_STRING": "postgresql://user:pass@127.0.0.1:1/integration",
            }
        )
        app_config = self._load_live_config(env_path)

        result = IntegrationRuntimeEvaluator().evaluate(
            app_config=app_config,
            mode="LIVE",
            phase="PHASE_1",
            active_adapters=["PostgresAdapter"],
        )

        self.assertEqual("FAIL", result.integration_status["PostgresAdapter"]["status"])
        handshake = result.integration_status["PostgresAdapter"]["transaction_handshake"]
        self.assertEqual("FAIL", handshake["status"])

    def test_ansible_execution_failure(self) -> None:
        routes = {
            ("GET", "/health"): (200, {"status": "PASS"}),
            ("GET", "/inventory"): (200, {"records": [{"ip_address": "10.10.0.1", "hostname": "ansible-node"}]}),
            ("POST", "/run"): (200, {"status": "FAIL", "exit_code": 2, "stderr": "playbook error"}),
        }

        with _json_server(routes) as endpoint:
            adapter = AnsibleAdapter(
                AnsibleConfig(
                    mode="LIVE",
                    execution_endpoint=endpoint,
                )
            )

            result = adapter.execute_playbook(
                operation="READ_ONLY_VALIDATE",
                records=[{"ip_address": "10.10.0.1", "hostname": "ansible-node"}],
                governance_approved=False,
            )

        self.assertEqual("FAIL", str(result.get("status", "")))
        self.assertEqual(2, int(result.get("exit_code", -1)))

    def test_nautobot_phase3_write_gate_enforcement(self) -> None:
        adapter = NautobotAdapter(
            NautobotConfig(
                mode="LIVE",
                token="token",
                base_url="http://127.0.0.1:9",
                desired_state_path="",
            )
        )

        with self.assertRaises(AdapterError):
            adapter.write_authoritative_inventory(
                [{"ip_address": "10.20.0.1", "hostname": "nb-node"}],
                phase="PHASE_3",
                governance_approved=True,
                bootstrap_ready=False,
                sst_owner="NAUTOBOT",
            )


if __name__ == "__main__":
    unittest.main()
