"""System integrity auditor for cross-layer coupling and gate consistency checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from legacy.hardening.baseline_comparison_toolkit import compare_baselines
from legacy.hardening.drift_detection_engine import build_dependency_graph, validate_dependency_graph

_FORBIDDEN_DEPENDENCY_EDGES: list[tuple[str, str]] = [
    ("query_layer", "api_layer"),
    ("query_layer", "auth_layer"),
    ("query_layer", "governance_layer"),
    ("persistence", "api_layer"),
    ("persistence", "auth_layer"),
    ("persistence", "governance_layer"),
    ("execution_contract", "api_layer"),
    ("execution_contract", "auth_layer"),
    ("execution_contract", "governance_layer"),
    ("ci", "api_layer"),
    ("ci", "auth_layer"),
    ("ci", "governance_layer"),
]


def check_gate_consistency(*, old_baseline: dict[str, Any], new_baseline: dict[str, Any]) -> dict[str, Any]:
    old_gate = old_baseline.get("authority_gate", {}) if isinstance(old_baseline, dict) else {}
    new_gate = new_baseline.get("execution_timestamps", {}) if isinstance(new_baseline, dict) else {}

    old_emv_status = str(old_gate.get("emv", {}).get("status", "")).upper()
    old_ci_status = str(old_gate.get("ci_kernel", {}).get("status", "")).upper()

    new_emv_status = str(new_gate.get("emv", {}).get("status", "")).upper()
    new_ci_status = str(new_gate.get("ci_kernel", {}).get("status", "")).upper()

    checks = {
        "old_emv_pass": old_emv_status == "PASS",
        "old_ci_pass": old_ci_status == "PASS",
        "new_emv_pass": new_emv_status == "PASS",
        "new_ci_pass": new_ci_status == "PASS",
    }

    consistent = all(checks.values())
    return {
        "consistent": consistent,
        "checks": checks,
        "old_status": {"emv": old_emv_status, "ci": old_ci_status},
        "new_status": {"emv": new_emv_status, "ci": new_ci_status},
    }


def detect_unauthorized_dependency_emergence(
    old_graph: dict[str, list[str]],
    new_graph: dict[str, list[str]],
    forbidden_edges: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    rules = forbidden_edges or _FORBIDDEN_DEPENDENCY_EDGES

    old_violations = validate_dependency_graph(old_graph, rules).get("violations", [])
    new_violations = validate_dependency_graph(new_graph, rules).get("violations", [])

    old_set = {(item["source"], item["target"], item["rule"]) for item in old_violations}
    new_set = {(item["source"], item["target"], item["rule"]) for item in new_violations}

    emerged = sorted(new_set - old_set)
    return {
        "violation_count_old": len(old_set),
        "violation_count_new": len(new_set),
        "new_violations": [
            {"source": item[0], "target": item[1], "rule": item[2]}
            for item in emerged
        ],
        "is_clean": len(emerged) == 0,
    }


def run_system_integrity_audit(root: Path, old_baseline_path: str | Path, new_baseline_path: str | Path) -> dict[str, Any]:
    old_baseline = json.loads(Path(old_baseline_path).read_text(encoding="utf-8"))
    new_baseline = json.loads(Path(new_baseline_path).read_text(encoding="utf-8"))

    baseline_diff = compare_baselines(old_baseline_path, new_baseline_path)
    gate_consistency = check_gate_consistency(old_baseline=old_baseline, new_baseline=new_baseline)

    old_graph = build_dependency_graph(root)
    new_graph = build_dependency_graph(root)
    dependency_emergence = detect_unauthorized_dependency_emergence(old_graph=old_graph, new_graph=new_graph)

    frozen_layers = ("enforcement", "persistence", "query", "api", "auth", "governance", "observability")
    frozen_layer_changed = any(
        bool(baseline_diff["layer_diffs"].get(layer, {}).get("layer_changed"))
        for layer in frozen_layers
        if layer in baseline_diff["layer_diffs"]
    )

    return {
        "gate_consistency": gate_consistency,
        "dependency_emergence": dependency_emergence,
        "baseline_diff": baseline_diff,
        "frozen_layer_changed": frozen_layer_changed,
        "is_clean": gate_consistency["consistent"] and dependency_emergence["is_clean"] and (not frozen_layer_changed),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run system integrity audit between two baselines")
    parser.add_argument("--old", required=True, help="Old baseline path")
    parser.add_argument("--new", required=True, help="New baseline path")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()

    report = run_system_integrity_audit(Path.cwd(), args.old, args.new)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["is_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
