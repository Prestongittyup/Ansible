"""Pure deterministic mapping from normalized Ansible results to failure taxonomy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from legacy.execution_contract.failure_taxonomy import FailureCategory, classify_failure_category

_NON_311_HINT_PATTERN = re.compile(r"python\s*3\.(1[2-9]|[2-9][0-9])|python31[2-9]", re.IGNORECASE)

_RUNTIME_TOKENS = {
    "traceback",
    "modulenotfounderror",
    "importerror",
    "ansible-playbook executable not found",
    "check_blocking_io",
    "incorrect function",
    "runtime version mismatch",
}

_CONNECTIVITY_TOKENS = {
    "unreachable",
    "timed out",
    "timeout",
    "permission denied",
    "authentication failed",
    "connection refused",
    "connection reset",
    "no route to host",
    "host key verification failed",
    "failed to connect to the host via ssh",
}

_ALLOWED_OUTCOMES = {
    "PLAYBOOK_FAILED",
    "TIMEOUT",
    "EXECUTION_ERROR",
    "RUNTIME_ERROR",
    "UNKNOWN",
}


class MapperInputError(ValueError):
    """Raised when mapper input is not a valid normalized_ansible_result."""


@dataclass(frozen=True)
class MappedAnsibleFailure:
    """Structured deterministic failure mapping result."""

    category: FailureCategory
    execution_state: str
    connectivity_status: str
    validation_status: str
    error_code: str
    error_message: str
    details: Dict[str, Any]


def _trim_text(value: str, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _contains_non_311_hint(text: str) -> bool:
    lowered = text.lower()
    if _NON_311_HINT_PATTERN.search(lowered):
        return True
    if "python 3.11" in lowered or "python311" in lowered:
        return False
    return "python313" in lowered or "python312" in lowered


def _validate_normalized_input(normalized_ansible_result: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(normalized_ansible_result, Mapping):
        raise MapperInputError("normalized_ansible_result must be an object")

    required_fields = {"outcome", "failure_text", "return_code", "source"}
    keys = set(normalized_ansible_result.keys())
    if keys != required_fields:
        raise MapperInputError("normalized_ansible_result schema mismatch")

    outcome = str(normalized_ansible_result.get("outcome", "")).strip().upper()
    if outcome not in _ALLOWED_OUTCOMES:
        raise MapperInputError("normalized_ansible_result.outcome is invalid")

    failure_text = _trim_text(str(normalized_ansible_result.get("failure_text", "")))
    source = str(normalized_ansible_result.get("source", "")).strip().lower()
    if not source:
        raise MapperInputError("normalized_ansible_result.source is required")

    return_code = normalized_ansible_result.get("return_code")
    if return_code is not None and not isinstance(return_code, int):
        raise MapperInputError("normalized_ansible_result.return_code must be integer or null")

    return {
        "outcome": outcome,
        "failure_text": failure_text,
        "return_code": return_code,
        "source": source,
    }


def _mapped_failure(
    *,
    category: FailureCategory,
    error_code: str,
    error_message: str,
    normalized_input: Mapping[str, Any],
) -> MappedAnsibleFailure:
    classification = classify_failure_category(category)

    return MappedAnsibleFailure(
        category=classification.category,
        execution_state=classification.execution_state,
        connectivity_status=classification.connectivity_status,
        validation_status=classification.validation_status,
        error_code=error_code,
        error_message=error_message,
        details={
            "normalized_ansible_result": {
                "outcome": normalized_input["outcome"],
                "return_code": normalized_input["return_code"],
                "source": normalized_input["source"],
                "failure_text": normalized_input["failure_text"],
            }
        },
    )


def map_normalized_ansible_result(normalized_ansible_result: Mapping[str, Any]) -> MappedAnsibleFailure:
    """Map normalized_ansible_result to deterministic failure classification."""
    normalized = _validate_normalized_input(normalized_ansible_result)

    outcome = normalized["outcome"]
    failure_text = normalized["failure_text"]
    lowered = failure_text.lower()

    if outcome == "TIMEOUT":
        return _mapped_failure(
            category=FailureCategory.CONNECTIVITY,
            error_code="CONNECTIVITY_TIMEOUT",
            error_message="device connectivity validation timed out",
            normalized_input=normalized,
        )

    if outcome in {"EXECUTION_ERROR", "RUNTIME_ERROR"}:
        error_code = "RUNTIME_VERSION_MISMATCH" if _contains_non_311_hint(lowered) else "ANSIBLE_RUNTIME_FAILURE"
        error_message = (
            "ansible runtime is not Python 3.11.x"
            if error_code == "RUNTIME_VERSION_MISMATCH"
            else "ansible runtime failure"
        )
        return _mapped_failure(
            category=FailureCategory.RUNTIME,
            error_code=error_code,
            error_message=error_message,
            normalized_input=normalized,
        )

    if outcome == "PLAYBOOK_FAILED":
        if _contains_non_311_hint(lowered):
            return _mapped_failure(
                category=FailureCategory.RUNTIME,
                error_code="RUNTIME_VERSION_MISMATCH",
                error_message="ansible runtime is not Python 3.11.x",
                normalized_input=normalized,
            )

        if any(token in lowered for token in _CONNECTIVITY_TOKENS):
            return _mapped_failure(
                category=FailureCategory.CONNECTIVITY,
                error_code="CONNECTIVITY_FAILURE",
                error_message="device connectivity validation failed",
                normalized_input=normalized,
            )

        if "failed=1" in lowered and "unreachable=0" in lowered:
            return _mapped_failure(
                category=FailureCategory.VALIDATION,
                error_code="VALIDATION_FAILED",
                error_message="device validation checks failed",
                normalized_input=normalized,
            )

        if any(token in lowered for token in _RUNTIME_TOKENS):
            return _mapped_failure(
                category=FailureCategory.RUNTIME,
                error_code="ANSIBLE_RUNTIME_FAILURE",
                error_message="ansible runtime failure",
                normalized_input=normalized,
            )

    return _mapped_failure(
        category=FailureCategory.RUNTIME,
        error_code="ANSIBLE_RUNTIME_INCONSISTENT",
        error_message="ansible failure could not be classified deterministically",
        normalized_input=normalized,
    )
