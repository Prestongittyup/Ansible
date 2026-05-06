"""Cross-sprint drift comparison utilities for Sprint 12 stabilization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from legacy.hardening.baseline_comparison_toolkit import compare_baselines
from legacy.hardening.drift_detection_engine import build_dependency_graph, validate_dependency_graph
from legacy.hardening.system_integrity_auditor import _FORBIDDEN_DEPENDENCY_EDGES

SCHEMA_VERSION = "2.1"

DEFAULT_PROTECTED_LAYERS = [
    "enforcement",
    "persistence",
    "query",
    "api",
    "auth",
    "governance",
    "observability",
    "export",
    "hardening",
    "orchestration",
]


class CrossSprintDriftError(RuntimeError):
    """Raised when fail-closed cross-sprint drift conditions are met."""


def _schema_version(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("schema_version", ""))


def _layer_mismatch_count(layer_diff: dict[str, Any]) -> int:
    file_diffs = layer_diff.get("file_diffs", {})
    added = len(file_diffs.get("added", []))
    removed = len(file_diffs.get("removed", []))
    changed = len(file_diffs.get("changed", []))
    return added + removed + changed


def compare_cross_sprint_baselines(
    *,
    sprint10_baseline: str | Path,
    sprint11_baseline: str | Path,
    sprint12_baseline: str | Path,
    root: Path | None = None,
    protected_layers: list[str] | None = None,
) -> dict[str, Any]:
    root_path = root or Path.cwd()
    layers = protected_layers or list(DEFAULT_PROTECTED_LAYERS)

    path10 = Path(sprint10_baseline)
    path11 = Path(sprint11_baseline)
    path12 = Path(sprint12_baseline)

    diff_10_11 = compare_baselines(path10, path11)
    diff_11_12 = compare_baselines(path11, path12)
    diff_10_12 = compare_baselines(path10, path12)

    per_layer: dict[str, dict[str, Any]] = {}

    for layer in sorted(layers):
        layer_10_11 = diff_10_11.get("layer_diffs", {}).get(layer, {})
        layer_11_12 = diff_11_12.get("layer_diffs", {}).get(layer, {})
        layer_10_12 = diff_10_12.get("layer_diffs", {}).get(layer, {})

        mismatch_10_11 = _layer_mismatch_count(layer_10_11)
        mismatch_11_12 = _layer_mismatch_count(layer_11_12)
        mismatch_10_12 = _layer_mismatch_count(layer_10_12)

        per_layer[layer] = {
            "sprint10_to_sprint11_mismatch_count": mismatch_10_11,
            "sprint11_to_sprint12_mismatch_count": mismatch_11_12,
            "sprint10_to_sprint12_mismatch_count": mismatch_10_12,
            "sprint11_to_sprint12_layer_changed": bool(layer_11_12.get("layer_changed", False)),
        }

    schema_versions = {
        "sprint10": _schema_version(path10),
        "sprint11": _schema_version(path11),
        "sprint12": _schema_version(path12),
    }
    schema_drift = len({value for value in schema_versions.values()}) != 1 or list(schema_versions.values())[0] != SCHEMA_VERSION

    dependency_report = validate_dependency_graph(build_dependency_graph(root_path), _FORBIDDEN_DEPENDENCY_EDGES)

    frozen_layers = [
        "enforcement",
        "persistence",
        "query",
        "api",
        "auth",
        "governance",
        "observability",
        "export",
        "hardening",
    ]

    frozen_drift_count = sum(
        per_layer.get(layer, {}).get("sprint11_to_sprint12_mismatch_count", 0)
        for layer in frozen_layers
        if layer in per_layer
    )

    drift_score = frozen_drift_count

    report = {
        "schema_version": SCHEMA_VERSION,
        "protected_layers": sorted(layers),
        "schema_versions": schema_versions,
        "schema_drift": schema_drift,
        "dependency_drift": {
            "violation_count": int(dependency_report.get("violation_count", 0)),
            "is_valid": bool(dependency_report.get("is_valid", False)),
            "violations": list(dependency_report.get("violations", [])),
        },
        "comparisons": {
            "sprint10_to_sprint11": diff_10_11,
            "sprint11_to_sprint12": diff_11_12,
            "sprint10_to_sprint12": diff_10_12,
        },
        "per_layer": per_layer,
        "frozen_layer_drift_count": frozen_drift_count,
        "drift_score": drift_score,
    }
    return report


def enforce_zero_frozen_drift(report: dict[str, Any]) -> None:
    if bool(report.get("schema_drift", False)):
        raise CrossSprintDriftError("schema drift detected across sprint baselines")

    dependency_violations = int(report.get("dependency_drift", {}).get("violation_count", 0))
    if dependency_violations > 0:
        raise CrossSprintDriftError(f"dependency drift detected: {dependency_violations} violation(s)")

    frozen_drift_count = int(report.get("frozen_layer_drift_count", 0))
    if frozen_drift_count > 0:
        raise CrossSprintDriftError(f"frozen-layer drift detected: {frozen_drift_count} mismatch(es)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-sprint drift comparator")
    parser.add_argument("--sprint10", required=True, help="Path to sprint 10 baseline")
    parser.add_argument("--sprint11", required=True, help="Path to sprint 11 baseline")
    parser.add_argument("--sprint12", required=True, help="Path to sprint 12 baseline")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    report = compare_cross_sprint_baselines(
        sprint10_baseline=args.sprint10,
        sprint11_baseline=args.sprint11,
        sprint12_baseline=args.sprint12,
    )

    try:
        enforce_zero_frozen_drift(report)
        status = 0
    except CrossSprintDriftError:
        status = 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
