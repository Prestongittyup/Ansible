from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from adapters.ansible_adapter import AnsibleAdapter, AnsibleConfig
from adapters.logicmonitor_adapter import LogicMonitorAdapter, LogicMonitorConfig
from adapters.nautobot_adapter import NautobotAdapter, NautobotConfig
from adapters.postgres_adapter import PostgresAdapter, PostgresConfig
from core.cutover_simulation import simulate_cutover
from config.env_loader import AppConfig, load_app_config
from kernel.audit_writer import write_audit
from kernel.orchestrator import KernelOrchestrator
from kernel.parity_engine import normalize_records
from kernel.state import RunConfig, RunState
from runtime.mock import fixtures

KERNEL_ANCHOR = "SYSTEM_KERNEL_RUNTIME::v1.0::ANCHOR_HASH=3f2db198::FAIL_CLOSED=true"


@dataclass(frozen=True)
class RuntimeExecutionResult:
    state: RunState
    audit_path: Path
    app_config: AppConfig


class RuntimeEngine:
    def __init__(self, *, root_path: str | Path | None = None, kernel_anchor: str = KERNEL_ANCHOR) -> None:
        self.root_path = Path(root_path or Path.cwd()).resolve()
        self.kernel_anchor = kernel_anchor

    def run(
        self,
        *,
        mode: Optional[str] = None,
        phase: str = "auto",
        target_phase: str = "auto",
        fail_closed: bool = True,
        audit_path: Optional[str] = None,
        execution_mode: str = "READ_ONLY",
        governance_approved: bool = False,
        window_days: int = 30,
        required_consecutive_parity: int = 3,
        max_global_drift: float = 5.0,
        critical_events_180d: int = 0,
        python_executable: Optional[str] = None,
    ) -> RuntimeExecutionResult:
        app_config = load_app_config(
            root_path=self.root_path,
            mode_override=mode,
            phase_override=phase,
            target_phase_override=target_phase,
            fail_closed_override=fail_closed,
            audit_path_override=audit_path,
            window_days_override=window_days,
            required_consecutive_parity_override=required_consecutive_parity,
            max_global_drift_override=max_global_drift,
            execution_mode_override=execution_mode,
            governance_approved_override=governance_approved,
            critical_events_override=critical_events_180d,
            python_executable_override=python_executable,
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

        pipeline = self._execute_pipeline(
            app_config=app_config,
            logicmonitor_adapter=logicmonitor_adapter,
            ansible_adapter=ansible_adapter,
            nautobot_adapter=nautobot_adapter,
            postgres_adapter=postgres_adapter,
        )

        serialized_inputs = self._write_runtime_inputs(
            source_records=pipeline["source_records"],
            target_records=pipeline["target_records"],
            identity_events=pipeline["identity_events"],
        )

        run_config = RunConfig(
            phase=app_config.kernel.phase,
            target_phase=app_config.kernel.target_phase,
            window_days=app_config.runtime.window_days,
            required_consecutive_parity=app_config.runtime.required_consecutive_parity,
            max_global_drift=app_config.runtime.max_global_drift,
            fail_closed=app_config.runtime.fail_closed,
            audit_path=app_config.runtime.audit_path,
            execution_mode=app_config.kernel.execution_mode,
            governance_approved=app_config.kernel.governance_approved,
            source_data_path=serialized_inputs["source"],
            target_data_path=serialized_inputs["target"],
            identity_events_path=serialized_inputs["identity_events"],
            parity_history_path=app_config.runtime.parity_history_path,
            critical_events_180d=app_config.runtime.critical_events_180d,
            root_path=app_config.kernel.root_path,
            python_executable=app_config.kernel.python_executable,
        )

        orchestrator = KernelOrchestrator(
            root_path=app_config.kernel.root_path,
            kernel_anchor=self.kernel_anchor,
        )
        state = orchestrator.execute(run_config)

        cutover = simulate_cutover(
            current_phase=state.source_phase,
            target_phase=state.target_phase,
            sst_source=state.sst_source,
            sst_target=state.sst_target,
            identity_conflicts_total=state.parity_metrics.identity_conflicts_total,
            global_drift_pct=state.parity_metrics.global_drift_pct,
            max_global_drift=app_config.runtime.max_global_drift,
            tracker_summary=state.tracker_summary,
        )

        state.data_stats["kernel_mode"] = app_config.runtime.kernel_mode
        state.data_stats["execution_mode"] = app_config.kernel.execution_mode
        state.data_stats["runtime_inputs"] = {
            "source": serialized_inputs["source"],
            "target": serialized_inputs["target"],
            "identity_events": serialized_inputs["identity_events"],
        }
        state.data_stats["pipeline_trace"] = pipeline["pipeline_trace"]
        state.data_stats["adapter_results"] = pipeline["adapter_results"]
        state.data_stats["adapter_states"] = self._collect_adapter_states(
            logicmonitor_adapter=logicmonitor_adapter,
            ansible_adapter=ansible_adapter,
            nautobot_adapter=nautobot_adapter,
            postgres_adapter=postgres_adapter,
        )
        state.data_stats["cutover_simulation"] = cutover.to_dict()
        state.data_stats["deterministic_signature"] = self._build_deterministic_signature(
            kernel_mode=app_config.runtime.kernel_mode,
            state=state,
            adapter_states=state.data_stats["adapter_states"],
            cutover=cutover.to_dict(),
        )

        audit_output = write_audit(state, app_config.runtime.audit_path)
        return RuntimeExecutionResult(state=state, audit_path=audit_output, app_config=app_config)

    def _execute_pipeline(
        self,
        *,
        app_config: AppConfig,
        logicmonitor_adapter: LogicMonitorAdapter,
        ansible_adapter: AnsibleAdapter,
        nautobot_adapter: NautobotAdapter,
        postgres_adapter: PostgresAdapter,
    ) -> Dict[str, Any]:
        pipeline_trace: List[Dict[str, Any]] = []

        discovery_records = logicmonitor_adapter.fetch_discovery_records()
        pipeline_trace.append(
            {
                "stage": "discovery",
                "adapter": "LogicMonitorAdapter",
                "records": len(discovery_records),
            }
        )

        normalized_records = self._normalize_ingestion_records(discovery_records)
        pipeline_trace.append(
            {
                "stage": "normalization",
                "adapter": "runtime_engine",
                "records": len(normalized_records),
            }
        )

        ansible_execution_result = ansible_adapter.execute_playbook(
            operation="READ_ONLY_VALIDATE",
            records=normalized_records,
            governance_approved=False,
        )
        pipeline_trace.append(
            {
                "stage": "execution",
                "adapter": "AnsibleAdapter",
                "records": len(normalized_records),
                "status": ansible_execution_result.get("status", "UNKNOWN"),
            }
        )

        ansible_validated_records = ansible_adapter.fetch_validated_inventory()
        pipeline_trace.append(
            {
                "stage": "validation_output",
                "adapter": "AnsibleAdapter",
                "records": len(ansible_validated_records),
            }
        )

        postgres_write_result = postgres_adapter.write_canonical_inventory(ansible_validated_records)
        pipeline_trace.append(
            {
                "stage": "persistence",
                "adapter": "PostgresAdapter",
                "records": len(ansible_validated_records),
                "status": postgres_write_result.get("status", "UNKNOWN"),
            }
        )

        phase_token = str(app_config.kernel.phase).strip().upper()
        target_phase_token = str(app_config.kernel.target_phase).strip().upper()
        postgres_phase2_records = postgres_adapter.fetch_phase2_canonical_inventory()
        postgres_phase3_records = postgres_adapter.fetch_phase3_mirror_inventory()
        nautobot_records = nautobot_adapter.fetch_authoritative_inventory()

        source_records, target_records = self._resolve_parity_datasets(
            phase_token=phase_token,
            normalized_records=normalized_records,
            ansible_validated_records=ansible_validated_records,
            nautobot_records=nautobot_records,
            postgres_phase2_records=postgres_phase2_records,
            postgres_phase3_records=postgres_phase3_records,
        )

        pipeline_trace.append(
            {
                "stage": "parity_source_selection",
                "source_records": len(source_records),
                "target_records": len(target_records),
                "phase": phase_token,
                "target_phase": target_phase_token,
            }
        )

        governance_simulation_status = "NOT_REQUESTED"
        if app_config.kernel.execution_mode == "CHANGE_ENABLED":
            if phase_token not in {"PHASE3", "PHASE_3", "3"}:
                governance_simulation_status = "SIMULATION_BLOCK_PRE_PHASE3"
            elif not app_config.kernel.governance_approved:
                governance_simulation_status = "SIMULATION_BLOCK_NO_APPROVAL"
            else:
                governance_simulation_status = "SIMULATION_APPROVED"

        pipeline_trace.append(
            {
                "stage": "governance_simulation",
                "status": governance_simulation_status,
            }
        )

        identity_events = fixtures.identity_change_events() if app_config.runtime.kernel_mode == "MOCK" else []

        return {
            "source_records": self._identity_guard(source_records),
            "target_records": self._identity_guard(target_records),
            "identity_events": identity_events,
            "pipeline_trace": pipeline_trace,
            "adapter_results": {
                "ansible_execution": ansible_execution_result,
                "postgres_write": postgres_write_result,
                "governance_simulation": {
                    "status": governance_simulation_status,
                    "execution_mode": app_config.kernel.execution_mode,
                    "phase": phase_token,
                },
            },
        }

    def _normalize_ingestion_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized_map, conflicts = normalize_records(records, dataset_label="ingestion")
        if conflicts:
            raise RuntimeError(
                "Ingestion identity validation failed: "
                + json.dumps(conflicts, sort_keys=True, separators=(",", ":"))
            )

        normalized_records = [dict(normalized_map[key]) for key in sorted(normalized_map.keys())]
        for row in normalized_records:
            row["authority_owner"] = "LOGICMONITOR"
            row["sst_source"] = "LOGICMONITOR"
        return normalized_records

    def _resolve_parity_datasets(
        self,
        *,
        phase_token: str,
        normalized_records: List[Dict[str, Any]],
        ansible_validated_records: List[Dict[str, Any]],
        nautobot_records: List[Dict[str, Any]],
        postgres_phase2_records: List[Dict[str, Any]],
        postgres_phase3_records: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        if phase_token in {"PHASE3", "PHASE_3", "3"}:
            return nautobot_records, postgres_phase3_records
        if phase_token in {"PHASE2", "PHASE_2", "2", "AUTO"}:
            return ansible_validated_records, postgres_phase2_records
        return normalized_records, ansible_validated_records

    def _identity_guard(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        guarded: List[Dict[str, Any]] = []
        for index, row in enumerate(records):
            ip_address = str(row.get("ip_address") or "").strip()
            if not ip_address:
                raise RuntimeError(f"Identity enforcement failed: missing ip_address at index {index}")
            if ip_address in seen:
                raise RuntimeError(f"Identity enforcement failed: duplicate ip_address={ip_address}")
            seen.add(ip_address)
            guarded.append(dict(row))
        return guarded

    def _write_runtime_inputs(
        self,
        *,
        source_records: List[Dict[str, Any]],
        target_records: List[Dict[str, Any]],
        identity_events: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        runtime_input_root = self.root_path / "logs" / "runtime_inputs"
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

    def _collect_adapter_states(
        self,
        *,
        logicmonitor_adapter: LogicMonitorAdapter,
        ansible_adapter: AnsibleAdapter,
        nautobot_adapter: NautobotAdapter,
        postgres_adapter: PostgresAdapter,
    ) -> Dict[str, Any]:
        return {
            "LogicMonitorAdapter": logicmonitor_adapter.status_snapshot(),
            "AnsibleAdapter": ansible_adapter.status_snapshot(),
            "NautobotAdapter": nautobot_adapter.status_snapshot(),
            "PostgresAdapter": postgres_adapter.status_snapshot(),
        }

    def _build_deterministic_signature(
        self,
        *,
        kernel_mode: str,
        state: RunState,
        adapter_states: Dict[str, Any],
        cutover: Dict[str, Any],
    ) -> str:
        signature_payload = {
            "kernel_mode": kernel_mode,
            "phase_source": state.source_phase,
            "phase_target": state.target_phase,
            "decision": state.decision,
            "parity_hash": state.parity_metrics.deterministic_hash,
            "adapter_states": adapter_states,
            "cutover": cutover,
            "gate_results": [
                {
                    "gate": gate.gate,
                    "status": gate.status,
                    "reason": gate.reason,
                }
                for gate in state.gate_results
            ],
        }
        rendered = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
