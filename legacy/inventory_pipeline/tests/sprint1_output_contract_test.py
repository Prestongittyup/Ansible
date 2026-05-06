#!/usr/bin/env python3
"""Sprint 1 Output Contract Test.

Validates Sprint 1 ingestion output artifact integrity and contract compliance.
Scope is limited to offline JSON artifact validation.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ALLOWED_VENDORS = {"aruba", "cisco", "fortinet", "juniper", "meraki", "unknown"}
REQUIRED_TOP_LEVEL_KEYS = {"metadata", "summary", "devices"}
REQUIRED_RECORD_FIELDS = {"ip_address", "vendor", "source"}
KNOWN_RECORD_FIELDS = {"ip_address", "vendor", "source"}


def _new_category_result() -> Dict[str, Any]:
    return {
        "status": "PASS",
        "violations": [],
    }


def _add_violation(category: Dict[str, Any], record_ref: str, reason: str) -> None:
    category["status"] = "FAIL"
    category["violations"].append({"record": record_ref, "reason": reason})


def _load_json(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    json_validity = _new_category_result()

    if not path.exists() or not path.is_file():
        _add_violation(json_validity, "file", "output file missing")
        return {}, json_validity

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _add_violation(json_validity, "file", f"invalid json: {exc}")
        return {}, json_validity

    if not isinstance(parsed, dict):
        _add_violation(json_validity, "root", "json root must be an object")
        return {}, json_validity

    return parsed, json_validity


def _validate_schema(payload: Mapping[str, Any]) -> Dict[str, Any]:
    schema_check = _new_category_result()

    root_keys = set(payload.keys())
    missing_root_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - root_keys)
    extra_root_keys = sorted(root_keys - REQUIRED_TOP_LEVEL_KEYS)

    if missing_root_keys:
        _add_violation(schema_check, "root", f"missing top-level keys: {missing_root_keys}")

    if extra_root_keys:
        _add_violation(schema_check, "root", f"unexpected top-level keys: {extra_root_keys}")

    devices = payload.get("devices")
    if not isinstance(devices, list):
        _add_violation(schema_check, "root.devices", "devices must be a list")
        return schema_check

    for index, record in enumerate(devices):
        record_ref = f"devices[{index}]"
        if not isinstance(record, dict):
            _add_violation(schema_check, record_ref, "record must be an object")
            continue

        record_keys = set(record.keys())
        missing_fields = sorted(REQUIRED_RECORD_FIELDS - record_keys)
        unexpected_fields = sorted(record_keys - KNOWN_RECORD_FIELDS)

        if missing_fields:
            _add_violation(schema_check, record_ref, f"missing required fields: {missing_fields}")

        if unexpected_fields:
            _add_violation(schema_check, record_ref, f"unexpected fields: {unexpected_fields}")

    return schema_check


def _validate_identity(payload: Mapping[str, Any]) -> Dict[str, Any]:
    identity_check = _new_category_result()
    devices = payload.get("devices")

    if not isinstance(devices, list):
        _add_violation(identity_check, "root.devices", "devices must be a list for identity validation")
        return identity_check

    for index, record in enumerate(devices):
        record_ref = f"devices[{index}]"
        if not isinstance(record, dict):
            _add_violation(identity_check, record_ref, "record must be an object")
            continue

        ip_value = record.get("ip_address")
        if not isinstance(ip_value, str) or not ip_value.strip():
            _add_violation(identity_check, record_ref, "ip_address missing or empty")
            continue

        try:
            ipaddress.ip_address(ip_value.strip())
        except ValueError:
            _add_violation(identity_check, record_ref, f"invalid ip_address: {ip_value}")

    return identity_check


def _validate_vendor(payload: Mapping[str, Any]) -> Dict[str, Any]:
    vendor_check = _new_category_result()
    devices = payload.get("devices")

    if not isinstance(devices, list):
        _add_violation(vendor_check, "root.devices", "devices must be a list for vendor validation")
        return vendor_check

    for index, record in enumerate(devices):
        record_ref = f"devices[{index}]"
        if not isinstance(record, dict):
            _add_violation(vendor_check, record_ref, "record must be an object")
            continue

        vendor_raw = record.get("vendor")
        if not isinstance(vendor_raw, str) or not vendor_raw.strip():
            _add_violation(vendor_check, record_ref, "vendor missing or empty")
            continue

        vendor = vendor_raw.strip().lower()
        if vendor not in ALLOWED_VENDORS:
            _add_violation(vendor_check, record_ref, f"vendor not allowed: {vendor_raw}")

    return vendor_check


def run_contract_test(output_path: Path) -> Dict[str, Any]:
    payload, json_validity_check = _load_json(output_path)

    if json_validity_check["status"] == "FAIL":
        result = {
            "overall_result": "FAIL",
            "identity_check": _new_category_result(),
            "vendor_check": _new_category_result(),
            "schema_check": _new_category_result(),
            "json_validity_check": json_validity_check,
        }
        result["identity_check"]["status"] = "FAIL"
        result["vendor_check"]["status"] = "FAIL"
        result["schema_check"]["status"] = "FAIL"
        return result

    schema_check = _validate_schema(payload)
    identity_check = _validate_identity(payload)
    vendor_check = _validate_vendor(payload)

    checks = [identity_check, vendor_check, schema_check, json_validity_check]
    overall = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"

    return {
        "overall_result": overall,
        "identity_check": identity_check,
        "vendor_check": vendor_check,
        "schema_check": schema_check,
        "json_validity_check": json_validity_check,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sprint 1 output contract test")
    parser.add_argument(
        "--input",
        default="inventory_output.json",
        help="Path to Sprint 1 output artifact",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_contract_test(Path(args.input))
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["overall_result"] == "PASS":
        print("PASS")
        return 0

    print("FAIL")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
