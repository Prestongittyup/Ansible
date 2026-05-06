from pathlib import Path
import tempfile

from operations.service_runtime import OperationsConfig, OperationsRuntimeController

root = Path.cwd()
temp_root = Path(tempfile.mkdtemp(prefix="diag_ops2_", dir=str(root / "logs")))
env_path = temp_root / "mock.env"
env_path.write_text("\n", encoding="utf-8")

config = OperationsConfig(
    root_path=str(root),
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
    state_path=str(temp_root / "operations_state.json"),
    event_log_path=str(temp_root / "operations_events.jsonl"),
    pid_path=str(temp_root / "operations_service.pid"),
    bootstrap_emit_ready_state=str(temp_root / "bootstrap"),
    run_audit_root=str(temp_root / "runs"),
    python_executable="C:/Users/fb002895/AppData/Local/Programs/Python/Python311/python.exe",
)

controller = OperationsRuntimeController(config)
print("bootstrap status:", controller.bootstrap_once(trigger="diag")["status"])
run_result = controller.run_once(trigger="diag")
print("run status:", run_result.get("status"))
print("reason:", run_result.get("reason"))
print("decision:", run_result.get("summary", {}).get("decision"))
print("exit_code:", run_result.get("summary", {}).get("exit_code"))
print("audit_path:", run_result.get("summary", {}).get("audit_path"))
