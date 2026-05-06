"""Adapter and phase ownership contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


ADAPTER_PHASE_OWNERSHIP_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "phase_source_of_truth": {
        "PHASE_1": "LOGICMONITOR",
        "PHASE_2": "ANSIBLE_POSTGRES",
        "PHASE_3": "NAUTOBOT",
    },
    "phase_writable_systems": {
        "PHASE_1": ["LOGICMONITOR"],
        "PHASE_2": ["ANSIBLE", "POSTGRES"],
        "PHASE_3": ["NAUTOBOT", "POSTGRES"],
    },
}


def _validate_adapter_phase_ownership(payload: Mapping[str, Any]) -> None:
    mapping = payload.get("phase_source_of_truth")
    writable = payload.get("phase_writable_systems")

    if not isinstance(mapping, Mapping):
        raise ValueError("ADAPTER_PHASE_OWNERSHIP requires phase_source_of_truth mapping")
    if not isinstance(writable, Mapping):
        raise ValueError("ADAPTER_PHASE_OWNERSHIP requires phase_writable_systems mapping")

    for phase in ("PHASE_1", "PHASE_2", "PHASE_3"):
        if phase not in mapping:
            raise ValueError(f"ADAPTER_PHASE_OWNERSHIP missing phase in source-of-truth map: {phase}")
        systems = writable.get(phase)
        if not isinstance(systems, list) or not systems:
            raise ValueError(f"ADAPTER_PHASE_OWNERSHIP missing writable system list for {phase}")


def get_contracts() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ADAPTER_PHASE_OWNERSHIP",
            "version": "1.0",
            "payload": ADAPTER_PHASE_OWNERSHIP_CONTRACT,
            "validator": _validate_adapter_phase_ownership,
        }
    ]
