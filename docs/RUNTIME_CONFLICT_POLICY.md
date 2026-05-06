# RUNTIME_CONFLICT_POLICY

## Purpose

This document defines deterministic runtime behavior for conflict conditions.
This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any conflict exists, SYSTEM_CONTRACT_INDEX.md is authoritative.

## 1. Conflict Types

The system recognizes these conflict types:

1. SCI violation detected.
1. Data mismatch between LogicMonitor and validation outputs.
1. Canonical state versus evidence contradiction.
1. Missing or ambiguous identity (no IP).

## 2. Required Runtime Behavior

1. FAIL CLOSED on all SCI violations.
1. Immediately STOP affected execution scope.
1. QUARANTINE conflicting records and DO NOT delete them.
1. LOG structured conflict event.
1. DO NOT attempt automatic resolution unless SCI explicitly defines resolution rules.

No silent reconciliation is permitted.

## 3. Structured Conflict Event Schema

Every conflict event MUST include:

1. run_id
1. device_ip
1. conflict_type
1. source_systems
1. timestamp

Additional required fields:

1. execution_mode
1. guard_state
1. blocked_scope
1. status

## 4. Deterministic Conflict Flow

Conflict handling MUST follow this sequence:

1. Detect conflict.
1. Classify conflict_type.
1. Stop affected scope.
1. Quarantine affected records.
1. Write structured conflict event.
1. Mark status as BLOCKED.
1. Escalate for SCI clarification or SCI update.

## 5. SCI Violation Handling

If any instruction, code generation, or runtime behavior conflicts with SCI:

1. Stop generation or execution immediately.
1. Mark output as BLOCKED due to SCI conflict.
1. Request clarification or SCI update.
1. Do not proceed with partial implementation.

## 6. Prohibited Runtime Behavior

1. Guessing intended behavior.
1. Merging conflicting rules without SCI authorization.
1. Prioritizing subordinate document logic over SCI.
1. Inferring missing authority or identity rules.

## 7. Safety Priority

Runtime policy priority order:

1. Safety over availability.
1. Blocking over guessing.
1. Explicit definition over inference.

If uncertainty exists, execution MUST be blocked.
