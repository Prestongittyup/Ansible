from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List

from adapters.ansible_adapter import AnsibleAdapter, AnsibleConfig
from adapters.logicmonitor_adapter import LogicMonitorAdapter, LogicMonitorConfig
from adapters.nautobot_adapter import NautobotAdapter, NautobotConfig
from adapters.postgres_adapter import PostgresAdapter, PostgresConfig
from config.env_loader import AppConfig, ConfigError, load_app_config
from core.execution_command_model import ExecutionCommandError, ModeResolver, PhaseResolver
from core.integration_runtime import IntegrationRuntimeEvaluator
from kernel.phase_resolver import PHASE_3
from kernel.state import EXIT_HARD_FAIL, EXIT_SUCCESS
from observability import context as obs_context
from observability import trace as obs_trace
from observability.logger import classify_error, initialize_observability, log_event, log_span

CANONICAL_READY_STATE_PATH = Path("logs/runtime_ready_state.json")
BOOT_STATE_SCHEMA_VERSION = "1.0"


class BootstrapError(RuntimeError):
    """Fail-closed bootstrap contract violation."""


@dataclass(frozen=True)
class BootstrapCommandConfig:
    mode: str
    phase: str
    env_path: str
    validate_env: bool
    init_adapters: bool
    health_check: bool
    emit_ready_state: str
    fail_closed: bool
    root_path: str
    python_executable: str
    debug: bool = False


@dataclass(frozen=True)
class BootstrapResult:
    boot_state: Dict[str, Any]
    boot_state_path: Path
    canonical_state_path: Path
    exit_code: int


class BootstrapRuntimeEngine:
    def __init__(self, *, root_path: str | Path | None = None) -> None:
        self.root = Path(root_path or Path.cwd()).resolve()

    def bootstrap(self, config: BootstrapCommandConfig) -> BootstrapResult:
        started = perf_counter()
        mode = str(config.mode).strip().upper()
        phase = str(config.phase).strip().upper()

        existing_trace = obs_context.get_trace_id()
        if not existing_trace:
            initialize_observability(
                run_id=obs_context.get_run_id() or f"bootstrap:{mode}:{phase}",
                phase=phase,
                mode=mode,
                debug=bool(config.debug),
            )
        else:
            obs_context.update_run_context(mode=mode, phase=phase, debug=bool(config.debug))

        trace_id = obs_trace.ensure_trace_id(
            run_id=obs_context.get_run_id() or f"bootstrap:{mode}:{phase}",
            phase=phase,
            mode=mode,
        )

        boot_state: Dict[str, Any] = {
            "boot_id": "",
            "trace_id": trace_id,
            "mode": mode,
            "phase": phase,
            "environment_valid": False,
            "adapters_initialized": [],
            "adapters_failed": [],
            "integration_status": {},
            "external_dependencies_ready": False,
            "live_execution_safe": False,
            "system_ready": False,
            "health_status": {},
            "fail_closed_triggered": False,
            "execution_status": "BLOCKED",
            "schema_version": BOOT_STATE_SCHEMA_VERSION,
        }

        try:
            with log_span(
                component="bootstrap",
                operation="environment_validation",
                mode=mode,
                phase=phase,
            ):
                if not config.fail_closed:
                    raise BootstrapError("FAIL_CLOSED_MUST_BE_TRUE")
                if not config.validate_env:
                    raise BootstrapError("BOOTSTRAP_VALIDATE_ENV_REQUIRED")
                if not config.init_adapters:
                    raise BootstrapError("BOOTSTRAP_INIT_ADAPTERS_REQUIRED")
                if not config.health_check:
                    raise BootstrapError("BOOTSTRAP_HEALTH_CHECK_REQUIRED")

            resolved_mode = ModeResolver.resolve(mode=mode, root_path=self.root)
            phase_info = PhaseResolver.resolve(phase=phase)

            app_config = load_app_config(
                root_path=self.root,
                env_path_override=config.env_path,
                mode_override=resolved_mode,
                phase_override=phase_info.phase,
                target_phase_override=phase_info.phase,
                fail_closed_override=True,
                execution_mode_override="READ_ONLY",
                governance_approved_override=False,
                python_executable_override=config.python_executable,
                validate_env=config.validate_env,
            )

            boot_state["mode"] = app_config.runtime.kernel_mode
            boot_state["phase"] = phase_info.phase
            boot_state["sst_owner"] = phase_info.sst_source
            boot_state["environment_valid"] = True
            obs_context.update_run_context(phase=phase_info.phase, mode=resolved_mode)

            active_adapter_names = self._active_adapters_for_phase(phase_info.phase)
            log_event(
                level="INFO",
                component="bootstrap",
                operation="adapter_activation_resolution",
                status="SUCCESS",
                active_adapters=active_adapter_names,
            )

            with log_span(
                component="bootstrap",
                operation="integration_readiness",
                mode=app_config.runtime.kernel_mode,
                phase=phase_info.phase,
            ):
                integration_result = IntegrationRuntimeEvaluator().evaluate(
                    app_config=app_config,
                    mode=app_config.runtime.kernel_mode,
                    phase=phase_info.phase,
                    active_adapters=active_adapter_names,
                )
            boot_state["integration_status"] = integration_result.integration_status
            boot_state["external_dependencies_ready"] = integration_result.external_dependencies_ready
            boot_state["live_execution_safe"] = integration_result.live_execution_safe
            log_event(
                level="INFO",
                component="bootstrap",
                operation="integration_readiness_summary",
                status="SUCCESS" if integration_result.external_dependencies_ready else "FAIL",
                external_dependencies_ready=integration_result.external_dependencies_ready,
                live_execution_safe=integration_result.live_execution_safe,
                integration_status=integration_result.integration_status,
            )

            if app_config.runtime.kernel_mode in {"LIVE", "HYBRID"}:
                if not integration_result.external_dependencies_ready:
                    raise BootstrapError("INTEGRATION_DEPENDENCIES_NOT_READY")
                if not integration_result.live_execution_safe:
                    raise BootstrapError("LIVE_EXECUTION_NOT_SAFE")

            adapters = self._build_adapters(app_config)

            with log_span(
                component="bootstrap",
                operation="adapter_initialization",
                mode=app_config.runtime.kernel_mode,
                phase=phase_info.phase,
            ):
                adapters_initialized, adapters_failed, hard_init_failure = self._initialize_and_register_adapters(
                    adapters=adapters,
                    active_adapter_names=active_adapter_names,
                    app_config=app_config,
                )
            boot_state["adapters_initialized"] = sorted(adapters_initialized)
            boot_state["adapters_failed"] = list(adapters_failed)
            log_event(
                level="INFO",
                component="bootstrap",
                operation="adapter_initialization_summary",
                status="SUCCESS" if not adapters_failed else "WARN",
                adapters_initialized=sorted(adapters_initialized),
                adapters_failed=adapters_failed,
            )

            if hard_init_failure and adapters_failed:
                raise BootstrapError(str(adapters_failed[0].get("reason", "ADAPTER_INITIALIZATION_FAILED")))

            with log_span(
                component="bootstrap",
                operation="adapter_health_checks",
                mode=app_config.runtime.kernel_mode,
                phase=phase_info.phase,
            ):
                health_status = self._run_health_checks(
                    adapters=adapters,
                    active_adapter_names=active_adapter_names,
                    app_config=app_config,
                    phase=phase_info.phase,
                )
            boot_state["health_status"] = health_status
            log_event(
                level="INFO",
                component="bootstrap",
                operation="adapter_health_checks_summary",
                status="SUCCESS",
                health_status=health_status,
            )

            if app_config.runtime.kernel_mode == "LIVE":
                failed_health = [
                    name
                    for name, status in health_status.items()
                    if str(status.get("status", "")).upper() != "PASS"
                ]
                if failed_health:
                    raise BootstrapError("LIVE_BOOTSTRAP_HEALTH_CHECK_FAILED")

            config_lock_hash = self._build_config_lock_hash(
                mode=app_config.runtime.kernel_mode,
                phase=phase_info.phase,
                app_config=app_config,
                active_adapters=boot_state["adapters_initialized"],
                health_status=health_status,
                integration_status=boot_state["integration_status"],
            )
            boot_state["runtime_lock_hash"] = config_lock_hash
            boot_state["credential_injection"] = self._credential_injection_summary(app_config)

            boot_state["boot_id"] = self._build_boot_id(boot_state)
            obs_context.update_run_context(run_id=boot_state["boot_id"])
            boot_state["trace_id"] = trace_id
            boot_state["system_ready"] = True
            boot_state["execution_status"] = "READY"
            boot_state["fail_closed_triggered"] = False

            output_path, canonical_path = self._emit_ready_state(
                boot_state=boot_state,
                emit_ready_state=config.emit_ready_state,
            )
            log_event(
                level="INFO",
                component="bootstrap",
                operation="ready_state_emit",
                status="SUCCESS",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                ready_state_path=str(output_path),
                canonical_state_path=str(canonical_path),
            )
            return BootstrapResult(
                boot_state=boot_state,
                boot_state_path=output_path,
                canonical_state_path=canonical_path,
                exit_code=EXIT_SUCCESS,
            )

        except Exception as exc:
            if not boot_state.get("adapters_failed"):
                boot_state["adapters_failed"] = [{"adapter": "SYSTEM", "reason": str(exc)}]

            boot_state["fail_closed_triggered"] = True
            boot_state["system_ready"] = False
            boot_state["execution_status"] = "BLOCKED"
            boot_state["reason"] = str(exc)
            boot_state["boot_id"] = self._build_boot_id(boot_state)
            obs_context.update_run_context(run_id=boot_state["boot_id"])
            boot_state["trace_id"] = trace_id

            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="bootstrap",
                operation="bootstrap_failure",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                **classified,
            )

            output_path, canonical_path = self._emit_ready_state(
                boot_state=boot_state,
                emit_ready_state=config.emit_ready_state,
            )
            return BootstrapResult(
                boot_state=boot_state,
                boot_state_path=output_path,
                canonical_state_path=canonical_path,
                exit_code=EXIT_HARD_FAIL,
            )

    def _build_adapters(self, app_config: AppConfig) -> Dict[str, Any]:
        return {
            "LogicMonitorAdapter": LogicMonitorAdapter(
                LogicMonitorConfig(
                    mode=app_config.adapters.logicmonitor_mode,
                    api_key=app_config.adapters.logicmonitor_api_key,
                    account=app_config.adapters.logicmonitor_account,
                    base_url=app_config.adapters.logicmonitor_base_url,
                    live_payload_path=app_config.adapters.logicmonitor_live_payload_path,
                )
            ),
            "AnsibleAdapter": AnsibleAdapter(
                AnsibleConfig(
                    mode=app_config.adapters.ansible_mode,
                    inventory_source_path=app_config.adapters.ansible_inventory_source_path,
                    playbook_command=app_config.adapters.ansible_playbook_command,
                    execution_endpoint=app_config.adapters.ansible_execution_endpoint,
                    runner_path=app_config.adapters.ansible_runner_path,
                )
            ),
            "NautobotAdapter": NautobotAdapter(
                NautobotConfig(
                    mode=app_config.adapters.nautobot_mode,
                    token=app_config.adapters.nautobot_token,
                    base_url=app_config.adapters.nautobot_base_url,
                    desired_state_path=app_config.adapters.nautobot_desired_state_path,
                )
            ),
            "PostgresAdapter": PostgresAdapter(
                PostgresConfig(
                    mode=app_config.adapters.postgres_mode,
                    host=app_config.adapters.postgres_host,
                    port=app_config.adapters.postgres_port,
                    user=app_config.adapters.postgres_user,
                    password=app_config.adapters.postgres_password,
                    database=app_config.adapters.postgres_db,
                    sqlite_path=app_config.adapters.postgres_sqlite_path,
                    connection_string=app_config.adapters.postgres_connection_string,
                )
            ),
        }

    def _active_adapters_for_phase(self, phase: str) -> List[str]:
        active = [
            "LogicMonitorAdapter",
            "AnsibleAdapter",
            "PostgresAdapter",
        ]
        if str(phase).strip().upper() == PHASE_3:
            active.append("NautobotAdapter")
        return active

    def _allow_hybrid_fallback(self, *, adapter_name: str, app_config: AppConfig) -> bool:
        if adapter_name == "LogicMonitorAdapter":
            return bool(app_config.adapters.allow_hybrid_fallback_logicmonitor)
        if adapter_name == "AnsibleAdapter":
            return bool(app_config.adapters.allow_hybrid_fallback_ansible)
        if adapter_name == "NautobotAdapter":
            return bool(app_config.adapters.allow_hybrid_fallback_nautobot)
        if adapter_name == "PostgresAdapter":
            return bool(app_config.adapters.allow_hybrid_fallback_postgres)
        return False

    def _initialize_and_register_adapters(
        self,
        *,
        adapters: Dict[str, Any],
        active_adapter_names: List[str],
        app_config: AppConfig,
    ) -> tuple[List[str], List[Dict[str, str]], bool]:
        initialized: List[str] = []
        failures: List[Dict[str, str]] = []
        hard_failure = False

        for name in active_adapter_names:
            adapter = adapters[name]
            try:
                validation = adapter.validate_credentials()
                if (
                    app_config.runtime.kernel_mode == "HYBRID"
                    and str(validation.get("status", "")).upper() == "WARN"
                    and not self._allow_hybrid_fallback(adapter_name=name, app_config=app_config)
                ):
                    raise BootstrapError(f"HYBRID_FALLBACK_NOT_ENABLED::{name}")

                adapter.init()
                adapter.register()
                initialized.append(name)
            except Exception as exc:
                failures.append({"adapter": name, "reason": str(exc)})
                if app_config.runtime.kernel_mode == "MOCK":
                    continue
                if (
                    app_config.runtime.kernel_mode == "HYBRID"
                    and self._allow_hybrid_fallback(adapter_name=name, app_config=app_config)
                ):
                    continue
                hard_failure = True
                break

        return initialized, failures, hard_failure

    def _run_health_checks(
        self,
        *,
        adapters: Dict[str, Any],
        active_adapter_names: List[str],
        app_config: AppConfig,
        phase: str,
    ) -> Dict[str, Dict[str, Any]]:
        health_status: Dict[str, Dict[str, Any]] = {}

        for name in active_adapter_names:
            adapter = adapters[name]
            try:
                result = adapter.health_check(phase=phase)
                status = str(result.get("status", "")).upper()
                if status == "WARN":
                    result["status"] = "PASS"
                    status = "PASS"
                health_status[name] = result

                if status != "PASS":
                    if app_config.runtime.kernel_mode == "LIVE":
                        raise BootstrapError(f"HEALTH_CHECK_FAILED::{name}")
                    if (
                        app_config.runtime.kernel_mode == "HYBRID"
                        and not self._allow_hybrid_fallback(adapter_name=name, app_config=app_config)
                    ):
                        raise BootstrapError(f"HYBRID_HEALTH_CHECK_FAILED::{name}")
            except Exception as exc:
                health_status[name] = {
                    "status": "FAIL",
                    "adapter": name,
                    "phase": phase,
                    "reason": str(exc),
                }
                if app_config.runtime.kernel_mode == "LIVE":
                    raise BootstrapError(f"LIVE_HEALTH_CHECK_FAILURE::{name}") from exc
                if (
                    app_config.runtime.kernel_mode == "HYBRID"
                    and not self._allow_hybrid_fallback(adapter_name=name, app_config=app_config)
                ):
                    raise BootstrapError(f"HYBRID_HEALTH_CHECK_FAILURE::{name}") from exc

        return health_status

    def _build_config_lock_hash(
        self,
        *,
        mode: str,
        phase: str,
        app_config: AppConfig,
        active_adapters: List[str],
        health_status: Dict[str, Dict[str, Any]],
        integration_status: Dict[str, Dict[str, Any]],
    ) -> str:
        payload = {
            "mode": mode,
            "phase": phase,
            "sst_owner": PhaseResolver.resolve(phase=phase).sst_source,
            "active_adapters": list(active_adapters),
            "adapter_modes": {
                "LogicMonitorAdapter": app_config.adapters.logicmonitor_mode,
                "AnsibleAdapter": app_config.adapters.ansible_mode,
                "NautobotAdapter": app_config.adapters.nautobot_mode,
                "PostgresAdapter": app_config.adapters.postgres_mode,
            },
            "health_status": health_status,
            "integration_status": integration_status,
            "runtime": {
                "window_days": app_config.runtime.window_days,
                "required_consecutive_parity": app_config.runtime.required_consecutive_parity,
                "max_global_drift": app_config.runtime.max_global_drift,
            },
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def _credential_injection_summary(self, app_config: AppConfig) -> Dict[str, bool]:
        return {
            "logicmonitor_api_key_present": bool(app_config.adapters.logicmonitor_api_key),
            "logicmonitor_account_present": bool(app_config.adapters.logicmonitor_account),
            "ansible_execution_endpoint_present": bool(app_config.adapters.ansible_execution_endpoint),
            "ansible_runner_path_present": bool(app_config.adapters.ansible_runner_path),
            "ansible_inventory_path_present": bool(app_config.adapters.ansible_inventory_source_path),
            "nautobot_token_present": bool(app_config.adapters.nautobot_token),
            "nautobot_url_present": bool(app_config.adapters.nautobot_base_url),
            "postgres_connection_string_present": bool(app_config.adapters.postgres_connection_string),
            "postgres_db_present": bool(app_config.adapters.postgres_db),
            "postgres_user_present": bool(app_config.adapters.postgres_user),
            "postgres_password_present": bool(app_config.adapters.postgres_password),
        }

    def _build_boot_id(self, boot_state: Dict[str, Any]) -> str:
        payload = {
            "mode": boot_state.get("mode", ""),
            "phase": boot_state.get("phase", ""),
            "environment_valid": bool(boot_state.get("environment_valid", False)),
            "adapters_initialized": sorted(list(boot_state.get("adapters_initialized", []))),
            "adapters_failed": boot_state.get("adapters_failed", []),
            "integration_status": boot_state.get("integration_status", {}),
            "external_dependencies_ready": bool(boot_state.get("external_dependencies_ready", False)),
            "live_execution_safe": bool(boot_state.get("live_execution_safe", False)),
            "health_status": boot_state.get("health_status", {}),
            "execution_status": boot_state.get("execution_status", "BLOCKED"),
            "runtime_lock_hash": boot_state.get("runtime_lock_hash", ""),
            "reason": boot_state.get("reason", ""),
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "system-boot-" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]

    def _emit_ready_state(self, *, boot_state: Dict[str, Any], emit_ready_state: str) -> tuple[Path, Path]:
        output_path = Path(str(emit_ready_state))
        if output_path.suffix.lower() != ".json":
            output_path = output_path / "runtime_ready_state.json"

        output_path = (self.root / output_path).resolve() if not output_path.is_absolute() else output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(boot_state, indent=2, sort_keys=True), encoding="utf-8")

        canonical = (self.root / CANONICAL_READY_STATE_PATH).resolve()
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text(json.dumps(boot_state, indent=2, sort_keys=True), encoding="utf-8")

        return output_path, canonical


def read_boot_state(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise BootstrapError(f"Boot state file missing: {file_path}")

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BootstrapError("Boot state payload is not an object")
    return payload


def validate_ready_state(*, path: str | Path, mode: str, phase: str) -> Dict[str, Any]:
    payload = read_boot_state(path)

    if str(payload.get("execution_status", "")).upper() != "READY":
        raise BootstrapError("BOOTSTRAP_NOT_READY")
    if not bool(payload.get("system_ready", False)):
        raise BootstrapError("BOOTSTRAP_SYSTEM_NOT_READY")
    if bool(payload.get("fail_closed_triggered", True)):
        raise BootstrapError("BOOTSTRAP_FAIL_CLOSED_TRIGGERED")

    payload_mode = str(payload.get("mode", "")).strip().upper()
    payload_phase = str(payload.get("phase", "")).strip().upper()

    if payload_mode != str(mode).strip().upper():
        raise BootstrapError(f"BOOTSTRAP_MODE_MISMATCH:{payload_mode}!= {str(mode).strip().upper()}")
    if payload_phase != str(phase).strip().upper():
        raise BootstrapError(f"BOOTSTRAP_PHASE_MISMATCH:{payload_phase}!= {str(phase).strip().upper()}")

    integration_status = payload.get("integration_status")
    if not isinstance(integration_status, dict):
        raise BootstrapError("BOOTSTRAP_INTEGRATION_STATUS_MISSING")

    mode_token = str(mode).strip().upper()
    if mode_token in {"LIVE", "HYBRID"}:
        if not bool(payload.get("external_dependencies_ready", False)):
            raise BootstrapError("BOOTSTRAP_EXTERNAL_DEPENDENCIES_NOT_READY")
        if not bool(payload.get("live_execution_safe", False)):
            raise BootstrapError("BOOTSTRAP_LIVE_EXECUTION_NOT_SAFE")

        failed_integrations = [
            name
            for name, status in integration_status.items()
            if str(status.get("status", "")).upper() not in {"PASS", "SKIPPED"}
        ]
        if failed_integrations:
            raise BootstrapError("BOOTSTRAP_INTEGRATION_FAILURE:" + ",".join(sorted(failed_integrations)))

    if not str(payload.get("runtime_lock_hash", "")).strip():
        raise BootstrapError("BOOTSTRAP_RUNTIME_LOCK_MISSING")

    return payload
