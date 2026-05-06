# SYSTEM_OVERVIEW

## 1. Scope

This repository implements a production network automation system with two pipelines:

- Pipeline A (active): Discovery + Validation
- Pipeline B (future): Control Plane

All behavior in this repository MUST follow the constraints in this document.

SCI authority note:

- This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
- If any conflict exists, SCI is authoritative and this document is descriptive for conflicting scope.

## 2. High-Level Architecture (Text Diagram)

```text
PIPELINE A (ACTIVE: DISCOVERY + VALIDATION)

[LogicMonitor]
    |
    | discovery payloads (non-authoritative)
    v
[Python Ingestion]
    |
    | normalized records keyed by IP
    v
[Normalized Inventory]
    |
    | validation targets and metadata
    v
[Ansible Validation]
    |
    | validation results, evidence, status
    v
[PostgreSQL API]


PIPELINE B (FUTURE: CONTROL PLANE)

[Nautobot]
    |
    | desired state / intent (future input; non-authoritative until SCI cutover)
    v
[Ansible Execution]
    |
    | approved tasks only
    v
[Network Devices]
```

## 3. Layer Responsibilities (Strict Separation)

### 3.1 LogicMonitor Layer

- Purpose: Device and state discovery.
- Allowed actions: Read and export observations.
- Prohibited actions: Authority decisions, execution targeting, configuration intent generation.
- Authority status: Discovery-only, not authoritative.

### 3.2 Python Ingestion Layer

- Purpose: Parse, normalize, and validate incoming discovery data.
- Allowed actions: Data quality checks, schema normalization, deduplication by IP.
- Prohibited actions: Device configuration, source-of-truth override.

### 3.3 Normalized Inventory Layer

- Purpose: Canonical inventory representation for Pipeline A operations.
- Allowed actions: Store normalized records and target metadata keyed by IP.
- Prohibited actions: Treating non-IP attributes as identity.

### 3.4 Ansible Layer

- Purpose: Execution engine.
- Allowed actions (current): Validation/fact collection only.
- Allowed actions (future with explicit enablement): Controlled configuration tasks.
- Prohibited actions: Inventory authority and source-of-truth ownership.
- Authority status: Execution-only, never authoritative for inventory.

### 3.5 PostgreSQL API Layer

- Purpose: Current canonical state store for validated runtime data and result serving API.
- Allowed actions: Persist normalized inventory state, validation evidence, execution metadata.
- Prohibited actions: Bypassing normalization policy.
- Authority status: Authoritative only for validated runtime state.

### 3.6 Nautobot Layer (Future)

- Purpose: Future control-plane source of truth for desired state.
- Current status: Integrated as future path only.
- Authority status: Not authoritative until SCI-approved cutover.

## 4. Identity Model

Identity is IP-based only.

Mandatory rules:

1. `ip_address` is the only identity key across all layers.
2. All deduplication, correlation, and targeting MUST resolve to `ip_address`.
3. Hostname, serial, MAC, asset tags, and vendor-native IDs are attributes only.
4. Any workflow that uses non-IP identity as primary key MUST fail validation.

## 5. Data Flow Description

### 5.1 Pipeline A (Active)

1. LogicMonitor exports discovery observations.
2. Python ingestion validates payload schema and normalizes fields.
3. Normalized inventory stores records keyed by `ip_address`.
4. Ansible runs read-only validation against target IPs.
5. Validation outputs are written to PostgreSQL API for canonical validated runtime state and reporting.

### 5.2 Pipeline B (Future)

1. Nautobot emits desired-state intent (post-cutover only).
2. Ansible consumes approved intent and target scope.
3. Network devices receive changes only when change execution is explicitly enabled.

## 6. Authority and Control Statements

- LogicMonitor is discovery-only and not authoritative.
- Nautobot is the future source of truth and is not yet authoritative.
- Ansible is execution-only and is never inventory authority.
- PostgreSQL is the current canonical state store for validated runtime state.
- Cross-layer shortcuts are prohibited.

## 7. SCI Execution Modes

Only these mutually exclusive global execution modes are valid:

### 7.1 READ_ONLY

- Discovery and validation are allowed.
- Configuration mutation is blocked.

### 7.2 VALIDATION_ONLY

- Ansible read-only validation is allowed.
- Discovery-triggered inventory refresh and all mutation paths are blocked.

### 7.3 CHANGE_ENABLED

- Configuration mutation is allowed only with global guard open and SCI permit.

If mode is undefined, default to READ_ONLY.

## 8. Vendor Scope and Priority

Priority order for implementation and support:

1. Aruba (highest)
2. Cisco
3. Fortinet
4. Juniper
5. Meraki

All vendor adapters MUST conform to the same identity and authority rules.

## 9. Guardrails (Operational)

1. Default operation is READ_ONLY.
2. Global automation guard MUST exist and fail closed.
3. Configuration changes are disabled unless explicitly enabled by approved process.
4. Target scope MUST be resolved from canonical IP-based inventory.
5. Missing guard state, invalid mode, or failed SCI compliance check MUST block execution.

## 10. Repository Structure

```text
inventory/
playbooks/
roles/
  aruba/
  cisco/
  fortinet/
  juniper/
  meraki/
scripts/
api_client/
docs/
logs/
```

This structure is mandatory for new components added to this repository.
