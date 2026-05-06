from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


_EXPECTED_SST = {
    "PHASE_1": "LOGICMONITOR",
    "PHASE_2": "ANSIBLE_POSTGRES",
    "PHASE_3": "NAUTOBOT",
}


@dataclass(frozen=True)
class CutoverSimulationResult:
    requested: bool
    current_phase: str
    target_phase: str
    status: str
    reason: str
    authority_switch_valid: bool
    checks: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "current_phase": self.current_phase,
            "target_phase": self.target_phase,
            "status": self.status,
            "reason": self.reason,
            "authority_switch_valid": self.authority_switch_valid,
            "checks": dict(self.checks),
        }


def simulate_cutover(
    *,
    current_phase: str,
    target_phase: str,
    sst_source: str,
    sst_target: str,
    identity_conflicts_total: int,
    global_drift_pct: float,
    max_global_drift: float,
    tracker_summary: Dict[str, Any],
) -> CutoverSimulationResult:
    source_expected = _EXPECTED_SST.get(current_phase, "")
    target_expected = _EXPECTED_SST.get(target_phase, "")
    authority_switch_valid = (source_expected == sst_source) and (target_expected == sst_target)

    if current_phase == target_phase:
        return CutoverSimulationResult(
            requested=False,
            current_phase=current_phase,
            target_phase=target_phase,
            status="NOT_REQUESTED",
            reason="No phase transition requested",
            authority_switch_valid=authority_switch_valid,
            checks={
                "authority_switch_valid": authority_switch_valid,
            },
        )

    if (current_phase, target_phase) == ("PHASE_1", "PHASE_2"):
        checks = {
            "authority_switch_valid": authority_switch_valid,
            "identity_stable": int(identity_conflicts_total) == 0,
            "drift_within_threshold": float(global_drift_pct) < float(max_global_drift),
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        reason = "PHASE_1_TO_PHASE_2_SIMULATION_PASS" if status == "PASS" else "PHASE_1_TO_PHASE_2_SIMULATION_FAIL"
        return CutoverSimulationResult(
            requested=True,
            current_phase=current_phase,
            target_phase=target_phase,
            status=status,
            reason=reason,
            authority_switch_valid=authority_switch_valid,
            checks=checks,
        )

    if (current_phase, target_phase) == ("PHASE_2", "PHASE_3"):
        checks = {
            "authority_switch_valid": authority_switch_valid,
            "identity_stable": int(identity_conflicts_total) == 0,
            "drift_within_threshold": float(global_drift_pct) < float(max_global_drift),
            "parity_stability_met": bool(tracker_summary.get("pass", False)),
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        reason = "PHASE_2_TO_PHASE_3_SIMULATION_PASS" if status == "PASS" else "PHASE_2_TO_PHASE_3_SIMULATION_FAIL"
        return CutoverSimulationResult(
            requested=True,
            current_phase=current_phase,
            target_phase=target_phase,
            status=status,
            reason=reason,
            authority_switch_valid=authority_switch_valid,
            checks=checks,
        )

    return CutoverSimulationResult(
        requested=True,
        current_phase=current_phase,
        target_phase=target_phase,
        status="FAIL",
        reason="UNSUPPORTED_CUTOVER_PATH",
        authority_switch_valid=authority_switch_valid,
        checks={
            "authority_switch_valid": authority_switch_valid,
            "supported_transition": False,
        },
    )
