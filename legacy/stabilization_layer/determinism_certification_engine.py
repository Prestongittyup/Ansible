"""Determinism certification engine for Sprint 12."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

SCHEMA_VERSION = "2.1"

RunnerCallable = Callable[[Path, str, int], dict[str, Any]]


class DeterminismCertificationError(RuntimeError):
    """Raised when determinism checks fail in fail-closed mode."""


def _run_unified_compliance(root: Path, python_executable: str, timeout_seconds: int) -> dict[str, Any]:
    process = subprocess.run(
        [
            python_executable,
            "scripts/run_unified_compliance.py",
            "--python-executable",
            python_executable,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if process.returncode != 0:
        raise DeterminismCertificationError(
            f"unified compliance runner failed: code={process.returncode} stderr={process.stderr.strip()}"
        )

    payload = json.loads(process.stdout.strip())
    if not isinstance(payload, dict):
        raise DeterminismCertificationError("unified compliance runner returned malformed payload")
    return payload


def certify_unified_runner_determinism(
    *,
    root: Path,
    python_executable: str | None = None,
    run_count: int = 3,
    timeout_seconds: int = 900,
    reference_hash: str | None = None,
    runner: RunnerCallable | None = None,
) -> dict[str, Any]:
    if run_count < 2:
        raise ValueError("run_count must be >= 2")

    py_exec = python_executable or sys.executable
    runner_fn = runner or _run_unified_compliance

    runs: list[dict[str, Any]] = []
    for _ in range(run_count):
        runs.append(runner_fn(root, py_exec, timeout_seconds))

    hashes = [str(item.get("deterministic_hash", "")) for item in runs]
    statuses = [str(item.get("compliance_status", "")) for item in runs]
    trace_signatures = [
        [entry.get("gate") for entry in item.get("execution_trace", [])]
        for item in runs
    ]

    unique_hashes = sorted({value for value in hashes if value})
    unique_statuses = sorted(set(statuses))
    unique_traces = sorted({json.dumps(trace, sort_keys=True) for trace in trace_signatures})

    mismatches: list[str] = []
    if len(unique_hashes) != 1:
        mismatches.append("deterministic_hash_mismatch")
    if unique_statuses != ["COMPLIANT"]:
        mismatches.append("non_compliant_status_detected")
    if len(unique_traces) != 1:
        mismatches.append("execution_trace_order_mismatch")

    reference_match = None
    if reference_hash is not None:
        reference_match = len(unique_hashes) == 1 and unique_hashes[0] == reference_hash
        if not reference_match:
            mismatches.append("reference_hash_mismatch")

    result = {
        "schema_version": SCHEMA_VERSION,
        "run_count": run_count,
        "hashes": hashes,
        "statuses": statuses,
        "unique_hash_count": len(unique_hashes),
        "unique_trace_count": len(unique_traces),
        "reference_hash": reference_hash,
        "reference_match": reference_match,
        "deterministic_hash": unique_hashes[0] if len(unique_hashes) == 1 else "",
        "is_deterministic": len(mismatches) == 0,
        "mismatches": sorted(mismatches),
    }
    return result


def enforce_determinism(report: dict[str, Any]) -> None:
    if not bool(report.get("is_deterministic", False)):
        raise DeterminismCertificationError(
            f"determinism certification failed: {report.get('mismatches', [])}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Determinism certification engine")
    parser.add_argument("--run-count", type=int, default=3, help="Number of repeated unified runs")
    parser.add_argument("--python-executable", default=sys.executable, help="Python executable")
    parser.add_argument("--reference-hash", default="", help="Optional deterministic hash reference")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    report = certify_unified_runner_determinism(
        root=Path.cwd(),
        python_executable=args.python_executable,
        run_count=args.run_count,
        reference_hash=args.reference_hash or None,
    )

    try:
        enforce_determinism(report)
        status = 0
    except DeterminismCertificationError:
        status = 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
