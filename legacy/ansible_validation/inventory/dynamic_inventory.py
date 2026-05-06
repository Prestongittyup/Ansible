#!/usr/bin/env python3
"""Sprint 2 dynamic inventory loader.

Reads Sprint 1 inventory output and extracts IP targets only.
No enrichment logic is performed.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


class InventoryLoadError(ValueError):
    """Raised when Sprint 1 inventory is missing or invalid."""


def load_ip_targets(inventory_path: Path) -> List[str]:
    """Load and validate IP-only targets from Sprint 1 output."""
    if not inventory_path.exists() or not inventory_path.is_file():
        raise InventoryLoadError("inventory output file missing")

    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise InventoryLoadError(f"inventory output json parse failed: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise InventoryLoadError("inventory output root must be an object")

    devices = payload.get("devices")
    if not isinstance(devices, list):
        raise InventoryLoadError("inventory output devices must be a list")

    targets: List[str] = []
    seen: Dict[str, bool] = {}

    for index, raw in enumerate(devices):
        if not isinstance(raw, Mapping):
            raise InventoryLoadError(f"devices[{index}] must be an object")

        ip_raw = raw.get("ip_address")
        if not isinstance(ip_raw, str) or not ip_raw.strip():
            raise InventoryLoadError(f"devices[{index}] missing ip_address")

        ip_clean = ip_raw.strip()
        try:
            ip_normalized = str(ipaddress.ip_address(ip_clean))
        except ValueError as exc:
            raise InventoryLoadError(f"devices[{index}] invalid ip_address: {ip_clean}") from exc

        if ip_normalized not in seen:
            seen[ip_normalized] = True
            targets.append(ip_normalized)

    return targets


def _to_ansible_inventory(hosts: List[str]) -> Dict[str, Any]:
    return {
        "all": {
            "hosts": hosts,
            "children": {},
            "vars": {},
        }
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sprint 2 dynamic inventory extractor")
    parser.add_argument(
        "--input",
        default="inventory_output.json",
        help="Path to Sprint 1 inventory output",
    )
    parser.add_argument(
        "--format",
        choices=["list", "ansible-json"],
        default="list",
        help="Output format",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        hosts = load_ip_targets(Path(args.input))
    except InventoryLoadError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 2

    if args.format == "ansible-json":
        print(json.dumps(_to_ansible_inventory(hosts), indent=2, sort_keys=True))
    else:
        print(json.dumps({"status": "PASS", "hosts": hosts}, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
