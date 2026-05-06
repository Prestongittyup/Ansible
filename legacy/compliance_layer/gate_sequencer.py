"""Deterministic gate sequencing contract for Sprint 11 orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable

SEQUENCER_SCHEMA_VERSION = "2.1"

SCI_REQUIRED_ARTIFACTS = (
    "SYSTEM_CONTRACT_INDEX.md",
    "EXECUTION_PRECEDENCE_CONTRACT.md",
    "ci/science/SCI_RULE_REGISTRY.yaml",
)

REQUIRED_GATE_SEQUENCE = (
    "SCI_AUTHORITY",
    "EMV",
    "CI_KERNEL",
    "AUTH",
    "GOVERNANCE",
    "API",
    "QUERY",
    "PERSISTENCE",
    "OBSERVABILITY",
    "EXPORT",
    "DRIFT_HARDENING",
)


@dataclass(frozen=True)
class GateDefinition:
    name: str
    layer: str
    rule_id: str
    command: tuple[str, ...] | None
    expects_json: bool = False


def expected_gate_sequence() -> list[str]:
    return list(REQUIRED_GATE_SEQUENCE)


def required_artifact_paths(root: Path) -> list[Path]:
    return [root / rel for rel in SCI_REQUIRED_ARTIFACTS]


def default_gate_definitions(python_executable: str | None = None) -> list[GateDefinition]:
    python_bin = python_executable or sys.executable

    return [
        GateDefinition("SCI_AUTHORITY", "sci_authority", "SCI-GATE-000", None, expects_json=False),
        GateDefinition("EMV", "enforcement", "SCI-001", (python_bin, "ci/meta/ci_enforcement_meta_validator.py"), expects_json=True),
        GateDefinition("CI_KERNEL", "enforcement", "SCI-006", (python_bin, "ci/run_ci_kernel.py"), expects_json=True),
        GateDefinition("AUTH", "auth", "AUTH-GATE-001", (python_bin, "-m", "unittest", "test_auth_layer.py")),
        GateDefinition("GOVERNANCE", "governance", "GOV-GATE-001", (python_bin, "-m", "unittest", "test_governance_layer.py")),
        GateDefinition("API", "api", "API-GATE-001", (python_bin, "-m", "unittest", "test_api_layer.py")),
        GateDefinition("QUERY", "query", "QUERY-GATE-001", (python_bin, "-m", "unittest", "test_query_layer.py")),
        GateDefinition("PERSISTENCE", "persistence", "PERSIST-GATE-001", (python_bin, "-m", "unittest", "persistence/test_ingest_validation.py")),
        GateDefinition("OBSERVABILITY", "observability", "OBS-GATE-001", (python_bin, "-m", "unittest", "test_observability_layer.py")),
        GateDefinition("EXPORT", "export", "EXPORT-GATE-001", (python_bin, "-m", "unittest", "test_export_layer.py")),
        GateDefinition(
            "DRIFT_HARDENING",
            "hardening",
            "HARDENING-GATE-001",
            (
                python_bin,
                "-m",
                "unittest",
                "test_drift_detection.py",
                "test_system_integrity.py",
                "test_baseline_comparison.py",
                "test_immutability_checker.py",
            ),
        ),
    ]


def validate_gate_sequence(gates: Iterable[GateDefinition]) -> tuple[bool, list[str]]:
    observed = [gate.name for gate in gates]
    expected = expected_gate_sequence()

    if observed == expected:
        return True, []

    issues: list[str] = []

    if len(observed) != len(expected):
        issues.append(f"gate_count_mismatch: observed={len(observed)} expected={len(expected)}")

    for index, expected_name in enumerate(expected):
        if index >= len(observed):
            issues.append(f"missing_gate_at_{index}:{expected_name}")
            continue
        if observed[index] != expected_name:
            issues.append(f"order_mismatch_at_{index}:observed={observed[index]} expected={expected_name}")

    if len(observed) > len(expected):
        for extra_name in observed[len(expected) :]:
            issues.append(f"unexpected_gate:{extra_name}")

    return False, issues
