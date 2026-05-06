"""Failure taxonomy contract for deterministic Sprint 2 classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple

from legacy.execution_contract.execution_state import ExecutionState


class FailureCategory(str, Enum):
    """Allowed failure categories for mapped execution outcomes."""

    RUNTIME = "RUNTIME"
    CONNECTIVITY = "CONNECTIVITY"
    VALIDATION = "VALIDATION"
    NOT_EXECUTED = "NOT_EXECUTED"


ALLOWED_CONNECTIVITY_STATUS: Tuple[str, ...] = ("REACHABLE", "UNREACHABLE", "NOT_EXECUTED")
ALLOWED_VALIDATION_STATUS: Tuple[str, ...] = ("PASSED", "FAILED", "NOT_EXECUTED")


@dataclass(frozen=True)
class FailureClassification:
    """Deterministic mapping from category to contract-level result states."""

    category: FailureCategory
    execution_state: str
    connectivity_status: str
    validation_status: str
    default_error_code: str


_CLASSIFICATIONS: Dict[FailureCategory, FailureClassification] = {
    FailureCategory.RUNTIME: FailureClassification(
        category=FailureCategory.RUNTIME,
        execution_state=ExecutionState.FAIL_RUNTIME.value,
        connectivity_status="NOT_EXECUTED",
        validation_status="NOT_EXECUTED",
        default_error_code="ANSIBLE_RUNTIME_FAILURE",
    ),
    FailureCategory.CONNECTIVITY: FailureClassification(
        category=FailureCategory.CONNECTIVITY,
        execution_state=ExecutionState.FAIL_CONNECTIVITY.value,
        connectivity_status="UNREACHABLE",
        validation_status="NOT_EXECUTED",
        default_error_code="CONNECTIVITY_FAILURE",
    ),
    FailureCategory.VALIDATION: FailureClassification(
        category=FailureCategory.VALIDATION,
        execution_state=ExecutionState.FAIL_VALIDATION.value,
        connectivity_status="REACHABLE",
        validation_status="FAILED",
        default_error_code="VALIDATION_FAILED",
    ),
    FailureCategory.NOT_EXECUTED: FailureClassification(
        category=FailureCategory.NOT_EXECUTED,
        execution_state=ExecutionState.NOT_EXECUTED.value,
        connectivity_status="NOT_EXECUTED",
        validation_status="NOT_EXECUTED",
        default_error_code="NOT_EXECUTED",
    ),
}


def parse_failure_category(value: str) -> FailureCategory:
    """Parse and validate a failure category from string input."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("failure category must be a non-empty string")

    normalized = value.strip().upper()
    try:
        return FailureCategory(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported failure category: {value}") from exc


def classify_failure_category(category: FailureCategory | str) -> FailureClassification:
    """Return deterministic classification details for a given category."""
    normalized = parse_failure_category(category) if isinstance(category, str) else category

    classification = _CLASSIFICATIONS.get(normalized)
    if classification is None:
        raise ValueError(f"no classification mapping for category: {normalized}")
    return classification
