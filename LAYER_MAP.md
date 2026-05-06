# LAYER_MAP

## 1. Purpose

This document is the enforcement specification for system integrity.
All components MUST comply with these layer boundaries and flow rules.

SCI binding:

- This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
- If any conflict exists, SCI is authoritative and this document is descriptive for conflicting scope.

## 2. Canonical Layer Order

```text
Layer 0 -> Layer 1 -> Layer 2 -> Layer 3 -> Layer 4
                         ^             |
                         |             v
                      Layer 6 <-> Layer 5
```

Operational interpretation:

1. Active pipeline order is Layer 0 to Layer 4.
2. Layer 5 is a governance gate over execution and change control.
3. Layer 6 is future source-of-truth state and is not authoritative yet.

## 3. Layer Definitions

### Layer 0: External Sources

Components:

- LogicMonitor (discovery only)
- Network devices (runtime state)

Responsibilities:

- Provide raw discovery observations and runtime device state.

Boundary constraints:

- LogicMonitor input is untrusted until validated downstream.
- Layer 0 MUST NOT define canonical inventory.
- Layer 0 MUST NOT trigger execution directly.

### Layer 1: Ingestion Layer (Python)

Responsibilities:

- Pull from LogicMonitor.
- Normalize vendor and identity fields.
- Output structured inventory JSON.

Allowed outputs:

- Structured inventory JSON only.

Hard prohibitions:

- NO SSH.
- NO DB writes.
- NO direct calls to Ansible execution.

### Layer 2: Normalization Layer

Responsibilities:

- Apply schema enforcement.
- Build Ansible inventory from structured JSON.

Allowed behavior:

- Deterministic transformation and validation only.

Hard prohibitions:

- NO external calls.
- NO direct device access.
- NO persistence writes.

### Layer 3: Execution Layer (Ansible)

Responsibilities:

- SSH validation.
- Fact collection.
- Read-only operations.

Allowed behavior:

- Execute validation playbooks against target IPs.
- Return evidence and execution status.

Hard prohibitions:

- NO inventory definition logic.
- NO canonical state ownership.
- NO configuration changes unless explicitly enabled by Layer 5 policy.

### Layer 4: Canonical State Layer (PostgreSQL via API)

Responsibilities:

- Store validated state.
- Track history.
- Maintain `last_seen`.

Allowed behavior:

- Persist validated records and execution evidence.
- Serve canonical state through API contracts.

Hard prohibitions:

- NO direct writes from Layer 0, Layer 1, or ad hoc scripts.
- NO schema bypass outside approved API contracts.

### Layer 5: Governance Layer

Responsibilities:

- Enforce `automation_enabled` flag.
- Provide future approval workflow.

Allowed behavior:

- Gate all mutation-capable execution paths.
- Block execution on policy ambiguity (fail closed).

Hard prohibitions:

- NO silent override of audit controls.
- NO bypass of mode/approval checks.

### Layer 6: Source of Truth Layer (Nautobot future state)

Responsibilities:

- Maintain planned network model.
- Provide future desired-state source.

Current authority status:

- Not authoritative yet.

Hard prohibitions (current phase):

- NO direct authority over active canonical state.
- NO direct configuration deployment without approved cutover.

## 4. Strict Global Rules

1. No layer skipping is allowed.
2. `ip_address` is the identity key.
3. LogicMonitor is untrusted input and discovery-only.
4. Ansible is execution-only.
5. Nautobot is future source-of-truth only and not yet authoritative.

## 5. Data and Control Flow Contracts

### 5.1 Required data flow

```text
Layer 0 -> Layer 1 -> Layer 2 -> Layer 3 -> Layer 4
```

### 5.2 Governance flow

```text
Layer 5 gates Layer 3 mutation paths.
```

### 5.3 Future SoT flow (post-cutover only)

```text
Layer 6 -> Layer 5 -> Layer 3
```

## 6. Forbidden Flows (Incorrect Patterns)

The following flows are forbidden and MUST be rejected:

1. `Layer 0 (LogicMonitor) -> Layer 3 (Ansible)`
Reason: bypasses ingestion and normalization controls.

2. `Layer 1 (Python ingestion) -> Layer 4 (PostgreSQL)`
Reason: Layer 1 has NO DB write authority.

3. `Layer 2 (Normalization) -> External APIs or devices`
Reason: Layer 2 is transformation-only and has NO external call permission.

4. `Layer 3 (Ansible) -> Inventory authority`
Reason: execution layer cannot define or own inventory.

5. `Layer 3 (Ansible) -> Config mutation while governance is not enabled`
Reason: read-only mode is default; change execution requires Layer 5 approval.

6. `Layer 6 (Nautobot) -> Layer 3 direct control (current phase)`
Reason: Nautobot is not authoritative yet.

7. `Any layer -> skip to non-adjacent downstream layer`
Reason: layer skipping breaks validation and audit guarantees.

## 7. Enforcement Requirements

1. Every pipeline run MUST record source layer, target layer, run ID, target IPs, and result.
2. Any prohibited flow attempt MUST be blocked and logged.
3. Compliance checks MUST fail closed on missing identity (`ip_address`) or missing governance state.
4. Boundary violations MUST be treated as integrity failures.
