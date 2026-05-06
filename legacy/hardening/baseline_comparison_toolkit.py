"""Deterministic baseline diff toolkit for Sprint 9 vs Sprint 10 comparisons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_layer_files(old_files: dict[str, str], new_files: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for key in sorted(old_files.keys()):
        if key not in new_files:
            removed.append({"path": key.replace("\\", "/"), "old": old_files[key], "new": None})
        elif old_files[key] != new_files[key]:
            changed.append({"path": key.replace("\\", "/"), "old": old_files[key], "new": new_files[key]})

    for key in sorted(new_files.keys()):
        if key not in old_files:
            added.append({"path": key.replace("\\", "/"), "old": None, "new": new_files[key]})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def compare_baselines(old_baseline_path: str | Path, new_baseline_path: str | Path) -> dict[str, Any]:
    old_path = Path(old_baseline_path)
    new_path = Path(new_baseline_path)

    old = _load(old_path)
    new = _load(new_path)

    old_layers = old.get("layers", {}) if isinstance(old, dict) else {}
    new_layers = new.get("layers", {}) if isinstance(new, dict) else {}

    all_layers = sorted(set(old_layers.keys()) | set(new_layers.keys()))

    layer_diffs: dict[str, Any] = {}
    has_changes = False

    for layer_name in all_layers:
        old_layer = old_layers.get(layer_name, {})
        new_layer = new_layers.get(layer_name, {})

        old_files = old_layer.get("files", {}) if isinstance(old_layer, dict) else {}
        new_files = new_layer.get("files", {}) if isinstance(new_layer, dict) else {}

        file_diffs = _compare_layer_files(old_files, new_files)

        layer_changed = (
            old_layer.get("layer_hash") != new_layer.get("layer_hash")
            or len(file_diffs["added"]) > 0
            or len(file_diffs["removed"]) > 0
            or len(file_diffs["changed"]) > 0
        )
        if layer_changed:
            has_changes = True

        layer_diffs[layer_name] = {
            "old_layer_hash": old_layer.get("layer_hash"),
            "new_layer_hash": new_layer.get("layer_hash"),
            "old_file_count": old_layer.get("file_count", 0),
            "new_file_count": new_layer.get("file_count", 0),
            "layer_changed": layer_changed,
            "file_diffs": file_diffs,
        }

    report = {
        "old_baseline": str(old_path),
        "new_baseline": str(new_path),
        "has_changes": has_changes,
        "layer_diffs": layer_diffs,
    }

    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two baseline snapshots deterministically")
    parser.add_argument("--old", required=True, help="Old baseline path")
    parser.add_argument("--new", required=True, help="New baseline path")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()

    report = compare_baselines(args.old, args.new)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
