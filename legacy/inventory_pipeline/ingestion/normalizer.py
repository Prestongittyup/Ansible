"""Normalization layer for Sprint 1 ingestion.

Enforces IP-only identity and drops records without valid IP addresses.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class NormalizationResult:
    """Normalization output and drop statistics."""

    records: List[Dict[str, Any]]
    input_records: int
    dropped_missing_ip: int
    dropped_invalid_ip: int


def normalize_logicmonitor_devices(
    raw_devices: Iterable[Mapping[str, Any]],
    logger: Optional[logging.Logger] = None,
) -> NormalizationResult:
    """Normalize raw LogicMonitor payloads into IP-keyed canonical records."""
    log = logger or logging.getLogger(__name__)

    normalized: List[Dict[str, Any]] = []
    dropped_missing_ip = 0
    dropped_invalid_ip = 0
    input_records = 0

    for raw in raw_devices:
        input_records += 1

        ip_raw = raw.get("ip")
        if not isinstance(ip_raw, str) or not ip_raw.strip():
            dropped_missing_ip += 1
            log.info(
                json.dumps(
                    {
                        "event": "record_dropped",
                        "stage": "normalization",
                        "reason": "missing_ip",
                        "source_device_id": str(raw.get("id", "")),
                    },
                    sort_keys=True,
                )
            )
            continue

        ip_clean = ip_raw.strip()
        try:
            ip_address = str(ipaddress.ip_address(ip_clean))
        except ValueError:
            dropped_invalid_ip += 1
            log.info(
                json.dumps(
                    {
                        "event": "record_dropped",
                        "stage": "normalization",
                        "reason": "invalid_ip",
                        "source_device_id": str(raw.get("id", "")),
                        "ip": ip_clean,
                    },
                    sort_keys=True,
                )
            )
            continue

        system_properties = raw.get("systemProperties")
        if not isinstance(system_properties, Mapping):
            system_properties = {}

        record: Dict[str, Any] = {
            "ip_address": ip_address,
            "attributes": {
                "device_id": str(raw.get("id", "")),
                "display_name": str(raw.get("displayName", "")),
                "sys_descr": str(system_properties.get("system.sysDescr", "")),
                "sys_object_id": str(system_properties.get("system.sysObjectID", "")),
            },
        }
        normalized.append(record)

    log.info(
        json.dumps(
            {
                "event": "normalization_complete",
                "stage": "normalization",
                "input_records": input_records,
                "valid_records": len(normalized),
                "dropped_missing_ip": dropped_missing_ip,
                "dropped_invalid_ip": dropped_invalid_ip,
            },
            sort_keys=True,
        )
    )

    return NormalizationResult(
        records=normalized,
        input_records=input_records,
        dropped_missing_ip=dropped_missing_ip,
        dropped_invalid_ip=dropped_invalid_ip,
    )
