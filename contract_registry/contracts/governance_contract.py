"""Governance contracts for CI and runtime gate behavior."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set


SCI_RULES_CONTRACT: Dict[str, Any] = {
    "registry_name": "SCI Governance Rule Contract",
    "registry_version": "1.0",
    "schema_version": "1.0",
    "validator_bindings": {
        "SCI_VALIDATOR": {"module": "ci.sci_validator", "callable": "run"},
        "IMPORT_GUARD": {"module": "ci.import_guard", "callable": "run"},
        "BOOTSTRAP_GATE": {"module": "ci.bootstrap_gate", "callable": "run"},
        "SCHEMA_VERSION_GUARD": {"module": "ci.schema_version_guard", "callable": "run"},
        "EXECUTION_CONTRACT_INTEGRITY_GUARD": {
            "module": "ci.execution_contract_integrity_guard",
            "callable": "run",
        },
        "CI_PIPELINE_ORDER_GUARD": {"module": "ci.ci_pipeline_order_guard", "callable": "run"},
    },
    "rules": [
        {
            "rule_id": "SCI-001",
            "name": "SCI Presence Validation",
            "description": "SCI authority contract must be valid before runtime and CI gate execution.",
            "severity": "CRITICAL",
            "enforcement": {"type": "CI_VALIDATION"},
            "validator_binding": {"validator_id": "SCI_VALIDATOR"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 1,
        },
        {
            "rule_id": "SCI-002",
            "name": "Import Boundary Enforcement",
            "description": "Import boundaries are governed by contract, not filesystem traversal.",
            "severity": "CRITICAL",
            "enforcement": {"type": "IMPORT_GUARD"},
            "validator_binding": {"validator_id": "IMPORT_GUARD"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 2,
        },
        {
            "rule_id": "SCI-003",
            "name": "Canonical Kernel Execution Enforcement",
            "description": "Kernel bootstrap authority must be contract-resolved.",
            "severity": "CRITICAL",
            "enforcement": {"type": "RUNTIME_GUARD"},
            "validator_binding": {"validator_id": "BOOTSTRAP_GATE"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 3,
        },
        {
            "rule_id": "SCI-004",
            "name": "Schema Version 2.1 Enforcement",
            "description": "Execution schema contract version must remain 2.1.",
            "severity": "CRITICAL",
            "enforcement": {"type": "SCHEMA_GUARD"},
            "validator_binding": {"validator_id": "SCHEMA_VERSION_GUARD"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 4,
        },
        {
            "rule_id": "SCI-005",
            "name": "Execution Contract Integrity",
            "description": "Execution authority and legacy bridge contracts must be immutable.",
            "severity": "CRITICAL",
            "enforcement": {"type": "IMPORT_GUARD"},
            "validator_binding": {"validator_id": "EXECUTION_CONTRACT_INTEGRITY_GUARD"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 5,
        },
        {
            "rule_id": "SCI-006",
            "name": "CI Pipeline Ordering Enforcement",
            "description": "CI ordering must run EMV before policy engine under kernel governance contract.",
            "severity": "CRITICAL",
            "enforcement": {"type": "CI_VALIDATION"},
            "validator_binding": {"validator_id": "CI_PIPELINE_ORDER_GUARD"},
            "fail_behavior": "FAIL_CLOSED",
            "dependencies": ["SCI", "EXECUTION_PRECEDENCE_CONTRACT"],
            "version": "1.0",
            "execution_order": 6,
        },
    ],
}


CI_KERNEL_RUNTIME_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "execution_mode": "CI_ENFORCEMENT_KERNEL",
    "meta_validation_required": True,
    "fail_closed": True,
    "meta_validator_contract": "SCI_RULES",
}


IMPORT_BOUNDARY_RULES_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "allowed_execution_contract_edges": {
        "legacy.execution_contract.schema": [
            "legacy.execution_contract.execution_state",
            "legacy.execution_contract.failure_taxonomy",
        ],
        "legacy.execution_contract.ansible_error_mapper": ["legacy.execution_contract.failure_taxonomy"],
        "legacy.execution_contract.failure_taxonomy": ["legacy.execution_contract.execution_state"],
        "legacy.execution_contract.runtime_guard": [],
        "legacy.execution_contract.execution_state": [],
        "legacy.execution_contract.__init__": [],
    },
    "runtime_guard_import_allowed": [
        "bootstrap",
        "legacy.execution_contract.runtime_guard",
    ],
    "runner_entry_allowed": [
        "bootstrap",
        "legacy.ansible_validation.runners.run_validation",
    ],
}


CI_PIPELINE_ORDER_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "required_entrypoint": "CI_KERNEL",
    "required_execution_mode": "CI_ENFORCEMENT_KERNEL",
    "require_emv_before_policy_engine": True,
    "forbid_direct_validator_execution": True,
}


GOVERNANCE_POLICY_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "read_only_mode": "READ_ONLY",
    "mutation_allowed_phase": "PHASE_3",
    "approval_required_for_mutation": True,
}


def _validate_sci_rules(payload: Mapping[str, Any]) -> None:
    schema_version = str(payload.get("schema_version", ""))
    registry_version = str(payload.get("registry_version", ""))
    if schema_version != "1.0" or registry_version != "1.0":
        raise ValueError("SCI_RULES schema_version and registry_version must both be 1.0")

    bindings = payload.get("validator_bindings")
    if not isinstance(bindings, Mapping) or not bindings:
        raise ValueError("SCI_RULES validator_bindings must be a non-empty mapping")

    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("SCI_RULES rules must be a non-empty list")

    rule_ids: Set[str] = set()
    execution_orders: Set[int] = set()
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise ValueError("SCI_RULES contains a non-mapping rule")

        rule_id = str(rule.get("rule_id", "")).strip()
        if not rule_id:
            raise ValueError("SCI_RULES rule_id is required")
        if rule_id in rule_ids:
            raise ValueError(f"SCI_RULES duplicate rule_id: {rule_id}")
        rule_ids.add(rule_id)

        binding = rule.get("validator_binding")
        if not isinstance(binding, Mapping):
            raise ValueError(f"SCI_RULES {rule_id} missing validator_binding")

        validator_id = str(binding.get("validator_id", "")).strip()
        if validator_id not in bindings:
            raise ValueError(f"SCI_RULES {rule_id} references unknown validator_id: {validator_id}")

        try:
            execution_order = int(rule.get("execution_order"))
        except Exception as exc:
            raise ValueError(f"SCI_RULES {rule_id} execution_order must be integer") from exc

        if execution_order in execution_orders:
            raise ValueError(f"SCI_RULES duplicate execution_order: {execution_order}")
        execution_orders.add(execution_order)

        if str(rule.get("version", "")) != schema_version:
            raise ValueError(f"SCI_RULES {rule_id} version must match schema_version")

    expected_orders = set(range(1, len(rules) + 1))
    if execution_orders != expected_orders:
        raise ValueError("SCI_RULES execution_order values must be contiguous starting at 1")


def _validate_ci_kernel_runtime(payload: Mapping[str, Any]) -> None:
    if str(payload.get("execution_mode", "")) != "CI_ENFORCEMENT_KERNEL":
        raise ValueError("CI_KERNEL_RUNTIME execution_mode must be CI_ENFORCEMENT_KERNEL")
    if not bool(payload.get("meta_validation_required")):
        raise ValueError("CI_KERNEL_RUNTIME meta_validation_required must be true")
    if not bool(payload.get("fail_closed")):
        raise ValueError("CI_KERNEL_RUNTIME fail_closed must be true")


def _validate_import_boundaries(payload: Mapping[str, Any]) -> None:
    edges = payload.get("allowed_execution_contract_edges")
    if not isinstance(edges, Mapping) or not edges:
        raise ValueError("IMPORT_BOUNDARY_RULES requires allowed_execution_contract_edges mapping")


def _validate_pipeline_order(payload: Mapping[str, Any]) -> None:
    if str(payload.get("required_entrypoint", "")) != "CI_KERNEL":
        raise ValueError("CI_PIPELINE_ORDER required_entrypoint must be CI_KERNEL")
    if not bool(payload.get("require_emv_before_policy_engine")):
        raise ValueError("CI_PIPELINE_ORDER must require EMV before policy engine")


def _validate_governance_policy(payload: Mapping[str, Any]) -> None:
    if str(payload.get("read_only_mode", "")) != "READ_ONLY":
        raise ValueError("GOVERNANCE_POLICY read_only_mode must be READ_ONLY")
    if str(payload.get("mutation_allowed_phase", "")) != "PHASE_3":
        raise ValueError("GOVERNANCE_POLICY mutation_allowed_phase must be PHASE_3")


def get_contracts() -> List[Dict[str, Any]]:
    return [
        {
            "name": "SCI_RULES",
            "version": "1.0",
            "payload": SCI_RULES_CONTRACT,
            "validator": _validate_sci_rules,
        },
        {
            "name": "CI_KERNEL_RUNTIME",
            "version": "1.0",
            "payload": CI_KERNEL_RUNTIME_CONTRACT,
            "validator": _validate_ci_kernel_runtime,
        },
        {
            "name": "IMPORT_BOUNDARY_RULES",
            "version": "1.0",
            "payload": IMPORT_BOUNDARY_RULES_CONTRACT,
            "validator": _validate_import_boundaries,
        },
        {
            "name": "CI_PIPELINE_ORDER",
            "version": "1.0",
            "payload": CI_PIPELINE_ORDER_CONTRACT,
            "validator": _validate_pipeline_order,
        },
        {
            "name": "GOVERNANCE_POLICY",
            "version": "1.0",
            "payload": GOVERNANCE_POLICY_CONTRACT,
            "validator": _validate_governance_policy,
        },
    ]
