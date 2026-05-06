from __future__ import annotations

import copy
from typing import Any, Dict, List

# Deterministic fixture set. These values must not be randomized.
_BASE_INVENTORY: List[Dict[str, Any]] = [
    {
        "ip_address": "10.10.0.1",
        "hostname": "edge-aruba-1",
        "serial_number": "ARUBA-0001",
        "vendor": "Aruba",
        "model": "AOS-CX",
        "site": "dc1",
        "status": "up",
        "authority_owner": "ANSIBLE_POSTGRES",
    },
    {
        "ip_address": "10.10.0.2",
        "hostname": "core-cisco-1",
        "serial_number": "CISCO-0002",
        "vendor": "Cisco",
        "model": "IOS-XE",
        "site": "dc1",
        "status": "up",
        "authority_owner": "ANSIBLE_POSTGRES",
    },
    {
        "ip_address": "10.10.0.3",
        "hostname": "fw-fortinet-1",
        "serial_number": "FORTINET-0003",
        "vendor": "Fortinet",
        "model": "FortiGate",
        "site": "dc2",
        "status": "up",
        "authority_owner": "ANSIBLE_POSTGRES",
    },
]


def _deepcopy_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return copy.deepcopy(records)


def logicmonitor_discovery_records() -> List[Dict[str, Any]]:
    records = _deepcopy_records(_BASE_INVENTORY)
    for record in records:
        record["authority_owner"] = "LOGICMONITOR"
        record["sst_source"] = "LOGICMONITOR"
    return records


def ansible_validated_inventory_records() -> List[Dict[str, Any]]:
    records = _deepcopy_records(_BASE_INVENTORY)
    for record in records:
        record["ansible_validated"] = True
        record["sst_source"] = "ANSIBLE_POSTGRES"
    return records


def postgres_canonical_records_for_phase2() -> List[Dict[str, Any]]:
    records = _deepcopy_records(_BASE_INVENTORY)
    for record in records:
        record["postgres_canonical"] = True
        record["sst_source"] = "ANSIBLE_POSTGRES"
    return records


def nautobot_authoritative_records_for_phase3() -> List[Dict[str, Any]]:
    records = _deepcopy_records(_BASE_INVENTORY)
    for record in records:
        record["authority_owner"] = "NAUTOBOT"
        record["nautobot_authoritative"] = True
        record["sst_source"] = "NAUTOBOT"
    return records


def postgres_mirror_records_for_phase3() -> List[Dict[str, Any]]:
    records = nautobot_authoritative_records_for_phase3()
    for record in records:
        record["postgres_canonical"] = True
    return records


def identity_change_events() -> List[Dict[str, Any]]:
    return []
