# Data Authority and Identity Policy

## Policy Statement

This repository enforces strict separation of discovery, canonical state, and execution. Authority is assigned by system role, not by convenience.

SCI binding:

- This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
- If any conflict exists, SCI is authoritative and this document is descriptive for conflicting scope.

## Canonical Rules

1. `ip_address` is the only identity key.
2. LogicMonitor is discovery-only and never authoritative.
3. Ansible is execution-only and never inventory authority.
4. Nautobot is future source of truth and is not authoritative until SCI-approved cutover.
5. PostgreSQL is the current canonical state store for validated runtime state.

## Identity Requirements

Mandatory requirements:

- Every managed entity MUST have exactly one canonical `ip_address` identity.
- Canonical records MUST be deduplicated by `ip_address`.
- Any record without a valid `ip_address` MUST be quarantined and excluded from execution workflows.

Explicitly non-identity fields:

- `hostname`
- `fqdn`
- `serial_number`
- `mac_address`
- `asset_tag`
- `vendor_device_id`

These attributes MAY support enrichment, but MUST NOT be used as primary keys.

## Source-of-Truth Ownership

| Data Domain | Current Authority | Future Authority | Notes |
| --- | --- | --- | --- |
| Device existence (by IP) | PostgreSQL canonical state | Nautobot | Derived from discovery + validation gates |
| Discovery observations | LogicMonitor (input only) | LogicMonitor (input only) | Observational, non-authoritative |
| Validation outcomes | PostgreSQL canonical state | PostgreSQL or delegated evidence store | Produced by Ansible validation runs |
| Desired configuration intent | Not active yet | Nautobot | Disabled until formal enablement |
| Change execution logs | PostgreSQL evidence tables | Same or centralized audit platform | Immutable audit trail required |

## Reconciliation Rules

1. Discovery data enters staging only after schema validation.
2. Canonical updates require normalization and policy checks.
3. Conflicts are resolved in canonical state according to policy precedence.
4. Execution consumes canonical state snapshots, never raw discovery feed.
5. Reconciliation MUST be deterministic and reproducible by run ID.

## Prohibited Shortcuts

The following are forbidden:

- Using LogicMonitor payloads directly as execution inventory.
- Allowing Ansible dynamic inventory to become persistent authority.
- Writing canonical state from execution-side parsing without ingestion policy controls.
- Bypassing PostgreSQL canonical checks when selecting targets.

## Audit and Traceability Requirements

Each pipeline run MUST capture:

- Run ID
- Trigger source
- Operator/service principal
- Target IP set
- Mode (`READ_ONLY`, `VALIDATION_ONLY`, or `CHANGE_ENABLED`)
- Guard state
- Per-target result and timestamp

## Cutover Preconditions for Nautobot Authority

Nautobot authority can be enabled only when:

1. SCI explicitly approves cutover.
2. Data quality SLOs are met for required period.
3. Rollback path to PostgreSQL canonical mode is tested.
4. Stakeholders approve cutover in writing.

Until then, PostgreSQL remains canonical and enforcement must stay active.
