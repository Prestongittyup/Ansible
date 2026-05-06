#!/usr/bin/env python3
"""SCI CI enforcement gate.

Fails pull requests when SCI violations are found in changed files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ALLOWED_EXECUTION_MODES = {"READ_ONLY", "VALIDATION_ONLY", "CHANGE_ENABLED"}

AUTOMATION_PATH_PREFIXES = ("playbooks/", "roles/", "scripts/")
AUTOMATION_EXTENSIONS = {".yml", ".yaml", ".py"}
AUTOMATION_EXEMPT_FILES = {
    "scripts/sci_ci_gate.py",
    "ci/sci_validator.py",
}

SCI_VALIDATION_MARKERS = (
    "SCI_VALIDATE",
    "ci/sci_validator.py",
    "sci_enforcement",
    "SCI_ENFORCEMENT",
)

INVALID_MODE_PATTERNS = [
    re.compile(r"\\binventory-only\\b"),
    re.compile(r"\\bsingle-device debug\\b"),
    re.compile(r"\\bread_only\\b"),
    re.compile(r"\\bchange_enabled\\b"),
]

FORBIDDEN_IDENTITY_PATTERNS = [
    re.compile(r"primary_key\\s*[:=]\\s*['\"]hostname['\"]", re.IGNORECASE),
    re.compile(r"hostname.{0,40}primary\\s+key", re.IGNORECASE),
    re.compile(r"primary\\s+key.{0,40}hostname", re.IGNORECASE),
]

LAYER_BYPASS_PATTERNS = [
    re.compile(r"LogicMonitor\\s*[-=]?>\\s*Ansible", re.IGNORECASE),
    re.compile(r"ingestion\\s*[-=]?>\\s*execution", re.IGNORECASE),
    re.compile(r"LogicMonitor\\s*[-=]?>\\s*PostgreSQL", re.IGNORECASE),
    re.compile(r"skip[_ -]?ingestion", re.IGNORECASE),
    re.compile(r"bypass[_ -]?normalization", re.IGNORECASE),
]


@dataclass
class Violation:
    file_path: str
    rule_violated: str
    severity: str
    message: str
    line: Optional[int] = None


def _run_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _changed_files(base_ref: str, head_ref: str) -> List[str]:
    proc = _run_git(["diff", "--name-only", f"{base_ref}...{head_ref}"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff failed")

    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return files


def _all_tracked_files() -> List[str]:
    proc = _run_git(["ls-files"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _line_number(content: str, start_index: int) -> int:
    return content.count("\n", 0, start_index) + 1


def _append_violation(
    violations: List[Violation],
    file_path: str,
    rule: str,
    severity: str,
    message: str,
    line: Optional[int],
) -> None:
    violations.append(
        Violation(
            file_path=file_path,
            rule_violated=rule,
            severity=severity,
            message=message,
            line=line,
        )
    )


def _scan_invalid_modes(file_path: str, content: str, violations: List[Violation]) -> None:
    for pattern in INVALID_MODE_PATTERNS:
        for match in pattern.finditer(content):
            _append_violation(
                violations,
                file_path,
                "INVALID_EXECUTION_MODE",
                "HIGH",
                f"Forbidden execution mode token detected: {match.group(0)}",
                _line_number(content, match.start()),
            )

    explicit_mode_pattern = re.compile(
        r"execution_mode\\s*[:=]\\s*['\"]([A-Za-z0-9_\\-]+)['\"]"
    )
    for match in explicit_mode_pattern.finditer(content):
        explicit_mode = match.group(1).upper()
        if explicit_mode not in ALLOWED_EXECUTION_MODES:
            _append_violation(
                violations,
                file_path,
                "INVALID_EXECUTION_MODE",
                "HIGH",
                f"execution_mode value not allowed by SCI: {match.group(1)}",
                _line_number(content, match.start()),
            )


def _scan_identity_violations(file_path: str, content: str, violations: List[Violation]) -> None:
    for pattern in FORBIDDEN_IDENTITY_PATTERNS:
        for match in pattern.finditer(content):
            _append_violation(
                violations,
                file_path,
                "FORBIDDEN_IDENTITY_USAGE",
                "HIGH",
                "Hostname used or declared as primary key.",
                _line_number(content, match.start()),
            )


def _scan_layer_bypass(file_path: str, content: str, violations: List[Violation]) -> None:
    for pattern in LAYER_BYPASS_PATTERNS:
        for match in pattern.finditer(content):
            _append_violation(
                violations,
                file_path,
                "LAYER_BOUNDARY_VIOLATION",
                "HIGH",
                f"Potential cross-layer shortcut detected: {match.group(0)}",
                _line_number(content, match.start()),
            )


def _is_automation_path(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    suffix = Path(normalized).suffix.lower()
    return normalized.startswith(AUTOMATION_PATH_PREFIXES) and suffix in AUTOMATION_EXTENSIONS


def _scan_missing_sci_call(file_path: str, content: str, violations: List[Violation]) -> None:
    normalized = file_path.replace("\\", "/")
    if normalized in AUTOMATION_EXEMPT_FILES:
        return

    if not _is_automation_path(normalized):
        return

    if not any(marker in content for marker in SCI_VALIDATION_MARKERS):
        _append_violation(
            violations,
            normalized,
            "MISSING_SCI_VALIDATION_CALL",
            "HIGH",
            "Automation code path does not include explicit SCI validation marker.",
            None,
        )


def _scan_file(file_path: str, violations: List[Violation]) -> None:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        _append_violation(
            violations,
            file_path,
            "FILE_READ_ERROR",
            "HIGH",
            "Unable to read file content for SCI scan.",
            None,
        )
        return

    _scan_invalid_modes(file_path, content, violations)
    _scan_identity_violations(file_path, content, violations)
    _scan_layer_bypass(file_path, content, violations)
    _scan_missing_sci_call(file_path, content, violations)


def _build_report(files: Sequence[str], violations: Sequence[Violation]) -> dict:
    return {
        "status": "PASS" if not violations else "FAIL",
        "violation_count": len(violations),
        "files_scanned": sorted(set(files)),
        "violations": [asdict(v) for v in violations],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SCI CI gate")
    parser.add_argument("--base-ref", help="Base git ref (commit SHA)")
    parser.add_argument("--head-ref", help="Head git ref (commit SHA)")
    args = parser.parse_args()

    try:
        if args.base_ref and args.head_ref:
            files = _changed_files(args.base_ref, args.head_ref)
        else:
            files = _all_tracked_files()

        violations: List[Violation] = []
        for file_path in files:
            _scan_file(file_path, violations)

        report = _build_report(files, violations)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not violations else 1

    except Exception as exc:
        report = {
            "status": "FAIL",
            "violation_count": 1,
            "files_scanned": [],
            "violations": [
                {
                    "file_path": "CI_GATE",
                    "rule_violated": "SCI_CI_GATE_ERROR",
                    "severity": "HIGH",
                    "message": str(exc),
                    "line": None,
                }
            ],
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
