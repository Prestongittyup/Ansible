from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from kernel import gates
from kernel.parity_engine import (
    ParityStabilityTracker,
    apply_ip_change_events,
    calculate_parity,
    load_history,
    save_history,
)
from kernel.phase_resolver import PHASE_2, PHASE_3, resolve_phase, resolve_target_phase
from kernel.state import (
    EXIT_GATE_FAILURE,
    EXIT_HARD_FAIL,
    EXIT_ROLLBACK_TRIGGERED,
    EXIT_SUCCESS,
    GateResult,
    RunConfig,
    RunState,
)

FULL_GATE_SEQUENCE = [
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
    "DRIFT_HARDENING",
]

RUNTIME_GATE_SEQUENCE = ["SCI", "EMV", "CI", "AUTH", "GOVERNANCE", "DRIFT", "PARITY"]


class KernelOrchestrator:
    def __init__(self, *, root_path: str | Path | None = None, kernel_anchor: str) -> None:
        self.root = Path(root_path or Path.cwd())
        self.kernel_anchor = kernel_anchor

    def execute(self, config: RunConfig) -> RunState:
        state = RunState(kernel_anchor=self.kernel_anchor, fail_closed=config.fail_closed)
        state.gate_sequence_expected = list(RUNTIME_GATE_SEQUENCE)
        state.data_stats["contract_gate_sequence"] = list(FULL_GATE_SEQUENCE)
        state.data_stats["runtime_gate_sequence"] = list(RUNTIME_GATE_SEQUENCE)

        try:
            if not config.fail_closed:
                state.mark_failure(
                    exit_code=EXIT_HARD_FAIL,
                    reason="FAIL_CLOSED_MODE_REQUIRED",
                    decision="HARD_FAIL",
                )
                return state

            # Step 0: SCI Gate
            sci_result = gates.run_sci_gate(self.root)
            state.add_gate_result(sci_result)
            if self._should_stop_on_gate_failure(state, sci_result, current_phase=""):
                return state

            # Step 1: Phase Resolution (before full data load by contract)
            source_preview, target_preview = self._load_phase_hint_records(
                config.source_data_path,
                config.target_data_path,
            )
            phase_info = resolve_phase(
                config.phase,
                source_records=source_preview,
                target_records=target_preview,
            )
            target_phase_info = resolve_target_phase(phase_info.phase, config.target_phase)
            state.set_phase_context(
                source_phase=phase_info.phase,
                target_phase=target_phase_info.phase,
                sst_source=phase_info.sst_source,
                sst_target=target_phase_info.sst_source,
            )

            # Step 2: Data Load (stub-capable)
            source_records = self._load_dataset(config.source_data_path, label="source")
            target_records = self._load_dataset(config.target_data_path, label="target")
            if not target_records:
                target_records = [dict(item) for item in source_records]

            state.data_stats["source_records_loaded"] = len(source_records)
            state.data_stats["target_records_loaded"] = len(target_records)

            # Step 3: Normalization + identity events
            identity_events = self._load_optional_list(config.identity_events_path)
            source_records, source_event_conflicts = apply_ip_change_events(source_records, identity_events)
            target_records, target_event_conflicts = apply_ip_change_events(target_records, identity_events)
            identity_event_conflicts = source_event_conflicts + target_event_conflicts

            # Step 4: Parity Calculation
            parity = calculate_parity(
                source_records,
                target_records,
                max_global_drift=config.max_global_drift,
            )

            if identity_event_conflicts:
                parity.metrics.identity_conflicts.extend(identity_event_conflicts)
                parity.metrics.identity_conflicts_total = len(parity.metrics.identity_conflicts)
                parity.metrics.hard_drift_count += len(identity_event_conflicts)
                parity.metrics.deterministic_hash = self._rehash_deterministic_metrics(parity.metrics)

            state.set_parity_metrics(parity.metrics)
            state.data_stats["normalized_source_records"] = len(parity.normalized_source)
            state.data_stats["normalized_target_records"] = len(parity.normalized_target)

            tracker_history = load_history(config.parity_history_path)
            tracker = ParityStabilityTracker(tracker_history)
            parity_pass = (
                parity.metrics.identity_conflicts_total == 0
                and parity.metrics.global_drift_pct < config.max_global_drift
                and parity.metrics.hard_drift_count == 0
            )
            tracker.record_run(
                timestamp=state.started_at,
                parity_pass=parity_pass,
                deterministic_hash=parity.metrics.deterministic_hash,
            )
            tracker_summary = tracker.evaluate(
                required_consecutive_passes=config.required_consecutive_parity,
                window_days=config.window_days,
                now=state.started_at,
            )
            state.tracker_summary = tracker_summary
            save_history(config.parity_history_path, tracker.history)

            # Step 5: Gate execution in strict runtime order
            emv_result = gates.run_emv_gate(self.root, config.python_executable)
            state.add_gate_result(emv_result)
            if self._should_stop_on_gate_failure(state, emv_result, current_phase=phase_info.phase):
                return state

            ci_result = gates.run_ci_gate(self.root, config.python_executable)
            state.add_gate_result(ci_result)
            if self._should_stop_on_gate_failure(state, ci_result, current_phase=phase_info.phase):
                return state

            auth_result = gates.run_auth_gate(phase_info)
            state.add_gate_result(auth_result)
            if self._should_stop_on_gate_failure(state, auth_result, current_phase=phase_info.phase):
                return state

            governance_result = gates.run_governance_gate(
                phase_info=phase_info,
                execution_mode=config.execution_mode,
                governance_approved=config.governance_approved,
            )
            state.add_gate_result(governance_result)
            if self._should_stop_on_gate_failure(state, governance_result, current_phase=phase_info.phase):
                return state

            drift_result = gates.run_drift_gate(
                metrics=parity.metrics,
                max_global_drift=config.max_global_drift,
            )
            state.add_gate_result(drift_result)
            if self._should_stop_on_gate_failure(state, drift_result, current_phase=phase_info.phase):
                return state

            enforce_cutover_stability = phase_info.phase == PHASE_2 and target_phase_info.phase == PHASE_3
            parity_gate_result = gates.run_parity_gate(
                metrics=parity.metrics,
                max_global_drift=config.max_global_drift,
                tracker_summary=tracker_summary,
                critical_events_180d=config.critical_events_180d,
                enforce_cutover_stability=enforce_cutover_stability,
            )
            state.add_gate_result(parity_gate_result)
            if self._should_stop_on_gate_failure(state, parity_gate_result, current_phase=phase_info.phase):
                return state

            # Step 6: Decision Engine
            state.critical_events_180d = int(config.critical_events_180d)
            decision, exit_code, reason = self._evaluate_cutover(
                current_phase=phase_info.phase,
                target_phase=target_phase_info.phase,
                tracker_summary=tracker_summary,
                global_drift_pct=parity.metrics.global_drift_pct,
                max_global_drift=config.max_global_drift,
                identity_conflicts_total=parity.metrics.identity_conflicts_total,
                critical_events_180d=int(config.critical_events_180d),
            )

            if exit_code == EXIT_SUCCESS:
                state.decision = decision
                state.mark_success(reason=reason)
            elif exit_code == EXIT_ROLLBACK_TRIGGERED:
                state.mark_failure(
                    exit_code=EXIT_ROLLBACK_TRIGGERED,
                    reason=reason,
                    decision=decision,
                )
            else:
                state.mark_failure(
                    exit_code=EXIT_GATE_FAILURE,
                    reason=reason,
                    decision=decision,
                )

            return state

        except Exception as exc:
            state.errors.append(str(exc))
            state.mark_failure(
                exit_code=EXIT_HARD_FAIL,
                reason=f"HARD_FAIL:{exc}",
                decision="HARD_FAIL",
            )
            return state

    def _load_dataset(self, path: str | None, *, label: str) -> List[Dict[str, Any]]:
        if not path:
            return []

        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = (self.root / file_path).resolve()

        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError(f"{label} dataset missing: {file_path}")

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            records = payload.get("records")
            if isinstance(records, list):
                return [item for item in records if isinstance(item, dict)]
        raise RuntimeError(f"{label} dataset must be JSON list or object containing records[]")

    def _load_phase_hint_records(
        self,
        source_path: str | None,
        target_path: str | None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        source_preview = self._read_dataset_preview(source_path)
        target_preview = self._read_dataset_preview(target_path)
        return source_preview, target_preview

    def _read_dataset_preview(self, path: str | None) -> List[Dict[str, Any]]:
        if not path:
            return []

        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = (self.root / file_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            return []

        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        records: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
            records = [item for item in payload["records"] if isinstance(item, dict)]

        return records[:25]

    def _load_optional_list(self, path: str | None) -> List[Dict[str, Any]]:
        if not path:
            return []

        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = (self.root / file_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError(f"identity events file missing: {file_path}")

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("identity events file must be a JSON list")
        return [item for item in payload if isinstance(item, dict)]

    def _rehash_deterministic_metrics(self, metrics: Any) -> str:
        payload = {
            "compared_records_total": metrics.compared_records_total,
            "drifted_records_total": metrics.drifted_records_total,
            "global_drift_pct": metrics.global_drift_pct,
            "hard_drift_count": metrics.hard_drift_count,
            "soft_drift_count": metrics.soft_drift_count,
            "identity_conflicts": metrics.identity_conflicts,
            "drift_details": metrics.drift_details,
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def _should_stop_on_gate_failure(
        self,
        state: RunState,
        gate_result: GateResult,
        *,
        current_phase: str,
    ) -> bool:
        if gate_result.status == "PASS":
            return False

        if current_phase == PHASE_3 and gate_result.gate in {"PARITY", "DRIFT"}:
            state.mark_failure(
                exit_code=EXIT_ROLLBACK_TRIGGERED,
                reason=f"{gate_result.gate}:{gate_result.reason}",
                decision="ROLLBACK_TRIGGERED",
            )
            return True

        if gate_result.severity == "HARD":
            state.mark_failure(
                exit_code=EXIT_HARD_FAIL,
                reason=f"{gate_result.gate}:{gate_result.reason}",
                decision="GATE_HARD_FAIL",
            )
            return True

        state.mark_failure(
            exit_code=EXIT_GATE_FAILURE,
            reason=f"{gate_result.gate}:{gate_result.reason}",
            decision="GATE_FAIL",
        )
        return True

    def _evaluate_cutover(
        self,
        *,
        current_phase: str,
        target_phase: str,
        tracker_summary: Dict[str, Any],
        global_drift_pct: float,
        max_global_drift: float,
        identity_conflicts_total: int,
        critical_events_180d: int,
    ) -> Tuple[str, int, str]:
        if current_phase == target_phase:
            return "CONTINUE_CURRENT_PHASE", EXIT_SUCCESS, "No phase transition requested"

        if current_phase == "PHASE_1" and target_phase == "PHASE_2":
            if identity_conflicts_total > 0:
                return "CUTOVER_DENIED", EXIT_GATE_FAILURE, "Identity conflicts prevent PHASE_1->PHASE_2"
            return "CUTOVER_APPROVED", EXIT_SUCCESS, "PHASE_1->PHASE_2 approved"

        if current_phase == "PHASE_2" and target_phase == "PHASE_3":
            gate_a = bool(tracker_summary.get("pass", False))
            gate_b = float(global_drift_pct) < float(max_global_drift)
            gate_c = int(critical_events_180d) == 0
            gate_d = int(identity_conflicts_total) == 0

            if gate_a and gate_b and gate_c and gate_d:
                return "CUTOVER_APPROVED", EXIT_SUCCESS, "PHASE_2->PHASE_3 approved"

            failed = []
            if not gate_a:
                failed.append("GATE_A_PARITY_STABILITY")
            if not gate_b:
                failed.append("GATE_B_DRIFT_THRESHOLD")
            if not gate_c:
                failed.append("GATE_C_STABILITY_CONSTRAINT")
            if not gate_d:
                failed.append("GATE_D_IDENTITY_STABILITY")
            return "CUTOVER_DENIED", EXIT_GATE_FAILURE, "Failed:" + ",".join(failed)

        if current_phase == "PHASE_3" and target_phase in {"PHASE_1", "PHASE_2"}:
            return "ROLLBACK_TRIGGERED", EXIT_ROLLBACK_TRIGGERED, "Rollback from PHASE_3 to prior phase"

        return "CUTOVER_DENIED", EXIT_GATE_FAILURE, "Unsupported phase transition"
