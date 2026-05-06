from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

PHASE_1 = "PHASE_1"
PHASE_2 = "PHASE_2"
PHASE_3 = "PHASE_3"
AUTO = "AUTO"


@dataclass(frozen=True)
class PhaseInfo:
    phase: str
    sst_source: str
    writable_systems: tuple[str, ...]
    execution_role: str


_PHASE_MAP = {
    PHASE_1: PhaseInfo(
        phase=PHASE_1,
        sst_source="LOGICMONITOR",
        writable_systems=("LOGICMONITOR",),
        execution_role="DISCOVERY_ONLY",
    ),
    PHASE_2: PhaseInfo(
        phase=PHASE_2,
        sst_source="ANSIBLE_POSTGRES",
        writable_systems=("ANSIBLE", "POSTGRES"),
        execution_role="INVENTORY_AUTHORITY",
    ),
    PHASE_3: PhaseInfo(
        phase=PHASE_3,
        sst_source="NAUTOBOT",
        writable_systems=("NAUTOBOT",),
        execution_role="EXECUTION_AND_VALIDATION_ONLY",
    ),
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_phase_token(value: str) -> str:
    token = str(value).strip().upper()
    aliases = {
        "AUTO": AUTO,
        "PHASE1": PHASE_1,
        "PHASE_1": PHASE_1,
        "1": PHASE_1,
        "PHASE2": PHASE_2,
        "PHASE_2": PHASE_2,
        "2": PHASE_2,
        "PHASE3": PHASE_3,
        "PHASE_3": PHASE_3,
        "3": PHASE_3,
    }
    if token not in aliases:
        raise ValueError(f"Invalid phase token: {value}")
    return aliases[token]


def _iter_records(*datasets: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for dataset in datasets:
        for record in dataset:
            if isinstance(record, dict):
                yield record


def resolve_phase(
    requested_phase: str,
    *,
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
) -> PhaseInfo:
    token = normalize_phase_token(requested_phase)
    if token != AUTO:
        return _PHASE_MAP[token]

    for record in _iter_records(source_records, target_records):
        if _truthy(record.get("nautobot_authoritative")):
            return _PHASE_MAP[PHASE_3]
        if str(record.get("sst_source", "")).strip().upper() == "NAUTOBOT":
            return _PHASE_MAP[PHASE_3]

    for record in _iter_records(source_records, target_records):
        if _truthy(record.get("ansible_validated")):
            return _PHASE_MAP[PHASE_2]
        if _truthy(record.get("postgres_canonical")):
            return _PHASE_MAP[PHASE_2]
        if str(record.get("sst_source", "")).strip().upper() in {"ANSIBLE", "POSTGRES", "ANSIBLE_POSTGRES"}:
            return _PHASE_MAP[PHASE_2]

    return _PHASE_MAP[PHASE_1]


def resolve_target_phase(current_phase: str, requested_target_phase: str) -> PhaseInfo:
    token = normalize_phase_token(requested_target_phase)
    if token == AUTO:
        return _PHASE_MAP[current_phase]

    if token == current_phase:
        return _PHASE_MAP[token]

    valid_forward = {
        (PHASE_1, PHASE_2),
        (PHASE_2, PHASE_3),
    }
    if (current_phase, token) not in valid_forward:
        raise ValueError(
            f"Invalid target phase transition: current={current_phase}, target={token}"
        )
    return _PHASE_MAP[token]


def phase_info_for(phase: str) -> PhaseInfo:
    return _PHASE_MAP[normalize_phase_token(phase)]
