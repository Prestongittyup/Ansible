"""Vendor classification for Sprint 1 ingestion.

Classification is attribute-only and never used for identity decisions.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional


def classify_vendor(sys_descr: str) -> str:
    """Classify vendor from sysDescr text."""
    text = (sys_descr or "").strip().lower()

    if any(token in text for token in ("aruba", "aoscx", "procurve")):
        return "Aruba"
    if any(token in text for token in ("cisco", "ios", "nx-os", "catalyst")):
        return "Cisco"
    if any(token in text for token in ("fortinet", "fortigate")):
        return "Fortinet"
    if any(token in text for token in ("juniper", "junos")):
        return "Juniper"
    if "meraki" in text:
        return "Meraki"
    return "Unknown"


def apply_vendor_classification(
    records: Iterable[Mapping[str, Any]],
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    """Attach vendor classification to normalized records."""
    log = logger or logging.getLogger(__name__)
    classified: List[Dict[str, Any]] = []
    vendor_counts: Dict[str, int] = {}

    for record in records:
        attributes_raw = record.get("attributes")
        if isinstance(attributes_raw, Mapping):
            attributes = dict(attributes_raw)
        else:
            attributes = {}

        vendor = classify_vendor(str(attributes.get("sys_descr", "")))
        attributes["vendor"] = vendor
        vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1

        output_record: Dict[str, Any] = {
            "ip_address": str(record.get("ip_address", "")),
            "attributes": attributes,
        }
        classified.append(output_record)

    log.info(
        json.dumps(
            {
                "event": "vendor_classification_complete",
                "stage": "classification",
                "records_classified": len(classified),
                "vendor_counts": vendor_counts,
            },
            sort_keys=True,
        )
    )

    return classified
