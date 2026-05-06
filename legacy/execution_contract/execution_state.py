"""Execution state enum contract for Sprint 2."""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class ExecutionState(str, Enum):
    """Allowed deterministic execution states for validation outcomes."""

    SUCCESS = "SUCCESS"
    FAIL_RUNTIME = "FAIL_RUNTIME"
    FAIL_CONNECTIVITY = "FAIL_CONNECTIVITY"
    FAIL_VALIDATION = "FAIL_VALIDATION"
    NOT_EXECUTED = "NOT_EXECUTED"


def allowed_execution_states() -> Tuple[str, ...]:
    """Return all allowed execution state values."""
    return tuple(state.value for state in ExecutionState)


def is_valid_execution_state(value: str) -> bool:
    """Check whether a value is a valid execution_state."""
    return value in allowed_execution_states()


def require_execution_state(value: str) -> str:
    """Return value when valid, otherwise raise ValueError."""
    if not is_valid_execution_state(value):
        raise ValueError(f"invalid execution_state: {value}")
    return value
