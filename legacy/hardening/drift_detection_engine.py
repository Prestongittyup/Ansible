"""Cross-layer drift detection utilities for immutable system surfaces."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_LAYER_GLOBS: dict[str, list[str]] = {
    "enforcement": ["bootstrap.py", "ci/**/*.py", "ci/**/*.yaml", "ci/**/*.yml", "execution_contract/**/*.py", ".github/workflows/sci_enforcement.yml"],
    "persistence": ["persistence/**/*.py", "persistence/**/*.sql"],
    "query": ["query_layer/**/*.py"],
    "api": ["api_layer/**/*.py", "test_api_layer.py"],
    "auth": ["auth_layer/**/*.py", "test_auth_layer.py"],
    "governance": ["governance_layer/**/*.py", "test_governance_layer.py"],
    "observability": ["observability/**/*.py", "test_observability_layer.py"],
    "export": ["export/**/*.py", "test_export_layer.py"],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_files(root: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        for candidate in root.glob(pattern):
            if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix != ".pyc":
                files.append(candidate)
    return sorted(set(files), key=lambda p: p.as_posix())


def compute_current_layer_hashes(root: Path, layer_globs: dict[str, list[str]] | None = None) -> dict[str, dict[str, Any]]:
    globs = layer_globs or DEFAULT_LAYER_GLOBS
    layers: dict[str, dict[str, Any]] = {}

    for layer_name in sorted(globs.keys()):
        files = _collect_files(root, globs[layer_name])
        per_file = {str(path.relative_to(root)).replace("/", "\\"): _sha256(path) for path in files}
        digest_material = "\n".join(f"{key}:{per_file[key]}" for key in sorted(per_file.keys()))
        layer_hash = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        layers[layer_name] = {
            "layer": layer_name,
            "file_count": len(per_file),
            "layer_hash": layer_hash,
            "files": per_file,
        }

    return layers


def compare_with_baseline(root: Path, baseline: dict[str, Any], layers_to_check: list[str] | None = None) -> dict[str, Any]:
    expected_layers = baseline.get("layers", {}) if isinstance(baseline, dict) else {}

    selected = sorted(layers_to_check or list(expected_layers.keys()))
    current = compute_current_layer_hashes(root, {name: DEFAULT_LAYER_GLOBS.get(name, []) for name in selected if name in DEFAULT_LAYER_GLOBS})

    result: dict[str, Any] = {}
    all_unchanged = True

    for layer_name in selected:
        expected_files = expected_layers.get(layer_name, {}).get("files", {})
        current_files = current.get(layer_name, {}).get("files", {})

        mismatches: list[dict[str, Any]] = []

        for rel_win, expected_hash in sorted(expected_files.items()):
            current_hash = current_files.get(rel_win)
            if current_hash is None:
                mismatches.append({"path": rel_win.replace("\\", "/"), "status": "MISSING", "expected": expected_hash, "current": None})
            elif current_hash != expected_hash:
                mismatches.append({"path": rel_win.replace("\\", "/"), "status": "CHANGED", "expected": expected_hash, "current": current_hash})

        for rel_win, current_hash in sorted(current_files.items()):
            if rel_win not in expected_files:
                mismatches.append({"path": rel_win.replace("\\", "/"), "status": "ADDED", "expected": None, "current": current_hash})

        if mismatches:
            all_unchanged = False

        result[layer_name] = {
            "tracked_files": len(expected_files),
            "current_files": len(current_files),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
            "expected_layer_hash": expected_layers.get(layer_name, {}).get("layer_hash"),
            "current_layer_hash": current.get(layer_name, {}).get("layer_hash"),
        }

    return {
        "all_unchanged": all_unchanged,
        "layers": result,
    }


def _resolve_validation_results_path(root: Path) -> Path:
    primary = root / "ansible_validation" / "outputs" / "validation_results.json"
    if primary.exists():
        return primary

    legacy = root / "legacy" / "ansible_validation" / "outputs" / "validation_results.json"
    if legacy.exists():
        return legacy

    return primary


def validate_schema_versions(root: Path) -> dict[str, Any]:
    validation_results_path = _resolve_validation_results_path(root)
    payload = json.loads(validation_results_path.read_text(encoding="utf-8"))
    metadata_schema = str(payload.get("metadata", {}).get("schema_version", ""))
    schema_block = str(payload.get("schema", {}).get("schema_version", ""))

    from observability import schema as obs_schema
    from legacy.export import metrics_exporter, otel_mapper, siem_exporter, trace_exporter

    checks = {
        "input_metadata_schema_version": metadata_schema,
        "input_schema_block_schema_version": schema_block,
        "observability_schema_version": str(obs_schema.OBSERVABILITY_SCHEMA_VERSION),
        "export_trace_schema_version": str(trace_exporter.SCHEMA_VERSION),
        "export_metrics_schema_version": str(metrics_exporter.SCHEMA_VERSION),
        "export_otel_schema_version": str(otel_mapper.SCHEMA_VERSION),
        "export_siem_schema_version": str(siem_exporter.SCHEMA_VERSION),
    }

    all_consistent = all(value == "2.1" for value in checks.values())
    return {
        "all_consistent": all_consistent,
        "checks": checks,
    }


def _local_module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def build_dependency_graph(root: Path) -> dict[str, list[str]]:
    py_files = sorted([path for path in root.rglob("*.py") if "__pycache__" not in path.parts], key=lambda p: p.as_posix())
    local_modules = {_local_module_name(root, path): path for path in py_files}

    graph: dict[str, list[str]] = {}

    for module_name, path in local_modules.items():
        imports: set[str] = set()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            graph[module_name] = []
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    if node.level and module_name:
                        parent_parts = module_name.split(".")[:-node.level]
                        resolved = ".".join(parent_parts + node.module.split(".")) if parent_parts else node.module
                        imports.add(resolved)
                    else:
                        imports.add(node.module)

        local_imports = sorted(
            {
                local
                for local in local_modules
                for imported in imports
                if imported == local or imported.startswith(local + ".")
            }
        )
        graph[module_name] = local_imports

    return dict(sorted((module, deps) for module, deps in graph.items()))


def validate_dependency_graph(graph: dict[str, list[str]], forbidden_edges: list[tuple[str, str]]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []

    for source_prefix, target_prefix in forbidden_edges:
        for source, deps in graph.items():
            if not source.startswith(source_prefix):
                continue
            for dep in deps:
                if dep.startswith(target_prefix):
                    violations.append({
                        "source": source,
                        "target": dep,
                        "rule": f"{source_prefix} !-> {target_prefix}",
                    })

    violations.sort(key=lambda item: (item["source"], item["target"], item["rule"]))
    return {
        "is_valid": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sprint 10 drift detection engine")
    parser.add_argument("--baseline", required=True, help="Path to baseline JSON")
    parser.add_argument("--layers", default="", help="Comma-separated layers to compare")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()

    root = Path.cwd()
    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    layers = [item.strip() for item in args.layers.split(",") if item.strip()] or None

    report = compare_with_baseline(root=root, baseline=baseline, layers_to_check=layers)
    schema = validate_schema_versions(root)

    print(json.dumps({"drift": report, "schema": schema}, indent=2, sort_keys=True))
    return 0 if report["all_unchanged"] and schema["all_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
