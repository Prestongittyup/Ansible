# SYSTEM CONTRACT INDEX (SCI)

## Status

- Authority level: Highest
- Scope: Entire repository
- Enforcement: Mandatory

## 1. Authority and Precedence

SCI is the highest authority document in this repository.
If any contradiction exists between documents, SCI wins.

Document precedence order (highest to lowest):

1. SYSTEM_CONTRACT_INDEX.md (SCI)
2. ARCHITECTURE / LAYER BOUNDARIES
3. DATA AUTHORITY AND IDENTITY POLICY
4. OPERATIONAL GUARDRAILS AND EXECUTION SAFETY
5. RFC-001
6. SPRINT_PLAN

No other document may override SCI-defined rules.

Fail-closed rule:

- If SCI is missing or ambiguous, execution MUST STOP.

## 2. Canonical Truth Rule (Zero Ambiguity Policy)

There is exactly one canonical definition for each system concept.

Canonical sources:

- Identity model: Data Authority and Identity Policy
- Layer behavior: Architecture / Layer Boundaries
- Execution safety: Operational Guardrails
- System structure: Architecture document
- Delivery sequence: Sprint Plan
- Conflict resolution: SCI only

If a rule is defined in multiple places:

1. SCI determines which definition is valid.
2. Other definitions are descriptive and non-authoritative.

Inference prohibition:

- Copilot and automation components MUST NOT infer missing authority rules.

## 3. Anti-Contradiction Rule

The system does not reason through ambiguity.

If conflicting statements exist, SCI explicitly resolves which one is valid.
If SCI does not explicitly resolve the conflict, output and execution MUST be blocked.

Resolution baseline for canonical state wording:

- PostgreSQL is the current canonical state store for validated runtime state.
- This status remains active until SCI-approved cutover to Nautobot authority.

No component may guess a reconciliation outside SCI.

## 4. Authority Model (Strict Hierarchy)

Authority is assigned only as follows:

- LogicMonitor: Discovery only, never authoritative.
- Python ingestion: Transformation only, never authoritative.
- Ansible: Execution only, never inventory authority.
- PostgreSQL: Current canonical state store, authoritative only for validated runtime state.
- Nautobot: Future source of truth, not authoritative until cutover is explicitly approved in SCI.

Any deviation from this hierarchy is a policy violation.

## 5. Execution Mode System (Single Source of Truth)

Only three execution modes are valid:

1. READ_ONLY

- Discovery and validation allowed.
- Configuration changes prohibited.

1. VALIDATION_ONLY

- Ansible read-only execution allowed.
- No mutation permitted.

1. CHANGE_ENABLED

- Configuration changes allowed.
- Allowed only when global guard is open and SCI permits.

Mode rules:

- Modes are mutually exclusive.
- Modes are globally defined.
- If mode is undefined, default to READ_ONLY (fail-closed).

No other execution mode definitions are valid in the repository.

## 6. Global Guard Rule

Before any execution step, all checks below are required:

1. Current execution mode is valid.
2. Global automation guard state is valid.
3. Target IP list is sourced from canonical state.
4. SCI compliance check passes.

If any check fails, execution MUST STOP immediately.

## 7. Prohibited Behavior

The following behaviors are non-negotiable violations:

1. Inventing new authority rules.
2. Resolving contradictions outside SCI.
3. Treating descriptive text as executable logic.
4. Introducing new execution modes.
5. Overriding IP-only identity model.
6. Bypassing layer boundaries.
7. Assuming Nautobot authority before SCI-approved cutover.

## 8. Required Behavior

Mandatory behavior for all implementation and review work:

1. Consult SCI first when ambiguity exists.
2. Treat all non-SCI documents as subordinate.
3. Default to fail-closed behavior on uncertainty.
4. Enforce IP-only identity model.
5. Preserve strict pipeline separation.
6. Use PostgreSQL only for canonical validated runtime state.
7. Ensure Ansible remains execution-only.

## 9. Drift Prevention Principle

If a decision cannot be traced to:

- SCI, or
- exactly one canonical source document,

then it MUST NOT be implemented.
The change MUST be rejected or deferred.

## 10. Summary Rule

This system does not resolve ambiguity by assumption.
It blocks ambiguity.
SCI is the only authority that resolves conflict.
