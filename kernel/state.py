from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Optional

EXIT_SUCCESS = 0
EXIT_GATE_FAILURE = 20
EXIT_HARD_FAIL = 30
EXIT_ROLLBACK_TRIGGERED = 40


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_run_id(kernel_anchor: str, started_at: str) -> str:
    token = f"{kernel_anchor}:{started_at}".encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:12]
    return f"system-enf-{digest}"


@dataclass
class GateResult:
    gate: str
    status: str
    reason: str
    severity: str = "SOFT"
    details: Dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "reason": self.reason,
            "severity": self.severity,
            "details": self.details,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class ParityMetrics:
    compared_records_total: int = 0
    drifted_records_total: int = 0
    global_drift_pct: float = 0.0
    hard_drift_count: int = 0
    soft_drift_count: int = 0
    identity_conflicts_total: int = 0
    identity_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    drift_details: List[Dict[str, Any]] = field(default_factory=list)
    deterministic_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compared_records_total": self.compared_records_total,
            "drifted_records_total": self.drifted_records_total,
            "global_drift_pct": self.global_drift_pct,
            "hard_drift_count": self.hard_drift_count,
            "soft_drift_count": self.soft_drift_count,
            "identity_conflicts_total": self.identity_conflicts_total,
            "identity_conflicts": self.identity_conflicts,
            "drift_details": self.drift_details,
            "deterministic_hash": self.deterministic_hash,
        }


@dataclass
class RunConfig:
    phase: str
    target_phase: str
    window_days: int
    required_consecutive_parity: int
    max_global_drift: float
    fail_closed: bool
    audit_path: str
    execution_mode: str = "READ_ONLY"
    governance_approved: bool = False
    source_data_path: Optional[str] = None
    target_data_path: Optional[str] = None
    identity_events_path: Optional[str] = None
    parity_history_path: Optional[str] = None
    critical_events_180d: int = 0
    root_path: str = "."
    python_executable: str = "python"


@dataclass
class RunState:
    kernel_anchor: str
    fail_closed: bool
    started_at: str = field(default_factory=utc_now_iso)
    run_id: str = ""
    finished_at: Optional[str] = None

    source_phase: str = ""
    target_phase: str = ""
    sst_source: str = ""
    sst_target: str = ""

    gate_sequence_expected: List[str] = field(default_factory=list)
    gate_results: List[GateResult] = field(default_factory=list)
    parity_metrics: ParityMetrics = field(default_factory=ParityMetrics)

    tracker_summary: Dict[str, Any] = field(default_factory=dict)
    data_stats: Dict[str, Any] = field(default_factory=dict)

    decision: str = "UNSET"
    decision_reason: str = ""
    exit_code: int = EXIT_HARD_FAIL
    exit_code_reason: str = "UNINITIALIZED"

    critical_events_180d: int = 0
    errors: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = generate_run_id(self.kernel_anchor, self.started_at)

    def add_gate_result(self, result: GateResult) -> None:
        self.gate_results.append(result)

    def set_parity_metrics(self, metrics: ParityMetrics) -> None:
        self.parity_metrics = metrics

    def set_phase_context(
        self,
        *,
        source_phase: str,
        target_phase: str,
        sst_source: str,
        sst_target: str,
    ) -> None:
        self.source_phase = source_phase
        self.target_phase = target_phase
        self.sst_source = sst_source
        self.sst_target = sst_target

    def mark_failure(self, *, exit_code: int, reason: str, decision: str = "FAILED") -> None:
        self.decision = decision
        self.decision_reason = reason
        self.exit_code = exit_code
        self.exit_code_reason = reason
        self.finished_at = utc_now_iso()

    def mark_success(self, reason: str = "SUCCESS") -> None:
        self.decision = "SUCCESS"
        self.decision_reason = reason
        self.exit_code = EXIT_SUCCESS
        self.exit_code_reason = reason
        self.finished_at = utc_now_iso()

    def ensure_finished(self) -> None:
        if self.finished_at is None:
            self.finished_at = utc_now_iso()

    @classmethod
    def bootstrap_failure(cls, *, kernel_anchor: str, reason: str, fail_closed: bool = True) -> "RunState":
        state = cls(kernel_anchor=kernel_anchor, fail_closed=fail_closed)
        state.mark_failure(exit_code=EXIT_HARD_FAIL, reason=reason, decision="BOOTSTRAP_FAILURE")
        state.errors.append(reason)
        return state
