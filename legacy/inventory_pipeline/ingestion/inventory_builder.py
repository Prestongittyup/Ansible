"""Inventory builder for Sprint 1 ingestion output."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class InventoryBuildResult:
    """Output inventory payload and duplicate counters."""

    inventory: Dict[str, Any]
    duplicate_ip_count: int


def build_inventory_json(
    records: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    logger: Optional[logging.Logger] = None,
) -> InventoryBuildResult:
    """Build JSON-ready inventory keyed by IP address."""
    log = logger or logging.getLogger(__name__)

    deduplicated: Dict[str, Dict[str, Any]] = {}
    duplicate_ip_count = 0
    vendor_counts: Dict[str, int] = {}

    for record in records:
        ip_address = str(record.get("ip_address", "")).strip()
        if not ip_address:
            continue

        if ip_address in deduplicated:
            duplicate_ip_count += 1
            continue

        attributes_raw = record.get("attributes")
        attributes = dict(attributes_raw) if isinstance(attributes_raw, Mapping) else {}
        vendor = str(attributes.get("vendor", "Unknown"))
        vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1

        deduplicated[ip_address] = {
            "ip_address": ip_address,
            "device_id": str(attributes.get("device_id", "")),
            "display_name": str(attributes.get("display_name", "")),
            "vendor": vendor,
            "sys_descr": str(attributes.get("sys_descr", "")),
            "sys_object_id": str(attributes.get("sys_object_id", "")),
        }

    devices = [deduplicated[ip] for ip in sorted(deduplicated)]
    generated_at = datetime.now(timezone.utc).isoformat()

    inventory = {
        "metadata": {
            "run_id": run_id,
            "generated_at": generated_at,
            "source": "LogicMonitor",
            "identity_key": "ip_address",
        },
        "summary": {
            "total_records": len(devices),
            "duplicate_ip_discarded": duplicate_ip_count,
            "vendor_counts": vendor_counts,
        },
        "devices": devices,
    }

    log.info(
        json.dumps(
            {
                "event": "inventory_build_complete",
                "stage": "inventory_builder",
                "run_id": run_id,
                "total_records": len(devices),
                "duplicate_ip_discarded": duplicate_ip_count,
                "vendor_counts": vendor_counts,
            },
            sort_keys=True,
        )
    )

    return InventoryBuildResult(inventory=inventory, duplicate_ip_count=duplicate_ip_count)
