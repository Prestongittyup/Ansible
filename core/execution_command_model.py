from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List

from adapters.ansible_adapter import AnsibleAdapter, AnsibleConfig
from adapters.logicmonitor_adapter import LogicMonitorAdapter, LogicMonitorConfig
from adapters.nautobot_adapter import NautobotAdapter, NautobotConfig
from adapters.postgres_adapter import PostgresAdapter, PostgresConfig
from config.env_loader import AppConfig, load_app_config
from kernel import gates
from kernel.audit_writer import write_audit
from kernel.parity_engine import ParityStabilityTracker, calculate_parity, normalize_records
from kernel.phase_resolver import PHASE_3, PhaseInfo, phase_info_for
from kernel.state import (
    EXIT_GATE_FAILURE,
    EXIT_HARD_FAIL,
    EXIT_ROLLBACK_TRIGGERED,
    EXIT_SUCCESS,
    GateResult,
    RunState,
)
from observability import context as obs_context
from observability.logger import classify_error, initialize_observability, log_event, log_span
from runtime.mock import fixtures

FULL_GATE_ORDER = [
    "SCI",
    "EMV",
    "CI",
    "AUTH",
    "GOVERNANCE",
    "API",
    "QUERY",
    "PERSISTENCE",
    "OBSERVABILITY",
    "EXPORT",
    "DRIFT",
    "PARITY",
]

_ALLOWED_MODES = {"MOCK", "LIVE", "HYBRID"}
_ALLOWED_PHASES = {"PHASE_1", "PHASE_2", "PHASE_3"}
_ALLOWED_SOURCES = {"logicmonitor", "mock"}
_ALLOWED_TARGETS = {"nautobot", "mock"}


class ExecutionCommandError(RuntimeError):
    """Execution command model contract error."""


@dataclass(frozen=True)
class ExecutionCommandConfig:
    mode: str
    phase: str
    source: str
    target: str
    parity_window_days: int
    fail_closed: bool
    emit_audit: str
    root_path: str
    python_executable: str
    debug: bool = False


@dataclass(frozen=True)
class ExecutionCommandResult:
    state: RunState
    audit_path: Path
    app_config: AppConfig | None


@dataclass(frozen=True)
class AdapterActivationRule:
    read_allowed: bool
    write_allowed: bool
    execute_validation_allowed: bool
    execute_mutation_allowed: bool


class ModeResolver:
    @staticmethod
    def resolve(*, mode: str, root_path: Path) -> str:
        token = str(mode).strip().upper()
        if token not in _ALLOWED_MODES:
            raise ExecutionCommandError(f"Invalid mode: {mode}")

        env_mode = ModeResolver._configured_kernel_mode(root_path)
        if env_mode and env_mode != token:
            raise ExecutionCommandError(
                f"MODE_MISMATCH_ENV: cli_mode={token}, configured_mode={env_mode}"
            )

        return token

    @staticmethod
    def _configured_kernel_mode(root_path: Path) -> str:
        env_value = str(os.environ.get("KERNEL_MODE") or "").strip().upper()
        if env_value:
            return env_value

        env_file = root_path / ".env"
        if not env_file.exists() or not env_file.is_file():
            return ""

        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "KERNEL_MODE":
                return value.strip().upper()
        return ""


class PhaseResolver:
    @staticmethod
    def resolve(*, phase: str) -> PhaseInfo:
        token = str(phase).strip().upper()
        if token not in _ALLOWED_PHASES:
            raise ExecutionCommandError(f"Invalid phase: {phase}")

        sst_set = {
            phase_info_for("PHASE_1").sst_source,
            phase_info_for("PHASE_2").sst_source,
            phase_info_for("PHASE_3").sst_source,
        }
        if len(sst_set) != 3:
            raise ExecutionCommandError("SST ambiguity detected across phases")

        return phase_info_for(token)


class AdapterActivationRegistry:
    def __init__(self, *, mode: str, phase_info: PhaseInfo, source: str, target: str) -> None:
        self.mode = mode
        self.phase_info = phase_info
        self.source = source
        self.target = target

        source_token = str(source).strip().lower()
        target_token = str(target).strip().lower()
        if source_token not in _ALLOWED_SOURCES:
            raise ExecutionCommandError(f"Invalid source selector: {source}")
        if target_token not in _ALLOWED_TARGETS:
            raise ExecutionCommandError(f"Invalid target selector: {target}")

        self._source = source_token
        self._target = target_token

        phase_is_3 = self.phase_info.phase == PHASE_3
        self._rules: Dict[str, AdapterActivationRule] = {
            "LogicMonitorAdapter": AdapterActivationRule(
                read_allowed=True,
                write_allowed=False,
                execute_validation_allowed=False,
                execute_mutation_allowed=False,
            ),
            "AnsibleAdapter": AdapterActivationRule(
                read_allowed=True,
                write_allowed=False,
                execute_validation_allowed=True,
                execute_mutation_allowed=phase_is_3,
            ),
            "NautobotAdapter": AdapterActivationRule(
                read_allowed=True,
                write_allowed=phase_is_3,
                execute_validation_allowed=False,
                execute_mutation_allowed=False,
            ),
            "PostgresAdapter": AdapterActivationRule(
                read_allowed=True,
                write_allowed=True,
                execute_validation_allowed=False,
                execute_mutation_allowed=False,
            ),
        }

        self._active: Dict[str, bool] = {
            "LogicMonitorAdapter": self._source == "logicmonitor",
            "AnsibleAdapter": True,
            "NautobotAdapter": self._target == "nautobot",
            "PostgresAdapter": True,
        }

    def assert_operation(self, *, adapter: str, operation: str) -> None:
        if adapter not in self._rules:
            raise ExecutionCommandError(f"Unknown adapter: {adapter}")
        if not self._active.get(adapter, False):
            raise ExecutionCommandError(f"Unauthorized adapter activation: {adapter}")

        rule = self._rules[adapter]
        operation_token = str(operation).strip().upper()

        if operation_token == "READ" and not rule.read_allowed:
            raise ExecutionCommandError(f"Adapter read not allowed: {adapter}")
        if operation_token == "WRITE" and not rule.write_allowed:
            raise ExecutionCommandError(f"Adapter write not allowed: {adapter}")
        if operation_token == "EXECUTE_VALIDATION" and not rule.execute_validation_allowed:
            raise ExecutionCommandError(f"Adapter validation execution not allowed: {adapter}")
        if operation_token == "EXECUTE_MUTATION" and not rule.execute_mutation_allowed:
            raise ExecutionCommandError(f"Adapter mutation execution not allowed: {adapter}")

    def active_adapters(self) -> List[str]:
        return [name for name, active in self._active.items() if active]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "phase": self.phase_info.phase,
            "source": self._source,
            "target": self._target,
            "active": dict(self._active),
            "rules": {
                key: {
                    "read_allowed": value.read_allowed,
                    "write_allowed": value.write_allowed,
                    "execute_validation_allowed": value.execute_validation_allowed,
                    "execute_mutation_allowed": value.execute_mutation_allowed,
                }
                for key, value in self._rules.items()
            },
        }


class ExecutionCommandEngine:
    def __init__(self, *, root_path: str | Path | None = None, kernel_anchor: str) -> None:
        self.root = Path(root_path or Path.cwd()).resolve()
        self.kernel_anchor = kernel_anchor

    def execute(self, config: ExecutionCommandConfig) -> ExecutionCommandResult:
        started = perf_counter()
        state = RunState(kernel_anchor=self.kernel_anchor, fail_closed=bool(config.fail_closed))
        state.gate_sequence_expected = list(FULL_GATE_ORDER)
        state.data_stats["runtime_gate_sequence"] = list(FULL_GATE_ORDER)
        state.data_stats["contract_gate_sequence"] = list(FULL_GATE_ORDER)

        existing_trace = obs_context.get_trace_id()
        if not existing_trace:
            initialize_observability(
                run_id=state.run_id,
                phase=config.phase,
                mode=config.mode,
                debug=bool(config.debug),
            )
        else:
            obs_context.update_run_context(
                run_id=state.run_id,
                phase=config.phase,
                mode=config.mode,
                debug=bool(config.debug),
            )

        state.data_stats["trace_id"] = obs_context.get_trace_id()
        log_event(
            level="INFO",
            component="execution_model",
            operation="command_model_start",
            status="START",
            source=config.source,
            target=config.target,
            parity_window_days=config.parity_window_days,
        )

        app_config: AppConfig | None = None

        try:
            if not config.fail_closed:
                raise ExecutionCommandError("Fail-closed must be true")

            mode = ModeResolver.resolve(mode=config.mode, root_path=self.root)
            phase_info = PhaseResolver.resolve(phase=config.phase)
            obs_context.update_run_context(mode=mode, phase=phase_info.phase)
            log_event(
                level="INFO",
                component="execution_model",
                operation="mode_phase_resolution",
                status="SUCCESS",
                mode=mode,
                phase=phase_info.phase,
                sst_owner=phase_info.sst_source,
            )

            state.set_phase_context(
                source_phase=phase_info.phase,
                target_phase=phase_info.phase,
                sst_source=phase_info.sst_source,
                sst_target=phase_info.sst_source,
            )

            app_config = load_app_config(
                root_path=self.root,
                mode_override=mode,
                phase_override=phase_info.phase,
                target_phase_override=phase_info.phase,
                fail_closed_override=True,
                audit_path_override=config.emit_audit,
                window_days_override=config.parity_window_days,
                required_consecutive_parity_override=1,
                execution_mode_override="READ_ONLY",
                governance_approved_override=False,
                python_executable_override=config.python_executable,
            )

            logicmonitor_adapter = LogicMonitorAdapter(
                LogicMonitorConfig(
                    mode=app_config.adapters.logicmonitor_mode,
                    api_key=app_config.adapters.logicmonitor_api_key,
                    account=app_config.adapters.logicmonitor_account,
                    base_url=app_config.adapters.logicmonitor_base_url,
                    live_payload_path=app_config.adapters.logicmonitor_live_payload_path,
                )
            )
            ansible_adapter = AnsibleAdapter(
                AnsibleConfig(
                    mode=app_config.adapters.ansible_mode,
                    inventory_source_path=app_config.adapters.ansible_inventory_source_path,
                    playbook_command=app_config.adapters.ansible_playbook_command,
                    execution_endpoint=app_config.adapters.ansible_execution_endpoint,
                    runner_path=app_config.adapters.ansible_runner_path,
                )
            )
            nautobot_adapter = NautobotAdapter(
                NautobotConfig(
                    mode=app_config.adapters.nautobot_mode,
                    token=app_config.adapters.nautobot_token,
                    base_url=app_config.adapters.nautobot_base_url,
                    desired_state_path=app_config.adapters.nautobot_desired_state_path,
                )
            )
            postgres_adapter = PostgresAdapter(
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
            )

            registry = AdapterActivationRegistry(
                mode=mode,
                phase_info=phase_info,
                source=config.source,
                target=config.target,
            )
            state.data_stats["adapter_activation_registry"] = registry.to_dict()
            state.data_stats["adapters_active"] = registry.active_adapters()
            state.data_stats["kernel_mode"] = mode
            state.data_stats["execution_mode"] = "READ_ONLY"
            state.data_stats["command_model"] = {
                "source": config.source,
                "target": config.target,
                "parity_window": f"{config.parity_window_days}d",
                "emit_audit": config.emit_audit,
            }
            log_event(
                level="INFO",
                component="execution_model",
                operation="adapter_activation",
                status="SUCCESS",
                active_adapters=registry.active_adapters(),
            )

            gate_sequence_started = perf_counter()
            log_event(
                level="INFO",
                component="execution_model",
                operation="gate_sequence",
                status="START",
                gate_order=FULL_GATE_ORDER,
            )

            if self._handle_gate_failure(state, gates.run_sci_gate(self.root)):
                return self._finalize(state, config.emit_audit, app_config)
            if self._handle_gate_failure(state, gates.run_emv_gate(self.root, app_config.kernel.python_executable)):
                return self._finalize(state, config.emit_audit, app_config)
            if self._handle_gate_failure(state, gates.run_ci_gate(self.root, app_config.kernel.python_executable)):
                return self._finalize(state, config.emit_audit, app_config)
            if self._handle_gate_failure(state, gates.run_auth_gate(phase_info)):
                return self._finalize(state, config.emit_audit, app_config)
            if self._handle_gate_failure(
                state,
                gates.run_governance_gate(
                    phase_info=phase_info,
                    execution_mode="READ_ONLY",
                    governance_approved=False,
                ),
            ):
                return self._finalize(state, config.emit_audit, app_config)

            log_event(
                level="INFO",
                component="execution_model",
                operation="gate_sequence",
                status="SUCCESS",
                duration_ms=int(round((perf_counter() - gate_sequence_started) * 1000.0, 0)),
                gates_executed=[gate.gate for gate in state.gate_results],
            )

            with log_span(
                component="execution_model",
                operation="source_target_collection",
                source=config.source,
                target=config.target,
            ):
                source_records = self._collect_source_records(
                    selector=config.source,
                    mode=mode,
                    registry=registry,
                    logicmonitor_adapter=logicmonitor_adapter,
                )
                target_records = self._collect_target_records(
                    selector=config.target,
                    phase=phase_info.phase,
                    mode=mode,
                    registry=registry,
                    nautobot_adapter=nautobot_adapter,
                )

            api_gate = GateResult(
                gate="API",
                status="PASS" if source_records and target_records else "FAIL",
                reason="API_DATA_READY" if source_records and target_records else "API_DATA_UNAVAILABLE",
                severity="SOFT" if source_records and target_records else "HARD",
                details={
                    "source_selector": config.source,
                    "target_selector": config.target,
                    "source_records": len(source_records),
                    "target_records": len(target_records),
                },
            )
            if self._handle_gate_failure(state, api_gate):
                return self._finalize(state, config.emit_audit, app_config)

            with log_span(
                component="execution_model",
                operation="query_normalization",
                source_records=len(source_records),
                target_records=len(target_records),
            ):
                source_map, source_conflicts = normalize_records(source_records, dataset_label="source")
                target_map, target_conflicts = normalize_records(target_records, dataset_label="target")
            query_conflicts = source_conflicts + target_conflicts
            if query_conflicts:
                query_gate = GateResult(
                    gate="QUERY",
                    status="FAIL",
                    reason="IDENTITY_CONFLICTS_PRESENT",
                    severity="HARD",
                    details={"conflicts": query_conflicts},
                )
                if self._handle_gate_failure(state, query_gate):
                    return self._finalize(state, config.emit_audit, app_config)

            source_normalized = [dict(source_map[key]) for key in sorted(source_map.keys())]
            target_normalized = [dict(target_map[key]) for key in sorted(target_map.keys())]

            query_gate = GateResult(
                gate="QUERY",
                status="PASS",
                reason="QUERY_NORMALIZATION_VALID",
                severity="SOFT",
                details={
                    "source_records": len(source_normalized),
                    "target_records": len(target_normalized),
                },
            )
            if self._handle_gate_failure(state, query_gate):
                return self._finalize(state, config.emit_audit, app_config)

            with log_span(
                component="execution_model",
                operation="ansible_validation_execution",
                records=len(source_normalized),
            ):
                registry.assert_operation(adapter="AnsibleAdapter", operation="EXECUTE_VALIDATION")
                ansible_exec_result = ansible_adapter.execute_playbook(
                    operation="READ_ONLY_VALIDATE",
                    records=source_normalized,
                    governance_approved=False,
                )
            if str(ansible_exec_result.get("status", "")).upper() != "PASS":
                persistence_gate = GateResult(
                    gate="PERSISTENCE",
                    status="FAIL",
                    reason="ANSIBLE_VALIDATION_FAILED",
                    severity="HARD",
                    details={"ansible_execution": ansible_exec_result},
                )
                if self._handle_gate_failure(state, persistence_gate):
                    return self._finalize(state, config.emit_audit, app_config)

            with log_span(
                component="execution_model",
                operation="validated_inventory_persistence",
                adapter="PostgresAdapter",
            ):
                validated_records = ansible_adapter.fetch_validated_inventory()
                registry.assert_operation(adapter="PostgresAdapter", operation="WRITE")
                postgres_write_result = postgres_adapter.write_canonical_inventory(validated_records)

            persistence_ok = str(postgres_write_result.get("status", "")).upper() == "PASS"
            persistence_gate = GateResult(
                gate="PERSISTENCE",
                status="PASS" if persistence_ok else "FAIL",
                reason="CANONICAL_PERSISTENCE_VALID" if persistence_ok else "CANONICAL_PERSISTENCE_FAILED",
                severity="SOFT" if persistence_ok else "HARD",
                details={
                    "ansible_execution": ansible_exec_result,
                    "postgres_write": postgres_write_result,
                },
            )
            if self._handle_gate_failure(state, persistence_gate):
                return self._finalize(state, config.emit_audit, app_config)

            adapter_states = {
                "LogicMonitorAdapter": logicmonitor_adapter.status_snapshot(),
                "AnsibleAdapter": ansible_adapter.status_snapshot(),
                "NautobotAdapter": nautobot_adapter.status_snapshot(),
                "PostgresAdapter": postgres_adapter.status_snapshot(),
            }
            state.data_stats["adapter_states"] = adapter_states

            observability_ok = all(isinstance(value, dict) for value in adapter_states.values())
            observability_gate = GateResult(
                gate="OBSERVABILITY",
                status="PASS" if observability_ok else "FAIL",
                reason="OBSERVABILITY_TRACES_CAPTURED" if observability_ok else "OBSERVABILITY_TRACE_FAILURE",
                severity="SOFT" if observability_ok else "HARD",
                details={"adapter_states": adapter_states},
            )
            if self._handle_gate_failure(state, observability_gate):
                return self._finalize(state, config.emit_audit, app_config)

            identity_events = fixtures.identity_change_events() if mode == "MOCK" else []
            runtime_inputs = self._write_runtime_inputs(
                source_records=source_normalized,
                target_records=target_normalized,
                identity_events=identity_events,
            )
            state.data_stats["runtime_inputs"] = runtime_inputs

            export_gate = GateResult(
                gate="EXPORT",
                status="PASS",
                reason="EXPORT_ARTIFACTS_WRITTEN",
                severity="SOFT",
                details={"runtime_inputs": runtime_inputs},
            )
            if self._handle_gate_failure(state, export_gate):
                return self._finalize(state, config.emit_audit, app_config)

            with log_span(
                component="parity",
                operation="calculate_parity",
                max_global_drift=app_config.runtime.max_global_drift,
            ):
                parity = calculate_parity(
                    source_normalized,
                    target_normalized,
                    max_global_drift=app_config.runtime.max_global_drift,
                )
            state.set_parity_metrics(parity.metrics)
            log_event(
                level="INFO",
                component="parity",
                operation="parity_metrics",
                status="SUCCESS",
                total_records=parity.metrics.compared_records_total,
                drift_count=parity.metrics.drifted_records_total,
                drift_pct=parity.metrics.global_drift_pct,
                identity_conflicts=parity.metrics.identity_conflicts_total,
            )

            drift_gate = gates.run_drift_gate(
                metrics=parity.metrics,
                max_global_drift=app_config.runtime.max_global_drift,
            )
            if self._handle_gate_failure(state, drift_gate):
                return self._finalize(state, config.emit_audit, app_config)

            tracker = ParityStabilityTracker([])
            parity_pass = (
                parity.metrics.identity_conflicts_total == 0
                and parity.metrics.global_drift_pct < app_config.runtime.max_global_drift
                and parity.metrics.hard_drift_count == 0
            )
            tracker.record_run(
                timestamp=state.started_at,
                parity_pass=parity_pass,
                deterministic_hash=parity.metrics.deterministic_hash,
            )
            tracker_summary = tracker.evaluate(
                required_consecutive_passes=1,
                window_days=config.parity_window_days,
                now=state.started_at,
            )
            state.tracker_summary = tracker_summary

            parity_gate = gates.run_parity_gate(
                metrics=parity.metrics,
                max_global_drift=app_config.runtime.max_global_drift,
                tracker_summary=tracker_summary,
                critical_events_180d=app_config.runtime.critical_events_180d,
                enforce_cutover_stability=False,
            )
            if self._handle_gate_failure(state, parity_gate):
                return self._finalize(state, config.emit_audit, app_config)

            state.data_stats["parity_result"] = {
                "status": parity_gate.status,
                "reason": parity_gate.reason,
                "deterministic_hash": parity.metrics.deterministic_hash,
            }
            state.data_stats["drift_percentage"] = parity.metrics.global_drift_pct
            state.data_stats["identity_conflicts"] = parity.metrics.identity_conflicts

            state.mark_success(reason="EXECUTION_COMMAND_MODEL_PASS")
            return self._finalize(state, config.emit_audit, app_config)

        except Exception as exc:
            state.errors.append(str(exc))
            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="execution_model",
                operation="command_model_exception",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                **classified,
            )
            state.mark_failure(
                exit_code=EXIT_HARD_FAIL,
                reason=f"HARD_FAIL:{exc}",
                decision="HARD_FAIL",
            )

            if "kernel_mode" not in state.data_stats:
                state.data_stats["kernel_mode"] = str(config.mode).strip().upper()
            if "execution_mode" not in state.data_stats:
                state.data_stats["execution_mode"] = "READ_ONLY"
            if "adapter_states" not in state.data_stats:
                state.data_stats["adapter_states"] = {}

            return self._finalize(state, config.emit_audit, app_config)

    def _collect_source_records(
        self,
        *,
        selector: str,
        mode: str,
        registry: AdapterActivationRegistry,
        logicmonitor_adapter: LogicMonitorAdapter,
    ) -> List[Dict[str, Any]]:
        token = str(selector).strip().lower()
        if token == "mock":
            return fixtures.logicmonitor_discovery_records()

        registry.assert_operation(adapter="LogicMonitorAdapter", operation="READ")
        records = logicmonitor_adapter.fetch_discovery_records()
        if not records:
            raise ExecutionCommandError("Source records are empty")
        return records

    def _collect_target_records(
        self,
        *,
        selector: str,
        phase: str,
        mode: str,
        registry: AdapterActivationRegistry,
        nautobot_adapter: NautobotAdapter,
    ) -> List[Dict[str, Any]]:
        token = str(selector).strip().lower()
        if token == "mock":
            if phase == "PHASE_1":
                return fixtures.logicmonitor_discovery_records()
            if phase == "PHASE_2":
                return fixtures.postgres_canonical_records_for_phase2()
            return fixtures.nautobot_authoritative_records_for_phase3()

        registry.assert_operation(adapter="NautobotAdapter", operation="READ")
        records = nautobot_adapter.fetch_authoritative_inventory()
        if not records:
            raise ExecutionCommandError("Target records are empty")
        return records

    def _write_runtime_inputs(
        self,
        *,
        source_records: List[Dict[str, Any]],
        target_records: List[Dict[str, Any]],
        identity_events: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        runtime_input_root = self.root / "logs" / "runtime_inputs"
        runtime_input_root.mkdir(parents=True, exist_ok=True)

        source_path = runtime_input_root / "source_records.json"
        target_path = runtime_input_root / "target_records.json"
        identity_events_path = runtime_input_root / "identity_events.json"

        source_path.write_text(json.dumps(source_records, indent=2, sort_keys=True), encoding="utf-8")
        target_path.write_text(json.dumps(target_records, indent=2, sort_keys=True), encoding="utf-8")
        identity_events_path.write_text(json.dumps(identity_events, indent=2, sort_keys=True), encoding="utf-8")

        return {
            "source": str(source_path),
            "target": str(target_path),
            "identity_events": str(identity_events_path),
        }

    def _handle_gate_failure(self, state: RunState, gate_result: GateResult) -> bool:
        state.add_gate_result(gate_result)

        duration_ms = 0
        if gate_result.started_at and gate_result.finished_at:
            try:
                from datetime import datetime

                started_dt = datetime.fromisoformat(str(gate_result.started_at))
                finished_dt = datetime.fromisoformat(str(gate_result.finished_at))
                duration_ms = int(round((finished_dt - started_dt).total_seconds() * 1000.0, 0))
                if duration_ms < 0:
                    duration_ms = 0
            except Exception:
                duration_ms = 0

        log_event(
            level="INFO" if gate_result.status == "PASS" else "ERROR",
            component="gate",
            operation=gate_result.gate,
            status="SUCCESS" if gate_result.status == "PASS" else "FAIL",
            duration_ms=duration_ms,
            error=None if gate_result.status == "PASS" else gate_result.reason,
            severity=gate_result.severity,
            details=gate_result.details,
        )

        if gate_result.status == "PASS":
            return False

        if state.source_phase == PHASE_3 and gate_result.gate in {"DRIFT", "PARITY"}:
            state.mark_failure(
                exit_code=EXIT_ROLLBACK_TRIGGERED,
                reason=f"{gate_result.gate}:{gate_result.reason}",
                decision="ROLLBACK_TRIGGERED",
            )
            state.data_stats["execution_status"] = "BLOCKED"
            return True

        if gate_result.severity == "HARD":
            state.mark_failure(
                exit_code=EXIT_HARD_FAIL,
                reason=f"{gate_result.gate}:{gate_result.reason}",
                decision="GATE_HARD_FAIL",
            )
            state.data_stats["execution_status"] = "BLOCKED" if gate_result.gate == "GOVERNANCE" else "FAIL"
            return True

        state.mark_failure(
            exit_code=EXIT_GATE_FAILURE,
            reason=f"{gate_result.gate}:{gate_result.reason}",
            decision="GATE_FAIL",
        )
        state.data_stats["execution_status"] = "FAIL"
        return True

    def _finalize(
        self,
        state: RunState,
        audit_path: str,
        app_config: AppConfig | None,
    ) -> ExecutionCommandResult:
        if "execution_status" not in state.data_stats:
            state.data_stats["execution_status"] = "PASS" if state.exit_code == EXIT_SUCCESS else "FAIL"

        state.data_stats["fail_closed_triggered"] = bool(state.exit_code != EXIT_SUCCESS)

        signature_payload = {
            "mode": state.data_stats.get("kernel_mode", ""),
            "phase": state.source_phase,
            "sst_owner": state.sst_source,
            "gate_results": [
                {
                    "gate": gate.gate,
                    "status": gate.status,
                    "reason": gate.reason,
                }
                for gate in state.gate_results
            ],
            "drift_percentage": state.parity_metrics.global_drift_pct,
            "identity_conflicts": state.parity_metrics.identity_conflicts,
            "execution_status": state.data_stats.get("execution_status", ""),
        }
        rendered = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
        state.data_stats["deterministic_signature"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        state.data_stats["trace_id"] = obs_context.get_trace_id()
        state.data_stats["execution_timeline"] = obs_context.get_execution_timeline()
        state.data_stats["failure_point"] = obs_context.get_failure_point()
        state.data_stats["external_failures"] = obs_context.get_external_failures()

        log_event(
            level="INFO" if state.exit_code == EXIT_SUCCESS else "ERROR",
            component="execution_model",
            operation="command_model_finalize",
            status="SUCCESS" if state.exit_code == EXIT_SUCCESS else "FAIL",
            decision=state.decision,
            exit_code=state.exit_code,
        )

        output = write_audit(state, audit_path)
        return ExecutionCommandResult(state=state, audit_path=output, app_config=app_config)


def parse_parity_window_days(value: str) -> int:
    token = str(value).strip().lower()
    if not token.endswith("d"):
        raise ExecutionCommandError(f"Invalid parity window: {value}")

    number = token[:-1]
    if not number.isdigit():
        raise ExecutionCommandError(f"Invalid parity window: {value}")

    parsed = int(number)
    if parsed < 1:
        raise ExecutionCommandError(f"Invalid parity window: {value}")

    return parsed
