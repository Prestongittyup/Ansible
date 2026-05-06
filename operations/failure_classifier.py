from __future__ import annotations

from typing import Any, Dict, List


FAILURE_CLASSES = {
    "AUTH_FAILURE",
    "CONNECTIVITY_FAILURE",
    "SCHEMA_MISMATCH",
    "DATA_INTEGRITY_VIOLATION",
    "TIMEOUT_FAILURE",
    "RATE_LIMIT_EXCEEDED",
    "DETERMINISM_FAILURE",
}


def classify_failure(*, root_cause: str, payload: Dict[str, Any] | None = None) -> str:
    message = str(root_cause or "").strip().lower()
    payload_dict = dict(payload or {})

    if "429" in message or "rate limit" in message or "retry-after" in message:
        return "RATE_LIMIT_EXCEEDED"

    if "timeout" in message or "timed out" in message:
        return "TIMEOUT_FAILURE"

    if (
        "401" in message
        or "403" in message
        or "unauthorized" in message
        or "forbidden" in message
        or "authentication" in message
        or "invalid token" in message
        or "auth failed" in message
        or "missing_credentials" in message
    ):
        return "AUTH_FAILURE"

    if (
        "schema" in message
        or "invalid json" in message
        or "contract" in message
        or "results" in message and "missing" in message
    ):
        return "SCHEMA_MISMATCH"

    if (
        "duplicate ip_address" in message
        or "missing ip_address" in message
        or "identity" in message
        or "data integrity" in message
        or "non-dict record" in message
        or "non-list records" in message
    ):
        return "DATA_INTEGRITY_VIOLATION"

    if "deterministic" in message or "hash mismatch" in message:
        return "DETERMINISM_FAILURE"

    if (
        "connection" in message
        or "getaddrinfo" in message
        or "refused" in message
        or "no route" in message
        or "dns" in message
        or "network" in message
        or "urlopen error" in message
        or "psycopg2 unavailable" in message
    ):
        return "CONNECTIVITY_FAILURE"

    # If adapter explicitly marks schema/auth/connectivity sections as fail, use that signal.
    auth_section = payload_dict.get("auth")
    if isinstance(auth_section, dict) and str(auth_section.get("status", "")).upper() == "FAIL":
        return "AUTH_FAILURE"

    schema_section = payload_dict.get("schema_handshake")
    if isinstance(schema_section, dict) and str(schema_section.get("status", "")).upper() == "FAIL":
        return "SCHEMA_MISMATCH"

    connectivity = payload_dict.get("connectivity")
    if isinstance(connectivity, dict) and str(connectivity.get("status", "")).upper() == "FAIL":
        return "CONNECTIVITY_FAILURE"

    return "CONNECTIVITY_FAILURE"


def flatten_integration_failures(*, integration_status: Dict[str, Dict[str, Any]], phase: str) -> List[Dict[str, str]]:
    failures: List[Dict[str, str]] = []
    for adapter_name, payload in integration_status.items():
        status_token = str(payload.get("status", "")).upper()
        if status_token in {"PASS", "SKIPPED"}:
            continue

        operation = str(payload.get("operation") or "bootstrap_integration_probe")
        root_cause = str(payload.get("root_cause") or payload.get("reason") or "unspecified_failure")
        failure_class = str(payload.get("failure_class") or classify_failure(root_cause=root_cause, payload=payload))
        failures.append(
            {
                "adapter": str(adapter_name),
                "phase": str(phase),
                "operation": operation,
                "root_cause": root_cause,
                "failure_class": failure_class,
            }
        )
    return failures
