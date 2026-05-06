from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from operations.event_log import OperationsEventLog


_OVERRIDE_TOKEN = "I_UNDERSTAND_FAIL_CLOSED"


class OperationsCliError(RuntimeError):
    """Raised when operations CLI encounters deterministic contract violations."""


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _event_log(args: argparse.Namespace) -> OperationsEventLog:
    event_path = Path(str(args.event_log_path))
    if not event_path.is_absolute():
        event_path = (Path(args.root_path).resolve() / event_path).resolve()
    return OperationsEventLog(path=event_path)


def _log_operator_action(
    args: argparse.Namespace,
    *,
    action: str,
    status: str,
    details: dict,
    override_used: bool = False,
) -> None:
    _event_log(args).emit(
        action=action,
        status=status,
        details=details,
        override_used=override_used,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operations_cli",
        description="Sprint 18 operational CLI extension",
    )

    parser.add_argument("--root-path", default=".")
    parser.add_argument("--env-path", default=".env")
    parser.add_argument("--mode", choices=["MOCK", "LIVE", "HYBRID"], default="MOCK")
    parser.add_argument("--phase", choices=["PHASE_1", "PHASE_2", "PHASE_3"], default="PHASE_1")
    parser.add_argument("--source", choices=["logicmonitor", "mock"], default="mock")
    parser.add_argument("--target", choices=["nautobot", "mock"], default="mock")
    parser.add_argument("--parity-window", default="30d")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--health-host", default="127.0.0.1")
    parser.add_argument("--health-port", type=int, default=8088)
    parser.add_argument("--state-path", default="logs/operations_state.json")
    parser.add_argument("--event-log-path", default="logs/operations_events.jsonl")
    parser.add_argument("--pid-path", default="logs/operations_service.pid")
    parser.add_argument("--bootstrap-output", default="logs/operations/bootstrap")
    parser.add_argument("--run-audit-root", default="logs/operations/runs")
    parser.add_argument("--python-executable", default=sys.executable)

    subparsers = parser.add_subparsers(dest="command", required=True)

    system = subparsers.add_parser("system")
    system_sub = system.add_subparsers(dest="system_command", required=True)

    start = system_sub.add_parser("start")
    start.add_argument("--detach", action="store_true")

    system_sub.add_parser("stop")

    restart = system_sub.add_parser("restart")
    restart.add_argument("--detach", action="store_true")

    system_sub.add_parser("status")
    system_sub.add_parser("health")

    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap_sub = bootstrap.add_subparsers(dest="bootstrap_command", required=True)
    force = bootstrap_sub.add_parser("force")
    force.add_argument("--override", required=True)

    run = subparsers.add_parser("run")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    run_sub.add_parser("execute")

    audit = subparsers.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_sub.add_parser("last")

    return parser


def _service_command(args: argparse.Namespace, command: str) -> list[str]:
    return [
        str(args.python_executable),
        "-m",
        "operations.service_entrypoint",
        command,
        "--root-path",
        str(args.root_path),
        "--env-path",
        str(args.env_path),
        "--mode",
        str(args.mode),
        "--phase",
        str(args.phase),
        "--source",
        str(args.source),
        "--target",
        str(args.target),
        "--parity-window",
        str(args.parity_window),
        "--health-host",
        str(args.health_host),
        "--health-port",
        str(args.health_port),
        "--state-path",
        str(args.state_path),
        "--event-log-path",
        str(args.event_log_path),
        "--pid-path",
        str(args.pid_path),
        "--bootstrap-output",
        str(args.bootstrap_output),
        "--run-audit-root",
        str(args.run_audit_root),
        "--python-executable",
        str(args.python_executable),
    ]


def _run_service_command(args: argparse.Namespace, command: str) -> dict:
    cmd = _service_command(args, command)
    if command == "start" and bool(args.schedule):
        cmd.append("--schedule")
        cmd.extend(["--interval-seconds", str(max(1, int(args.interval_seconds)))])

    completed = subprocess.run(
        cmd,
        cwd=Path(args.root_path).resolve(),
        capture_output=True,
        text=True,
        check=False,
    )

    payload = {}
    for raw_line in reversed((completed.stdout or "").splitlines()):
        line = raw_line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    return {
        "exit_code": int(completed.returncode),
        "stdout": str(completed.stdout or ""),
        "stderr": str(completed.stderr or ""),
        "payload": payload,
        "command": cmd,
    }


def _pid_file_path(args: argparse.Namespace) -> Path:
    candidate = Path(str(args.pid_path))
    if candidate.is_absolute():
        return candidate
    return (Path(args.root_path).resolve() / candidate).resolve()


def _health_url(args: argparse.Namespace) -> str:
    return f"http://{args.health_host}:{int(args.health_port)}/health"


def _try_http_health(args: argparse.Namespace) -> dict:
    endpoint = _health_url(args)
    try:
        with urllib_request.urlopen(endpoint, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            return payload
        return {"status": "NOT_READY", "reason": "invalid health payload"}
    except (urllib_error.URLError, json.JSONDecodeError) as exc:
        return {"status": "NOT_READY", "reason": str(exc)}


def _start_detached(args: argparse.Namespace) -> int:
    command = _service_command(args, "start")
    if bool(args.schedule):
        command.append("--schedule")
        command.extend(["--interval-seconds", str(max(1, int(args.interval_seconds)))])

    root = Path(args.root_path).resolve()
    log_file = root / "logs" / "operations_service.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    with log_file.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=creationflags,
        )

    _emit(
        {
            "status": "PASS",
            "action": "system.start",
            "detach": True,
            "pid": int(process.pid),
            "log_file": str(log_file),
        }
    )
    _log_operator_action(
        args,
        action="system.start",
        status="PASS",
        details={
            "detach": True,
            "pid": int(process.pid),
        },
    )
    return 0


def _stop_service(args: argparse.Namespace) -> int:
    pid_file = _pid_file_path(args)
    if not pid_file.exists() or not pid_file.is_file():
        _emit(
            {
                "status": "FAIL",
                "action": "system.stop",
                "reason": f"pid file missing: {pid_file}",
            }
        )
        _log_operator_action(
            args,
            action="system.stop",
            status="FAIL",
            details={"reason": f"pid file missing: {pid_file}"},
        )
        return 30

    pid = int(pid_file.read_text(encoding="utf-8").strip() or "0")
    if pid <= 0:
        _emit(
            {
                "status": "FAIL",
                "action": "system.stop",
                "reason": "invalid pid value",
            }
        )
        _log_operator_action(
            args,
            action="system.stop",
            status="FAIL",
            details={"reason": "invalid pid value"},
        )
        return 30

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            raise

    _emit(
        {
            "status": "PASS",
            "action": "system.stop",
            "pid": pid,
        }
    )
    _log_operator_action(
        args,
        action="system.stop",
        status="PASS",
        details={"pid": pid},
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(raw)

    if args.command == "system":
        if args.system_command == "start":
            if bool(args.detach):
                return _start_detached(args)

            command = _service_command(args, "start")
            if bool(args.schedule):
                command.append("--schedule")
                command.extend(["--interval-seconds", str(max(1, int(args.interval_seconds)))])
            exit_code = subprocess.call(command, cwd=Path(args.root_path).resolve())
            _log_operator_action(
                args,
                action="system.start",
                status="PASS" if exit_code == 0 else "FAIL",
                details={"detach": False, "exit_code": int(exit_code)},
            )
            return exit_code

        if args.system_command == "stop":
            return _stop_service(args)

        if args.system_command == "restart":
            stop_exit = _stop_service(args)
            if stop_exit not in {0, 30}:
                return stop_exit
            if bool(args.detach):
                restart_exit = _start_detached(args)
                _log_operator_action(
                    args,
                    action="system.restart",
                    status="PASS" if restart_exit == 0 else "FAIL",
                    details={"detach": True, "exit_code": int(restart_exit)},
                )
                return restart_exit

            command = _service_command(args, "start")
            if bool(args.schedule):
                command.append("--schedule")
                command.extend(["--interval-seconds", str(max(1, int(args.interval_seconds)))])
            restart_exit = subprocess.call(command, cwd=Path(args.root_path).resolve())
            _log_operator_action(
                args,
                action="system.restart",
                status="PASS" if restart_exit == 0 else "FAIL",
                details={"detach": False, "exit_code": int(restart_exit)},
            )
            return restart_exit

        if args.system_command == "status":
            result = _run_service_command(args, "status")
            _emit(
                {
                    "status": "PASS" if result["exit_code"] == 0 else "FAIL",
                    "action": "system.status",
                    "payload": result["payload"],
                }
            )
            _log_operator_action(
                args,
                action="system.status",
                status="PASS" if result["exit_code"] == 0 else "FAIL",
                details={"exit_code": int(result["exit_code"])},
            )
            return 0 if result["exit_code"] == 0 else 30

        if args.system_command == "health":
            health_payload = _try_http_health(args)
            if str(health_payload.get("status", "")).upper() in {"READY", "DEGRADED"}:
                _emit(
                    {
                        "status": "PASS",
                        "action": "system.health",
                        "payload": health_payload,
                        "source": "http",
                    }
                )
                _log_operator_action(
                    args,
                    action="system.health",
                    status="PASS",
                    details={"source": "http"},
                )
                return 0

            result = _run_service_command(args, "health")
            _emit(
                {
                    "status": "PASS" if result["exit_code"] == 0 else "FAIL",
                    "action": "system.health",
                    "payload": result["payload"],
                    "source": "state_store",
                }
            )
            _log_operator_action(
                args,
                action="system.health",
                status="PASS" if result["exit_code"] == 0 else "FAIL",
                details={"source": "state_store", "exit_code": int(result["exit_code"])},
            )
            return 0 if result["exit_code"] == 0 else 30

    if args.command == "bootstrap":
        if args.bootstrap_command != "force":
            raise OperationsCliError("Unsupported bootstrap action")
        if str(args.override).strip() != _OVERRIDE_TOKEN:
            _emit(
                {
                    "status": "FAIL",
                    "action": "bootstrap.force",
                    "reason": f"override token mismatch; expected {_OVERRIDE_TOKEN}",
                }
            )
            _log_operator_action(
                args,
                action="bootstrap.force",
                status="FAIL",
                details={"reason": "override token mismatch"},
            )
            return 30

        result = _run_service_command(args, "bootstrap")
        _emit(
            {
                "status": "PASS" if result["exit_code"] == 0 else "FAIL",
                "action": "bootstrap.force",
                "override_used": True,
                "payload": result["payload"],
            }
        )
        _log_operator_action(
            args,
            action="bootstrap.force",
            status="PASS" if result["exit_code"] == 0 else "FAIL",
            details={"exit_code": int(result["exit_code"])},
            override_used=True,
        )
        return 0 if result["exit_code"] == 0 else 30

    if args.command == "run":
        if args.run_command != "execute":
            raise OperationsCliError("Unsupported run action")

        result = _run_service_command(args, "run")
        _emit(
            {
                "status": "PASS" if result["exit_code"] == 0 else "FAIL",
                "action": "run.execute",
                "payload": result["payload"],
            }
        )
        _log_operator_action(
            args,
            action="run.execute",
            status="PASS" if result["exit_code"] == 0 else "FAIL",
            details={"exit_code": int(result["exit_code"])},
        )
        return 0 if result["exit_code"] == 0 else 30

    if args.command == "audit":
        if args.audit_command != "last":
            raise OperationsCliError("Unsupported audit action")

        result = _run_service_command(args, "audit-last")
        _emit(
            {
                "status": "PASS" if result["exit_code"] == 0 else "FAIL",
                "action": "audit.last",
                "payload": result["payload"],
            }
        )
        _log_operator_action(
            args,
            action="audit.last",
            status="PASS" if result["exit_code"] == 0 else "FAIL",
            details={"exit_code": int(result["exit_code"])},
        )
        return 0 if result["exit_code"] == 0 else 30

    raise OperationsCliError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
