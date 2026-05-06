from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from core.bootstrap_runtime import CANONICAL_READY_STATE_PATH, read_boot_state, validate_ready_state
from kernel.state import EXIT_HARD_FAIL, EXIT_SUCCESS
from operations.event_log import OperationsEventLog
from operations.lifecycle import LifecycleTransitionError, OperationsLifecycleManager
from operations.state_store import OperationsStateStore


@dataclass(frozen=True)
class OperationsConfig:
    root_path: str
    env_path: str
    mode: str
    phase: str
    source: str
    target: str
    parity_window: str
    fail_closed: bool
    scheduler_enabled: bool
    scheduler_interval_seconds: int
    health_host: str
    health_port: int
    state_path: str
    event_log_path: str
    pid_path: str
    bootstrap_emit_ready_state: str
    run_audit_root: str
    python_executable: str


class OperationsRuntimeError(RuntimeError):
    """Raised when operations runtime cannot satisfy deterministic contracts."""


class OperationsRuntimeController:
    def __init__(self, config: OperationsConfig) -> None:
        self.config = config
        self.root_path = Path(config.root_path).resolve()
        self.state_store = OperationsStateStore(path=self._resolve_path(config.state_path))
        self.event_log = OperationsEventLog(path=self._resolve_path(config.event_log_path))
        self.pid_path = self._resolve_path(config.pid_path)
        self.lifecycle = OperationsLifecycleManager(
            initial_state=self.state_store.load().get("lifecycle_state", "STOPPED")
        )
        self._shutdown = threading.Event()
        self._http_server: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None

    def _resolve_path(self, value: str) -> Path:
        candidate = Path(str(value))
        if candidate.is_absolute():
            return candidate
        return (self.root_path / candidate).resolve()

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_state(self) -> Dict[str, Any]:
        return self.state_store.load()

    def _save_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["lifecycle_state"] = self.lifecycle.state
        state["health_state"] = self.lifecycle.health_state(require_bootstrap=bool(state.get("require_bootstrap", True)))
        state["operational_truth"] = self.lifecycle.operational_truth(
            require_bootstrap=bool(state.get("require_bootstrap", True))
        )
        return self.state_store.save(state)

    def _transition(self, *, target: str, reason: str, state: Dict[str, Any]) -> None:
        event = self.lifecycle.transition(to_state=target, reason=reason)
        self.event_log.emit(
            action="lifecycle.transition",
            status="PASS",
            details={
                "from_state": event.from_state,
                "to_state": event.to_state,
                "reason": event.reason,
                "timestamp": event.timestamp,
            },
        )
        self._save_state(state)

    def _record_failure(self, *, state: Dict[str, Any], reason: str, action: str) -> None:
        state["require_bootstrap"] = True
        state["failure_reason"] = str(reason)
        state["failure_count"] = int(state.get("failure_count", 0)) + 1

        if int(state.get("failure_count", 0)) >= 3:
            self._transition(target="BLOCKED", reason=f"{action}:persistent_failure", state=state)
        else:
            self._transition(target="DEGRADED", reason=f"{action}:partial_failure", state=state)

        self.event_log.emit(
            action=action,
            status="FAIL",
            details={
                "reason": str(reason),
                "failure_count": int(state.get("failure_count", 0)),
            },
        )

    def _parse_json_summary(self, text: str) -> Dict[str, Any]:
        for raw_line in reversed(str(text).splitlines()):
            line = raw_line.strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def _run_system_kernel(self, *, args: list[str]) -> Dict[str, Any]:
        command = [self.config.python_executable, "system_kernel.py", *args]
        completed = subprocess.run(
            command,
            cwd=self.root_path,
            capture_output=True,
            text=True,
            check=False,
        )
        summary = self._parse_json_summary(completed.stdout)
        return {
            "exit_code": int(completed.returncode),
            "stdout": str(completed.stdout or ""),
            "stderr": str(completed.stderr or ""),
            "summary": summary,
            "command": command,
        }

    def bootstrap_once(self, *, trigger: str, override_used: bool = False) -> Dict[str, Any]:
        state = self._load_state()
        state.setdefault("metrics", {})
        state["metrics"]["bootstrap_attempts_total"] = int(state["metrics"].get("bootstrap_attempts_total", 0)) + 1

        try:
            self._transition(target="BOOTSTRAPPING", reason=f"bootstrap:{trigger}", state=state)
        except LifecycleTransitionError as exc:
            raise OperationsRuntimeError(str(exc)) from exc

        result = self._run_system_kernel(
            args=[
                "bootstrap",
                "--mode",
                self.config.mode,
                "--phase",
                self.config.phase,
                "--env-path",
                self.config.env_path,
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                self.config.bootstrap_emit_ready_state,
                "--fail-closed",
                "true",
            ]
        )

        canonical_bootstrap_path = self._resolve_path(str(CANONICAL_READY_STATE_PATH))
        boot_state: Dict[str, Any] = {}
        if canonical_bootstrap_path.exists() and canonical_bootstrap_path.is_file():
            try:
                boot_state = read_boot_state(canonical_bootstrap_path)
            except Exception as exc:
                state["metrics"]["bootstrap_failures_total"] = int(state["metrics"].get("bootstrap_failures_total", 0)) + 1
                state["failure_reason"] = f"bootstrap_state_parse_error:{exc}"
                state["require_bootstrap"] = True
                self._transition(target="BLOCKED", reason="bootstrap_state_parse_error", state=state)
                self._save_state(state)
                return {
                    "status": "FAIL",
                    "reason": str(exc),
                    "command": result,
                }

        state["last_bootstrap_result"] = dict(boot_state)
        state["integration_readiness_status"] = dict(boot_state.get("integration_status", {}))
        state["phase_state"] = {
            "phase": str(boot_state.get("phase", "")),
            "sst_owner": str(boot_state.get("sst_owner", "")),
        }
        state["adapter_health_snapshot"] = dict(boot_state.get("health_status", {}))

        is_ready = (
            int(result["exit_code"]) == 0
            and str(boot_state.get("execution_status", "")).upper() == "READY"
            and bool(boot_state.get("system_ready", False))
            and bool(boot_state.get("live_execution_safe", True))
            and bool(boot_state.get("external_dependencies_ready", True))
        )

        if is_ready:
            state["require_bootstrap"] = False
            state["failure_reason"] = ""
            state["failure_count"] = 0
            self._transition(target="READY", reason=f"bootstrap_ready:{trigger}", state=state)
            self._save_state(state)
            self.event_log.emit(
                action="bootstrap",
                status="PASS",
                override_used=override_used,
                details={
                    "trigger": trigger,
                    "exit_code": result["exit_code"],
                },
            )
            return {
                "status": "PASS",
                "boot_state": boot_state,
                "command": result,
            }

        state["metrics"]["bootstrap_failures_total"] = int(state["metrics"].get("bootstrap_failures_total", 0)) + 1
        state["require_bootstrap"] = True
        state["failure_reason"] = str(boot_state.get("reason") or result["stderr"] or "BOOTSTRAP_FAILED")
        state["failure_count"] = int(state.get("failure_count", 0)) + 1
        self._transition(target="BLOCKED", reason=f"bootstrap_failed:{trigger}", state=state)
        self._save_state(state)
        self.event_log.emit(
            action="bootstrap",
            status="FAIL",
            override_used=override_used,
            details={
                "trigger": trigger,
                "exit_code": result["exit_code"],
                "failure_reason": state["failure_reason"],
            },
        )
        return {
            "status": "FAIL",
            "boot_state": boot_state,
            "command": result,
            "reason": state["failure_reason"],
        }

    def _bootstrap_check_before_run(self, state: Dict[str, Any]) -> None:
        try:
            validate_ready_state(
                path=self._resolve_path(str(CANONICAL_READY_STATE_PATH)),
                mode=self.config.mode,
                phase=self.config.phase,
            )
        except Exception as exc:
            self._record_failure(state=state, reason=f"bootstrap_validation_failed:{exc}", action="run")
            self._save_state(state)
            raise OperationsRuntimeError(f"RUN_BLOCKED:{exc}") from exc

    def run_once(self, *, trigger: str, override_used: bool = False) -> Dict[str, Any]:
        state = self._load_state()
        state.setdefault("metrics", {})
        self._bootstrap_check_before_run(state)

        if self.lifecycle.state != "RUNNING":
            self._transition(target="RUNNING", reason=f"run_start:{trigger}", state=state)

        state["metrics"]["run_attempts_total"] = int(state["metrics"].get("run_attempts_total", 0)) + 1

        audit_folder = self._resolve_path(self.config.run_audit_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result = self._run_system_kernel(
            args=[
                "run",
                "--mode",
                self.config.mode,
                "--phase",
                self.config.phase,
                "--source",
                self.config.source,
                "--target",
                self.config.target,
                "--parity-window",
                self.config.parity_window,
                "--fail-closed",
                "true",
                "--emit-audit",
                str(audit_folder),
            ]
        )

        summary = dict(result.get("summary") or {})
        exit_code = int(result["exit_code"])
        state["last_run_status"] = {
            "trigger": trigger,
            "exit_code": exit_code,
            "decision": str(summary.get("decision") or ""),
            "run_id": str(summary.get("run_id") or ""),
            "audit_path": str(summary.get("audit_path") or ""),
            "timestamp": self._utc_now(),
        }

        if exit_code == EXIT_SUCCESS:
            state["require_bootstrap"] = False
            state["failure_reason"] = ""
            state["failure_count"] = 0
            state["last_successful_run_id"] = str(summary.get("run_id") or "")
            state["last_audit_path"] = str(summary.get("audit_path") or "")
            self._transition(target="RUNNING", reason=f"run_success:{trigger}", state=state)
            self._save_state(state)
            self.event_log.emit(
                action="run",
                status="PASS",
                override_used=override_used,
                details={
                    "trigger": trigger,
                    "run_id": state["last_successful_run_id"],
                    "audit_path": state["last_audit_path"],
                },
            )
            return {
                "status": "PASS",
                "summary": summary,
                "command": result,
            }

        state["metrics"]["run_failures_total"] = int(state["metrics"].get("run_failures_total", 0)) + 1
        failure_reason = str(summary.get("decision") or result["stderr"] or "RUN_FAILED")
        self._record_failure(state=state, reason=failure_reason, action="run")
        self._save_state(state)
        return {
            "status": "FAIL",
            "summary": summary,
            "command": result,
            "reason": failure_reason,
        }

    def get_status(self) -> Dict[str, Any]:
        state = self._load_state()
        return self._save_state(state)

    def get_health_payload(self) -> Dict[str, Any]:
        state = self._load_state()
        document = self._save_state(state)
        return {
            "status": document.get("health_state", "NOT_READY"),
            "lifecycle_state": document.get("lifecycle_state", "STOPPED"),
            "operational_truth": document.get("operational_truth", "NOT_RUNNING"),
            "require_bootstrap": bool(document.get("require_bootstrap", True)),
            "failure_reason": str(document.get("failure_reason", "")),
        }

    def get_last_audit(self) -> Dict[str, Any]:
        state = self._load_state()
        audit_path = str(state.get("last_audit_path") or "")
        if not audit_path:
            return {
                "status": "NOT_FOUND",
                "reason": "no successful run recorded",
            }

        audit_file = Path(audit_path)
        if not audit_file.is_absolute():
            audit_file = (self.root_path / audit_file).resolve()

        if not audit_file.exists() or not audit_file.is_file():
            return {
                "status": "NOT_FOUND",
                "audit_path": str(audit_file),
                "reason": "audit file missing",
            }

        payload = json.loads(audit_file.read_text(encoding="utf-8"))
        return {
            "status": "PASS",
            "audit_path": str(audit_file),
            "audit": payload,
        }

    def _build_http_handler(self):
        controller = self

        class OperationsHandler(BaseHTTPRequestHandler):
            def _write_json(self, payload: Dict[str, Any], status_code: int = 200) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    self._write_json(controller.get_health_payload())
                    return

                state = controller.get_status()

                if path == "/status":
                    self._write_json(state)
                    return
                if path == "/status/bootstrap":
                    self._write_json({"last_bootstrap_result": state.get("last_bootstrap_result", {})})
                    return
                if path == "/status/run":
                    self._write_json(
                        {
                            "last_run_status": state.get("last_run_status", {}),
                            "last_successful_run_id": state.get("last_successful_run_id", ""),
                            "last_audit_path": state.get("last_audit_path", ""),
                        }
                    )
                    return
                if path == "/status/adapters":
                    self._write_json({"adapter_health_snapshot": state.get("adapter_health_snapshot", {})})
                    return
                if path == "/status/integration":
                    self._write_json({"integration_readiness_status": state.get("integration_readiness_status", {})})
                    return
                if path == "/audit/last":
                    self._write_json(controller.get_last_audit())
                    return
                if path == "/metrics":
                    self._write_json({"metrics": state.get("metrics", {})})
                    return

                self._write_json({"status": "NOT_FOUND", "path": path}, status_code=404)

            def log_message(self, format: str, *args: object) -> None:
                return

        return OperationsHandler

    def _start_http_server(self) -> None:
        handler = self._build_http_handler()
        self._http_server = ThreadingHTTPServer((self.config.health_host, int(self.config.health_port)), handler)

        def _serve() -> None:
            if self._http_server is None:
                return
            self._http_server.serve_forever(poll_interval=0.5)

        self._http_thread = threading.Thread(target=_serve, daemon=True)
        self._http_thread.start()

    def _stop_http_server(self) -> None:
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=5)
            self._http_thread = None

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def run_service_loop(self) -> int:
        state = self._load_state()
        try:
            self._transition(target="STARTING", reason="service_start", state=state)
        except LifecycleTransitionError as exc:
            raise OperationsRuntimeError(str(exc)) from exc

        state["service"] = {
            "pid": int(os.getpid()),
            "host": self.config.health_host,
            "port": int(self.config.health_port),
            "started_at": self._utc_now(),
        }
        self._save_state(state)

        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(state["service"]["pid"]), encoding="utf-8")

        bootstrap_result = self.bootstrap_once(trigger="startup")
        if str(bootstrap_result.get("status", "")).upper() != "PASS":
            return EXIT_HARD_FAIL

        self._transition(target="RUNNING", reason="service_online", state=self._load_state())
        self._start_http_server()

        self.event_log.emit(
            action="service.start",
            status="PASS",
            details={
                "scheduler_enabled": bool(self.config.scheduler_enabled),
                "scheduler_interval_seconds": int(self.config.scheduler_interval_seconds),
                "health_host": self.config.health_host,
                "health_port": int(self.config.health_port),
            },
        )

        next_tick = time.time() + max(1, int(self.config.scheduler_interval_seconds))
        try:
            while not self._shutdown.is_set():
                if self.config.scheduler_enabled and time.time() >= next_tick:
                    self.run_once(trigger="scheduler")
                    next_tick = time.time() + max(1, int(self.config.scheduler_interval_seconds))
                self._shutdown.wait(1.0)
        finally:
            self._stop_http_server()
            current = self._load_state()
            try:
                self._transition(target="STOPPED", reason="service_stop", state=current)
            except LifecycleTransitionError:
                current["lifecycle_state"] = "STOPPED"
                self._save_state(current)
            self.event_log.emit(action="service.stop", status="PASS", details={})
            if self.pid_path.exists() and self.pid_path.is_file():
                self.pid_path.unlink()

        return EXIT_SUCCESS
