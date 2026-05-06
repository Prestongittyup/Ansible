"""Execution trace replay validation for Sprint 12 stabilization."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

SCHEMA_VERSION = "2.1"
VOLATILE_KEYS = {
    "timestamp",
    "started_at",
    "finished_at",
    "captured_at",
    "output_hash",
    "stdout_excerpt",
    "stderr_excerpt",
}


class ExecutionReplayError(RuntimeError):
    """Raised when replay validation fails in fail-closed mode."""


CommandRunner = Callable[[list[str], Path, int], dict[str, Any]]


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _parse_json_output(stdout: str) -> dict[str, Any]:
    payload = json.loads(stdout.strip())
    if not isinstance(payload, dict):
        raise ExecutionReplayError("command output payload is not a JSON object")
    return payload


def _run_json_command(command: list[str], root: Path, timeout_seconds: int) -> dict[str, Any]:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if process.returncode != 0:
        raise ExecutionReplayError(
            f"command failed: code={process.returncode} command={command} stderr={process.stderr.strip()}"
        )
    return _parse_json_output(process.stdout)


def _signature(payload: dict[str, Any]) -> str:
    normalized = _strip_volatile(payload)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_execution_replay(
    *,
    root: Path,
    python_executable: str | None = None,
    replay_count: int = 2,
    timeout_seconds: int = 900,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    if replay_count < 2:
        raise ValueError("replay_count must be >= 2")

    py_exec = python_executable or sys.executable
    runner = command_runner or _run_json_command

    command_map = {
        "emv": [py_exec, "ci/meta/ci_enforcement_meta_validator.py"],
        "ci_kernel": [py_exec, "ci/run_ci_kernel.py"],
        "unified_runner": [py_exec, "scripts/run_unified_compliance.py", "--python-executable", py_exec],
    }

    results: dict[str, Any] = {}
    mismatches: list[str] = []

    for name, command in sorted(command_map.items()):
        payloads = [runner(command, root, timeout_seconds) for _ in range(replay_count)]
        signatures = [_signature(item) for item in payloads]
        unique_signatures = sorted(set(signatures))

        status_key = "compliance_status" if name == "unified_runner" else ("ci_status" if name == "ci_kernel" else "emv_status")
        statuses = [str(item.get(status_key, "")) for item in payloads]
        expected_status = "COMPLIANT" if name == "unified_runner" else "PASS"

        if len(unique_signatures) != 1:
            mismatches.append(f"{name}_signature_mismatch")
        if sorted(set(statuses)) != [expected_status]:
            mismatches.append(f"{name}_status_mismatch")

        results[name] = {
            "replay_count": replay_count,
            "statuses": statuses,
            "unique_signature_count": len(unique_signatures),
            "signature": unique_signatures[0] if len(unique_signatures) == 1 else "",
        }

    report = {
        "schema_version": SCHEMA_VERSION,
        "replay_count": replay_count,
        "results": results,
        "is_replay_consistent": len(mismatches) == 0,
        "mismatches": sorted(mismatches),
    }
    return report


def enforce_replay_consistency(report: dict[str, Any]) -> None:
    if not bool(report.get("is_replay_consistent", False)):
        raise ExecutionReplayError(f"execution replay mismatch detected: {report.get('mismatches', [])}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execution replay validator")
    parser.add_argument("--replay-count", type=int, default=2, help="Number of repeated executions per command")
    parser.add_argument("--python-executable", default=sys.executable, help="Python executable")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    report = validate_execution_replay(
        root=Path.cwd(),
        python_executable=args.python_executable,
        replay_count=args.replay_count,
    )

    try:
        enforce_replay_consistency(report)
        status = 0
    except ExecutionReplayError:
        status = 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
