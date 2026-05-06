"""Execution authority contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


SCI_AUTHORITY_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "authority": "SCI",
    "required_markers": [
        "SCI is the highest authority",
        "Fail-closed rule",
        "Authority and Precedence",
    ],
    "execution_contract_markers": [
        "Execution Order (Non-Negotiable)",
        "Authority Precedence Rules",
        "Forbidden Execution Patterns",
        "Fail-Closed Behavior Chain",
    ],
    "authority_statements": [
        "SCI is the highest authority for runtime and CI execution decisions.",
        "Fail-closed rule is mandatory for every gate outcome.",
        "Authority and Precedence are contract-governed and immutable at runtime.",
    ],
    "required_context_fields": {
        "run_id": "str",
        "device_ip": "str",
        "execution_mode": "str",
        "source_layer": "str",
        "target_action": "str",
        "data_classification": "str",
        "timestamp": "str",
    },
    "allowed_execution_modes": ["READ_ONLY", "VALIDATION_ONLY", "CHANGE_ENABLED"],
    "allowed_data_classifications": ["STATE", "EVIDENCE"],
    "layer_aliases": {
        "LAYER_0": "EXTERNAL_SOURCES",
        "LAYER_1": "INGESTION",
        "LAYER_2": "NORMALIZATION",
        "LAYER_3": "EXECUTION",
        "LAYER_4": "CANONICAL_STATE",
        "LAYER_5": "GOVERNANCE",
        "LAYER_6": "SOURCE_OF_TRUTH",
        "LOGICMONITOR": "EXTERNAL_SOURCES",
        "NETWORK_DEVICES": "EXTERNAL_SOURCES",
        "POSTGRESQL": "CANONICAL_STATE",
        "NAUTOBOT": "SOURCE_OF_TRUTH",
    },
    "allowed_layer_actions": {
        "EXTERNAL_SOURCES": ["DISCOVERY_EXPORT", "RUNTIME_STATE_READ"],
        "INGESTION": ["INGEST", "NORMALIZE_IDENTITY", "OUTPUT_STRUCTURED_INVENTORY_JSON"],
        "NORMALIZATION": ["SCHEMA_ENFORCEMENT", "BUILD_ANSIBLE_INVENTORY"],
        "EXECUTION": ["SSH_VALIDATION", "FACT_COLLECTION", "READ_ONLY_OPERATION", "CONFIG_CHANGE"],
        "CANONICAL_STATE": ["STORE_VALIDATED_STATE", "TRACK_HISTORY", "UPDATE_LAST_SEEN", "READ_CANONICAL_STATE"],
        "GOVERNANCE": ["EVALUATE_AUTOMATION_ENABLED", "APPROVAL_CHECK", "SCI_COMPLIANCE_CHECK"],
        "SOURCE_OF_TRUTH": ["PLANNED_MODEL_SYNC_READONLY"],
    },
    "state_actions": [
        "INGEST",
        "NORMALIZE_IDENTITY",
        "OUTPUT_STRUCTURED_INVENTORY_JSON",
        "SCHEMA_ENFORCEMENT",
        "BUILD_ANSIBLE_INVENTORY",
        "STORE_VALIDATED_STATE",
        "TRACK_HISTORY",
        "UPDATE_LAST_SEEN",
        "READ_CANONICAL_STATE",
    ],
    "evidence_actions": ["SSH_VALIDATION", "FACT_COLLECTION", "READ_ONLY_OPERATION"],
    "forbidden_shortcut_tokens": [
        "BYPASS",
        "SKIP_INGESTION",
        "DIRECT_TO_EXECUTION",
        "INGESTION_TO_EXECUTION",
        "LOGICMONITOR_TO_DB",
        "DIRECT_DB_WRITE",
        "EVIDENCE_DIRECT_STATE_WRITE",
    ],
    "guard_policy": {
        "env_var": "GLOBAL_AUTOMATION_GUARD",
        "allowed_states": ["ENABLED", "OPEN", "CLOSED"],
        "change_enabled_requires_open": True,
        "change_enabled_requires_sci_permit": True,
        "non_change_mode_forbids_open_guard": True,
    },
}


EXECUTION_AUTHORITY_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "canonical_kernel_module": "system_kernel",
    "required_kernel_commands": ["bootstrap", "run"],
    "legacy_kernel_entrypoint_enabled": False,
    "bootstrap_runtime_guard_required": True,
    "runner_entry_contract": "LEGACY_BRIDGE",
}


LEGACY_BRIDGE_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "lock_marker": "LEGACY_IMMOBILIZATION_LOCK_V1",
    "locked": True,
    "immutable": True,
    "runtime_guard": {
        "module": "legacy.execution_contract.runtime_guard",
        "runtime_standard_constant": "EXECUTION_LAYER_RUNTIME_STANDARD",
        "error_type": "RuntimeGuardError",
        "enforce_callable": "enforce_preimport_runtime_311",
        "python_runtime_callable": "current_python_runtime",
    },
    "inventory": {
        "module": "legacy.ansible_validation.inventory.dynamic_inventory",
        "error_type": "InventoryLoadError",
    },
    "runner": {
        "module": "legacy.ansible_validation.runners.run_validation",
        "build_argument_parser": "build_argument_parser",
        "run_orchestration": "run_orchestration",
        "error_type": "ValidationSystemError",
    },
    "schema": {
        "module": "legacy.execution_contract.schema",
        "error_type": "SchemaContractError",
        "schema_version_callable": "schema_version",
    },
}


BOOTSTRAP_AUTHORITY_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "bootstrap_entrypoint": "bootstrap.main",
    "required_runtime_guard_call": "enforce_preimport_runtime_311",
    "required_chain": ["runtime_guard", "runner", "schema"],
    "allowed_orchestration_callers": ["bootstrap"],
    "legacy_bridge_contract": "LEGACY_BRIDGE",
}


def _require_keys(payload: Mapping[str, Any], required: List[str], contract_name: str) -> None:
    for key in required:
        if key not in payload:
            raise ValueError(f"{contract_name} missing required key: {key}")


def _validate_sci_authority(payload: Mapping[str, Any]) -> None:
    _require_keys(
        payload,
        [
            "authority",
            "required_markers",
            "required_context_fields",
            "allowed_execution_modes",
            "allowed_layer_actions",
            "guard_policy",
        ],
        "SCI_AUTHORITY",
    )
    if str(payload.get("authority", "")).upper() != "SCI":
        raise ValueError("SCI_AUTHORITY authority must be SCI")
    markers = payload.get("required_markers")
    if not isinstance(markers, list) or not markers:
        raise ValueError("SCI_AUTHORITY required_markers must be a non-empty list")


def _validate_execution_authority(payload: Mapping[str, Any]) -> None:
    _require_keys(
        payload,
        ["canonical_kernel_module", "required_kernel_commands", "legacy_kernel_entrypoint_enabled"],
        "EXECUTION_AUTHORITY",
    )
    commands = payload.get("required_kernel_commands")
    if not isinstance(commands, list) or "bootstrap" not in commands or "run" not in commands:
        raise ValueError("EXECUTION_AUTHORITY requires bootstrap and run kernel commands")


def _validate_legacy_bridge(payload: Mapping[str, Any]) -> None:
    _require_keys(payload, ["lock_marker", "locked", "immutable", "runtime_guard", "runner", "schema"], "LEGACY_BRIDGE")
    if not str(payload.get("lock_marker", "")).strip():
        raise ValueError("LEGACY_BRIDGE requires a non-empty lock_marker")
    if not bool(payload.get("locked")) or not bool(payload.get("immutable")):
        raise ValueError("LEGACY_BRIDGE must be locked and immutable")


def _validate_bootstrap_authority(payload: Mapping[str, Any]) -> None:
    _require_keys(payload, ["bootstrap_entrypoint", "required_chain", "legacy_bridge_contract"], "BOOTSTRAP_AUTHORITY")
    chain = payload.get("required_chain")
    if not isinstance(chain, list) or not chain:
        raise ValueError("BOOTSTRAP_AUTHORITY required_chain must be a non-empty list")


def get_contracts() -> List[Dict[str, Any]]:
    return [
        {
            "name": "SCI_AUTHORITY",
            "version": "1.0",
            "payload": SCI_AUTHORITY_CONTRACT,
            "validator": _validate_sci_authority,
        },
        {
            "name": "EXECUTION_AUTHORITY",
            "version": "1.0",
            "payload": EXECUTION_AUTHORITY_CONTRACT,
            "validator": _validate_execution_authority,
        },
        {
            "name": "LEGACY_BRIDGE",
            "version": "1.0",
            "payload": LEGACY_BRIDGE_CONTRACT,
            "validator": _validate_legacy_bridge,
        },
        {
            "name": "BOOTSTRAP_AUTHORITY",
            "version": "1.0",
            "payload": BOOTSTRAP_AUTHORITY_CONTRACT,
            "validator": _validate_bootstrap_authority,
        },
    ]
