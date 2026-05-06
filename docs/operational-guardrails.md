# Operational Guardrails and Execution Safety

## Objective

Define fail-closed controls for read-only operation and controlled enablement of change execution.

## SCI Authority Binding

This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any statement in this document conflicts with SCI, SCI is authoritative and this document is descriptive for the conflicting scope.

## Default Operating Mode

The platform starts in READ_ONLY mode.

Required defaults:

- `AUTOMATION_MODE=READ_ONLY`
- `GLOBAL_AUTOMATION_GUARD=enabled`
- `CHANGE_EXECUTION_ENABLED=false`

Interpretation:

- Discovery and validation may run in READ_ONLY mode.
- Configuration changes are blocked.
- Any ambiguous state is treated as blocked (fail closed).

## Global Automation Guard

A global guard MUST exist and be evaluated before any execution step that could alter device state.

Guard requirements:

1. Single global control point checked by all execution entry points.
2. Fail-closed behavior on missing config, parse errors, or policy mismatch.
3. Immutable audit log for all guard decisions.
4. Guard evaluation included in every run summary.

## Allowed Actions by Mode

| Mode | Discovery | Validation | Config Change |
| --- | --- | --- | --- |
| `READ_ONLY` | Allowed | Allowed | Blocked |
| `VALIDATION_ONLY` | Blocked | Allowed | Blocked |
| `CHANGE_ENABLED` | Allowed | Allowed | Allowed only with guard open, SCI permit, and approved change window |

## Ansible Execution Constraints

Read-only behavior:

- Validation playbooks MUST gather facts, run checks, and collect evidence only.
- Any task that mutates configuration MUST be skipped or blocked.
- Dry-run semantics (`--check` where supported) SHOULD be used for safety.

Change-enabled behavior:

- Requires explicit mode change and approved change window.
- Requires target scope verification by canonical IP inventory.
- Requires pre-check and post-check evidence capture.
- Requires SCI permit for change execution.

Validation-only behavior:

- Allows Ansible read-only execution for targeted validation.
- Disables discovery-triggered inventory refresh and all mutation paths.

## Required Pre-Execution Gates

Before any playbook starts:

1. Confirm operating mode is one of READ_ONLY, VALIDATION_ONLY, or CHANGE_ENABLED.
2. Confirm global guard status.
3. Confirm target list sourced from canonical state by IP.
4. Confirm SCI compliance check passed.

If any gate fails, execution MUST stop with a blocked status.

## Emergency Stop

A global stop action MUST be available to immediately block all automation execution paths.

Emergency stop behavior:

- Blocks new runs immediately.
- Signals in-flight runs to halt at next safe checkpoint.
- Emits high-priority alert with reason and timestamp.

## Change Enablement Process

To move from READ_ONLY or VALIDATION_ONLY to CHANGE_ENABLED, all steps are required:

1. Formal approval recorded.
2. Scope and risk review completed.
3. Guard state changed by authorized operator.
4. SCI permit confirmed for the requested change scope.
5. Audit event generated with approver and expiration time.
6. Post-change verification and rollback readiness confirmed.

If approval expires or any verification fails, mode MUST return to READ_ONLY.

## Vendor Safety Application

Guardrails apply equally to all in-scope vendors:

1. Aruba (highest priority)
2. Cisco
3. Fortinet
4. Juniper
5. Meraki

No vendor integration may bypass global guard checks.
