# EVIDENCE_VS_STATE_MODEL

## Purpose

This document defines strict separation between canonical state and evidence data.
This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any conflict exists, SYSTEM_CONTRACT_INDEX.md is authoritative.

## 1. Data Domain A: CANONICAL STATE

Source:

- PostgreSQL

Purpose:

- Authoritative current system state using IP-based identity.
- Execution targeting and system decisions.

Required properties:

1. Canonical records MUST use ip_address as identity key.
1. Canonical records MUST be decision-grade and normalized.
1. Canonical state MUST be authoritative only for validated runtime state.

## 2. Data Domain B: EVIDENCE LAYER

Source:

- Ansible validation outputs
- Structured logs

Purpose:

- Auditability
- Verification history
- Runtime traceability

Required properties:

1. Evidence records MUST be immutable or append-only within retention policy.
1. Evidence records MUST NOT be treated as canonical state.
1. Evidence records MUST support run-based traceability.

## 3. Domain Separation Rules

1. Every record MUST be classified as either STATE or EVIDENCE.
1. Evidence MUST NEVER overwrite canonical state directly.
1. State MUST only be updated via ingestion plus normalization plus validation pipeline.
1. Evidence MUST NOT influence canonical state directly without normalization pipeline.
1. Mixing evidence and state is a violation of SCI model.

## 4. Allowed State Update Path

Only this path is valid for canonical state mutation:

1. Ingestion
1. Normalization
1. Validation
1. PostgreSQL canonical write through approved API path

Any direct EVIDENCE to STATE write is prohibited.

## 5. Execution and Decision Use

1. Execution targeting MUST read from CANONICAL STATE only.
1. Operational decisions MUST read from CANONICAL STATE only.
1. EVIDENCE LAYER MAY be used for audit, diagnostics, and verification only.

## 6. Violation Handling

The following outcomes are mandatory on separation violations:

1. Block affected execution scope.
1. Quarantine conflicting records.
1. Log structured violation event.
1. Require SCI-backed resolution path before unblocking.
