from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_SECRET_ENV_MAPPINGS = {
    "LOGICMONITOR_API_KEY": "LOGICMONITOR_API_KEY_FILE",
    "NAUTOBOT_TOKEN": "NAUTOBOT_TOKEN_FILE",
    "POSTGRES_CONNECTION_STRING": "POSTGRES_CONNECTION_STRING_FILE",
}


def _read_secret_file(path_value: str) -> str:
    secret_path = Path(path_value)
    if not secret_path.exists() or not secret_path.is_file():
        return ""
    return secret_path.read_text(encoding="utf-8").strip()


def _materialize_runtime_env(*, root_path: Path, output_path: Path, base_env_path: Path) -> Path:
    merged: dict[str, str] = {}

    if base_env_path.exists() and base_env_path.is_file():
        for raw_line in base_env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            merged[key.strip()] = value.strip()

    for key, value in os.environ.items():
        if key.startswith("OPS_") or key in {
            "KERNEL_MODE",
            "KERNEL_PHASE",
            "RUN_SOURCE",
            "RUN_TARGET",
            "RUN_PARITY_WINDOW",
            "SCHEDULER_ENABLED",
            "SCHEDULER_INTERVAL_SECONDS",
            "LOGICMONITOR_ACCOUNT",
            "LOGICMONITOR_BASE_URL",
            "ANSIBLE_EXECUTION_ENDPOINT",
            "ANSIBLE_RUNNER_PATH",
            "NAUTOBOT_URL",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
        }:
            merged[key] = str(value)

    for env_key, file_env_key in _SECRET_ENV_MAPPINGS.items():
        file_path = str(os.environ.get(file_env_key, "")).strip()
        if not file_path:
            continue
        secret = _read_secret_file(file_path)
        if secret:
            merged[env_key] = secret

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(f"{key}={value}" for key, value in sorted(merged.items()))
    output_path.write_text(rendered + "\n", encoding="utf-8")
    return output_path


def _parse_bool(value: str, *, default: bool) -> bool:
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def main() -> int:
    root_path = Path(os.environ.get("OPS_ROOT_PATH", "/app")).resolve()
    python_executable = str(os.environ.get("OPS_PYTHON", sys.executable))

    base_env_path = Path(os.environ.get("OPS_BASE_ENV_PATH", str(root_path / "deploy" / ".env.ops.example")))
    runtime_env_path = Path(os.environ.get("OPS_RUNTIME_ENV_PATH", "/tmp/ops_runtime.env"))

    materialized_env = _materialize_runtime_env(
        root_path=root_path,
        output_path=runtime_env_path,
        base_env_path=base_env_path,
    )

    mode = str(os.environ.get("KERNEL_MODE", "MOCK")).strip().upper()
    phase = str(os.environ.get("KERNEL_PHASE", "PHASE_1")).strip().upper()
    source = str(os.environ.get("RUN_SOURCE", "mock")).strip().lower()
    target = str(os.environ.get("RUN_TARGET", "mock")).strip().lower()
    parity_window = str(os.environ.get("RUN_PARITY_WINDOW", "30d")).strip().lower()

    health_host = str(os.environ.get("OPS_HEALTH_HOST", "0.0.0.0")).strip()
    health_port = int(str(os.environ.get("OPS_HEALTH_PORT", "8088")))

    schedule_enabled = _parse_bool(str(os.environ.get("SCHEDULER_ENABLED", "true")), default=True)
    interval_seconds = int(str(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300")))

    command = [
        python_executable,
        "-m",
        "operations.service_entrypoint",
        "start",
        "--root-path",
        str(root_path),
        "--env-path",
        str(materialized_env),
        "--mode",
        mode,
        "--phase",
        phase,
        "--source",
        source,
        "--target",
        target,
        "--parity-window",
        parity_window,
        "--health-host",
        health_host,
        "--health-port",
        str(health_port),
        "--state-path",
        "logs/operations_state.json",
        "--event-log-path",
        "logs/operations_events.jsonl",
        "--pid-path",
        "logs/operations_service.pid",
        "--bootstrap-output",
        "logs/operations/bootstrap",
        "--run-audit-root",
        "logs/operations/runs",
        "--python-executable",
        python_executable,
    ]

    if schedule_enabled:
        command.append("--schedule")
        command.extend(["--interval-seconds", str(max(1, interval_seconds))])

    return subprocess.call(command, cwd=root_path)


if __name__ == "__main__":
    raise SystemExit(main())
