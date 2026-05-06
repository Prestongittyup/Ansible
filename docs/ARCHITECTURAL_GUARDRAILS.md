# ARCHITECTURAL_GUARDRAILS

## Purpose

This document defines non-negotiable controls that prevent architectural drift, including human and AI-generated drift.
All rules in this document are mandatory.

SCI binding:

- This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
- If any conflict exists, SCI is authoritative and this document is descriptive for conflicting scope.

Normative language:

- MUST: required behavior
- MUST NOT: prohibited behavior

## 1. Core Invariants

The following invariants are permanent system constraints:

1. IP-based identity only.
2. Strict layer separation.
3. LogicMonitor is not source of truth.
4. Ansible is execution only.
5. Nautobot is future state only and not yet authoritative.

## 2. Forbidden Behaviors

The following behaviors are always prohibited:

1. Skipping ingestion layer.
2. Writing directly from LogicMonitor to PostgreSQL.
3. Using hostname as primary key.
4. Pushing unvalidated data into Nautobot.
5. Mixing execution and inventory-definition logic in the same component.

Any occurrence is a policy violation and MUST block merge or runtime execution.

## 3. Required Behaviors

The following behaviors are mandatory:

1. All inventory flows MUST pass through ingestion layer.
2. All validation MUST be Ansible-driven.
3. All persistent state MUST go through PostgreSQL API.
4. All secrets MUST be stored in Ansible Vault.
5. All logs MUST be JSON structured.

Implementations that do not satisfy all five requirements are non-compliant.

## 4. Automation Safety Rules

Safety controls are mandatory for all execution paths:

1. Global automation_enabled flag is required.
2. No configuration changes unless explicitly enabled.
3. Approval gate is required for future-state execution paths.

Fail-closed rule:

- Missing flag, ambiguous mode, or missing approval MUST block execution.

## 5. Vendor Handling Rules

Vendor controls are mandatory:

1. Vendor-specific modules are required.
2. No generic abstraction layer is allowed for vendor execution logic.

In-scope vendors and priority order:

1. Aruba
2. Cisco
3. Fortinet
4. Juniper
5. Meraki

## 6. Logging Requirements

Logging requirements are mandatory for every pipeline stage:

1. Structured JSON logs.
2. Failure capture is required.
3. Correlation via device IP is required.

Minimum required log fields:

- timestamp
- run_id
- stage
- device_ip
- status
- error_code
- error_message
- mode

## 7. Enforcement

Compliance checks MUST run in CI and at runtime gate points.

Required enforcement actions:

1. Reject changes that violate any invariant or required behavior.
2. Block runtime actions that violate safety rules.
3. Record violations as structured JSON events.
4. Require corrective change before re-enable.

No exception path may bypass these controls without formal approval and recorded audit evidence.
