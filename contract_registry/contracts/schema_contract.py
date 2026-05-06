"""Schema contracts for execution outputs and CI compatibility."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


EXECUTION_SCHEMA_CONTRACT: Dict[str, Any] = {
    "schema_version": "2.1",
    "ci_schema_version": "1.0",
    "metadata_required_fields": ["schema_version"],
    "result_required_fields": [
        "run_id",
        "device_ip",
        "status",
        "error_code",
        "error_message",
        "execution_time_ms",
        "timestamp",
        "schema_version",
    ],
}


def _validate_execution_schema(payload: Mapping[str, Any]) -> None:
    schema_version = str(payload.get("schema_version", ""))
    if schema_version != "2.1":
        raise ValueError("EXECUTION_SCHEMA schema_version must be 2.1")

    fields = payload.get("result_required_fields")
    if not isinstance(fields, list) or "schema_version" not in fields:
        raise ValueError("EXECUTION_SCHEMA result_required_fields must include schema_version")

    metadata_fields = payload.get("metadata_required_fields")
    if not isinstance(metadata_fields, list) or "schema_version" not in metadata_fields:
        raise ValueError("EXECUTION_SCHEMA metadata_required_fields must include schema_version")


def get_contracts() -> List[Dict[str, Any]]:
    return [
        {
            "name": "EXECUTION_SCHEMA",
            "version": "2.1",
            "payload": EXECUTION_SCHEMA_CONTRACT,
            "validator": _validate_execution_schema,
        }
    ]
