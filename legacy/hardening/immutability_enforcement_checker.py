"""Immutability checker for frozen-layer integrity and hidden-change detection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from legacy.hardening.drift_detection_engine import compare_with_baseline, validate_schema_versions

FROZEN_LAYERS = ["enforcement", "persistence", "query", "api", "auth", "governance", "observability"]


def detect_hidden_behavioral_changes(drift_report: dict[str, Any]) -> dict[str, Any]:
    flagged: list[dict[str, Any]] = []

    for layer_name, detail in sorted(drift_report.get("layers", {}).items()):
        for mismatch in detail.get("mismatches", []):
            status = mismatch.get("status")
            if status in {"CHANGED", "ADDED", "MISSING"}:
                flagged.append(
                    {
                        "layer": layer_name,
                        "path": mismatch.get("path"),
                        "status": status,
                    }
                )

    return {
        "violation_count": len(flagged),
        "violations": flagged,
        "is_clean": len(flagged) == 0,
    }


def run_immutability_check(root: Path, baseline_path: str | Path, frozen_layers: list[str] | None = None) -> dict[str, Any]:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    layers = frozen_layers or FROZEN_LAYERS

    drift = compare_with_baseline(root=root, baseline=baseline, layers_to_check=layers)
    schema = validate_schema_versions(root)
    hidden = detect_hidden_behavioral_changes(drift)

    for layer_name in layers:
        detail = drift["layers"].get(layer_name, {})
        if detail.get("mismatch_count", 0) > 0:
            hidden["is_clean"] = False

    is_clean = drift["all_unchanged"] and schema["all_consistent"] and hidden["is_clean"]
    return {
        "is_clean": is_clean,
        "drift": drift,
        "schema": schema,
        "hidden_behavioral_changes": hidden,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run immutability enforcement checks for frozen layers")
    parser.add_argument("--baseline", required=True, help="Baseline JSON path")
    parser.add_argument("--layers", default=",".join(FROZEN_LAYERS), help="Comma-separated frozen layers")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()

    layers = [item.strip() for item in args.layers.split(",") if item.strip()]
    report = run_immutability_check(Path.cwd(), args.baseline, frozen_layers=layers)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["is_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
