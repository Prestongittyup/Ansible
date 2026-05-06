# SCI_ENFORCEMENT_INTERFACE

## Authority Binding

This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any conflict exists, SYSTEM_CONTRACT_INDEX.md is authoritative.

SCI is a runtime enforcement contract.
All execution, validation, automation, and code generation MUST pass through SCI enforcement.

## 1. Core Interface

Single enforcement function:

SCI_VALIDATE(context) -> ALLOW | BLOCK

This function is mandatory for runtime, CI, and execution gates.
No component may bypass SCI_VALIDATE.

## 2. Required Input Schema

Each SCI_VALIDATE request MUST include all fields below:

1. run_id (string, required)
1. device_ip (string, required)
1. execution_mode (READ_ONLY | VALIDATION_ONLY | CHANGE_ENABLED)
1. source_layer (string)
1. target_action (string)
1. data_classification (STATE | EVIDENCE)
1. timestamp (string, required)

Additional enforcement fields:

1. global_automation_guard (string, required for effective guard validation)
1. sci_permit (bool, required when execution_mode is CHANGE_ENABLED)

## 3. Mandatory Validation Rules

SCI_VALIDATE MUST enforce all rules below:

1. SCI existence and accessibility.
1. Execution mode validity (only READ_ONLY, VALIDATION_ONLY, CHANGE_ENABLED).
1. IP-only identity validation (device_ip is required and must be valid IPv4 or IPv6).
1. Layer boundary compliance.
1. Evidence versus State classification correctness.
1. Global automation guard compliance.
1. No cross-layer shortcut violations.

## 4. Output Contract

SCI_VALIDATE returns only one decision:

1. ALLOW
1. BLOCK

No partial approval is permitted.

If decision is BLOCK, output MUST include:

1. reason_code
1. violated_rule
1. context_id (run_id)

Required machine output shape:

{
  "decision": "ALLOW" | "BLOCK",
  "reason_code": "string or null",
  "violated_rule": "string or null",
  "run_id": "string"
}

## 5. Fail-Closed Requirement

If SCI is missing, inaccessible, ambiguous, or validation cannot complete:

- SCI_VALIDATE MUST return BLOCK.
- Execution MUST NOT proceed.

No exceptions.

## 6. Runtime and CI Integration Contract

1. Runtime execution MUST call SCI_VALIDATE before action execution.
1. Ansible pre_tasks MUST call SCI_VALIDATE before any task execution.
1. CI pull request checks MUST block merge when SCI violations are detected.
1. Every blocked decision MUST emit structured conflict metadata.

## 7. Prohibited Behavior

1. Introducing execution modes outside SCI.
1. Trusting descriptive documents as executable authority.
1. Bypassing SCI_VALIDATE due to missing context.
1. Inferring missing authority rules.

All prohibited behavior MUST result in BLOCK.
