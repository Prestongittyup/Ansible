# Architecture and Layer Boundaries

## Purpose

This document defines the enforced architecture for the production automation platform. It is normative and uses MUST / MUST NOT requirements.

## SCI Authority Binding

This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any statement in this document conflicts with SCI, SCI is authoritative and this document is descriptive only for the conflicting scope.

## Two-Pipeline Model

### Pipeline A: Discovery + Validation (Current)

Flow:
LogicMonitor -> Python ingestion -> Normalized inventory -> Ansible validation -> PostgreSQL API

Responsibilities:

- LogicMonitor discovers device and interface data.
- Python ingestion normalizes and sanitizes discovery data.
- Normalized inventory maps all records to canonical schema keyed only by IP address.
- Ansible performs validation and evidence collection only.
- PostgreSQL stores current canonical validated runtime state and validation outcomes.

Constraints:

- Pipeline A MUST NOT push configuration changes.
- Pipeline A MUST treat LogicMonitor as non-authoritative input.
- Pipeline A MUST write canonical state only through normalization and validation workflows.

### Pipeline B: Control Plane (Future State)

Flow:
Nautobot -> Ansible -> Network devices

Responsibilities:

- Nautobot is intended future source of truth for desired state.
- Ansible executes approved intent from the control plane.
- Devices receive changes only after explicit change enablement.

Constraints:

- Pipeline B is not authoritative until SCI-approved cutover is completed.
- Pipeline B MUST remain blocked from config pushes unless global guard is explicitly opened.
- Pipeline B MUST consume authoritative intent only from approved source-of-truth state.

## Authority Matrix

| System | Role | Authority Status | Allowed Writes |
| --- | --- | --- | --- |
| LogicMonitor | Discovery input | Not authoritative | None to canonical truth |
| Python ingestion | Normalization and data quality | Transform-only | Normalized staging/canonical writes via policy |
| Ansible | Execution engine | Not authoritative | Validation outputs and run evidence only |
| PostgreSQL | Current canonical validated runtime state | Authoritative for validated runtime state only | Canonical inventory, validation status, evidence metadata |
| Nautobot | Future source of truth | Not authoritative until SCI-approved cutover | Desired state (when formally activated) |

## Identity Model

- The only identity key is `ip_address`.
- Hostname, serial number, MAC, and platform identifiers are attributes, not identity.
- Any process that tries to key entities by non-IP identifiers MUST fail validation.

## Boundary Rules

1. Discovery layer MUST NOT write directly to device control workflows.
2. Execution layer MUST NOT bypass canonical state.
3. Pipeline A and Pipeline B MUST communicate through explicitly defined data contracts.
4. No service may infer authority from convenience or data freshness.
5. Direct device changes from ad hoc scripts are prohibited.

## No Cross-Layer Shortcuts

Prohibited patterns include:

- LogicMonitor data directly driving change execution.
- Ansible generating its own persistent inventory as source of truth.
- Device state snapshots overwriting canonical state without normalization/validation gates.
- Any direct integration that skips PostgreSQL canonical checks in current state.

## Vendor Handling

In-scope vendors:

1. Aruba (highest priority)
2. Cisco
3. Fortinet
4. Juniper
5. Meraki

All vendor adapters MUST preserve the same identity and authority policies.

## Required Quality Gates

Before promoting any new automation behavior:

1. Validate identity contract (`ip_address` only).
2. Validate authority contract (correct source system for each field).
3. Validate guard contract (change path blocked unless explicitly enabled).
4. Validate auditability (run ID, operator, timestamp, target IPs, and results recorded).

## Execution Precedence Contract Integration

Runtime execution order is controlled by `EXECUTION_PRECEDENCE_CONTRACT.md` and is mandatory.

Execution sequence for Pipeline A runtime is fixed:

1. Bootstrap start
2. Runtime Guard pre-import validation
3. Import Boundary Lock
4. Runner orchestration start
5. Mapper deterministic transformation
6. Schema validation gate
7. Output emission

Execution precedence constraints:

1. `bootstrap.py` is the only valid runtime entrypoint.
2. `execution_contract/runtime_guard.py` is the only runtime pre-import enforcement authority.
3. `ansible_validation/runners/run_validation.py` is orchestration-only.
4. `execution_contract/ansible_error_mapper.py` performs deterministic classification only.
5. `execution_contract/schema.py` is the only output contract validation gate.

Any sequence deviation, authority overlap, or module reordering against this contract is a policy violation and MUST fail closed.

## CI Enforcement Boundary (Sprint 3 Entry Gate)

CI is a mandatory enforcement boundary for SCI and execution contract integrity.
CI is the highest pre-runtime enforcement layer.

CI hard gate controls:

1. SCI and execution contract presence and ambiguity validation.
2. Import boundary and cross-layer static enforcement.
3. Bootstrap-only execution path validation.
4. Schema version contract (`2.1`) validation gate checks.
5. Enforcement system self-integrity validation via EMV before kernel rule execution.

CI gate requirements:

1. CI checks run in deterministic order and fail closed on first violation.
2. No pull request may merge when any SCI or execution-contract violation exists.
3. Runtime guard and schema gate controls are validated at CI boundary before runtime execution.
4. No override path exists for CI failures.
5. CI kernel execution is prohibited unless EMV status is `PASS`.
6. EMV has higher precedence than CI kernel within CI enforcement execution order.
7. EMV failure blocks policy engine execution, validator execution, and runtime invocation.

Sprint 3 dependency note:

- Sprint 3 persistence work depends on CI gate pass against SCI and execution-contract rules.
- Contract-invalid outputs are blocked from entering mainline branches.
- Sprint 3 entry requires zero CI violations.

## Truth Boundary State Model

| Layer | Responsibility |
| --- | --- |
| SCI | Policy definition (what must happen) |
| CI Kernel | Enforcement interpretation (how policy is validated) |
| Runtime | Execution behavior (what actually runs) |

Strict interpretation rule:

1. SCI does not execute.
2. CI does not decide policy.
3. Runtime does not interpret policy or CI rules.

CI enforcement chain:

1. EMV validates CI enforcement structure.
2. CI kernel loads policy engine.
3. Policy engine resolves registry-to-validator bindings.
4. Validators execute in deterministic registry order.
5. CI decision state is emitted for sprint gate evaluation.

EMV fail-closed boundary:

1. If EMV status is `FAIL`, CI kernel does not load policy engine.
2. If EMV status is `FAIL`, no validator execution is permitted.
3. If EMV status is `FAIL`, runtime invocation paths remain blocked.

## Sprint 3 Consumer Contract Boundary

Formal boundary statement:

Sprint 3 operates strictly downstream of the CI Enforcement System and may only consume validated outputs produced after EMV and kernel execution.

Layer model:

[Enforcement Layer (IMMUTABLE)]
EMV -> CI Kernel -> Policy Engine -> Validators -> Schema Gate

    ↓ (ONLY OUTPUT CONTRACT)

[Consumer Layer (Sprint 3)]
Ingestion Consumers -> Storage -> Reporting -> APIs -> Analytics

Sprint 3 output dependency rules:

1. Consumer systems must treat CI output as immutable truth input.
2. Consumer systems must rely only on `schema_version`-validated outputs.
3. Consumer systems must reject unvalidated or pre-EMV data.
4. Consumer systems must treat CI output as authoritative and complete.

Sprint 3 enforcement freeze rules:

1. Sprint 3 must not modify EMV logic, CI kernel logic, policy engine logic, validator logic, registry semantics, or bootstrap flow.
2. Sprint 3 must not add enforcement layers, alter fail-closed behavior, or introduce new pre-kernel stages.
3. Any enforcement-layer change invalidates Sprint 3 consumer-contract assumptions.

Sprint 3 canonical persistence tracker update:

1. What changed: Added consumer-layer canonical persistence ingestion for validated CI outputs into PostgreSQL canonical state tables.
2. Why it changed: Provide durable, queryable canonical state for Pipeline A downstream consumers.
3. Risk removed: Eliminate dependence on transient JSON files as the only consumer truth source.
4. Risk introduced: Database consistency and transaction correctness become critical dependencies.
5. Sprint 3 impact: Enables reporting, reconciliation staging, and future Nautobot integration using persisted validated state.

Sprint 3 persistence dependency declarations:

1. Persistence is a consumer-only layer and has no authority to influence EMV, CI kernel, policy engine, validators, or bootstrap sequencing.
2. Persistence accepts only schema `2.1` validation output as canonical ingest input.
3. Persistence execution is explicitly downstream of EMV PASS and CI kernel PASS outcomes.
4. Any persistence-side schema or mapping extension must remain downstream and must not introduce upstream influence paths.

## Sprint 4 Query Layer Boundary

Query layer architectural role:

1. Query layer is a read-only consumer of persisted canonical state (`validation_state`) and immutable evidence (`validation_evidence`).
2. Query layer executes parameterized read queries and emits contract-validated output shapes.
3. Query layer provides deterministic ordering guarantees for list/evidence responses.

Hard rule:

1. Query layer MUST NOT mutate, reinterpret, recompute, or infer state.

Separation reinforcement:

1. Enforcement validates truth boundaries (EMV, CI kernel, validators).
2. Ingestion persists validated outputs into canonical state/evidence tables.
3. Query layer reads persisted canonical data only and cannot influence enforcement or ingestion decisions.

## Sprint 5 API Access Layer Boundary

API layer architectural role:

1. API layer is a read-only consumer of query-layer contracts and exposes canonical state/evidence endpoints for integration use.
2. API layer performs request/response contract validation and deterministic response ordering enforcement.
3. API layer must return JSON contract envelopes including `request_id`, `timestamp`, and `schema_version`.

Hard rules:

1. API layer MUST NOT access the database directly.
2. API layer MUST NOT execute SQL, write operations, schema mutation, or state recomputation.
3. API layer MUST call query service only and MUST NOT bypass query-layer validation semantics.

Separation reinforcement:

1. Enforcement defines and validates truth boundaries.
2. Ingestion persists validated truth into canonical storage.
3. Query layer provides deterministic read abstractions over canonical storage.
4. API layer exposes validated query outputs without mutating or reinterpreting data semantics.

## Sprint 6 Access Control Layer Boundary

Access control architectural role:

1. Access control layer is a pure API-boundary wrapper that performs authentication, authorization, and request-scope policy enforcement.
2. Access control layer validates token format, role validity, policy existence, and scope correctness before route execution.
3. Access control layer enriches API responses with deterministic `access_context` metadata without mutating query payload semantics.

Hard rules:

1. Access control layer MUST NOT access the database directly.
2. Access control layer MUST NOT execute SQL, write operations, or schema mutation.
3. Access control layer MUST NOT bypass query-service mediation and MUST NOT reinterpret state.

Separation reinforcement:

1. Enforcement validates truth production boundaries.
2. Ingestion persists validated truth.
3. Query layer reads canonical persisted truth.
4. API layer exposes query outputs.
5. Access control layer guards API access and visibility without changing underlying data meaning.

## Sprint 7 Rate Limiting and Query Governance Boundary

Governance layer architectural role:

1. Governance layer is a pure pre-query execution filter at the API boundary.
2. Governance layer enforces per-user, per-role, and per-endpoint rate limits and quotas.
3. Governance layer enforces request-shape bounds for pagination and evidence window scope.

Hard rules:

1. Governance layer MUST execute before API routes and MUST block non-compliant requests fail closed.
2. Governance layer MUST NOT access the database directly and MUST NOT execute SQL.
3. Governance layer MUST NOT mutate payload semantics or recompute canonical state.
4. Governance layer MUST NOT bypass access-control or query-service boundaries.

Separation reinforcement:

1. Enforcement defines truth and CI/runtime boundary guarantees.
2. Ingestion persists validated truth into canonical storage.
3. Query layer performs read-only retrieval and contract validation.
4. API and auth layers provide controlled access boundaries.
5. Governance layer protects query capacity and fairness without changing data meaning.

## Sprint 8 Observability and Audit Boundary

Observability layer architectural role:

1. Observability layer is a non-invasive truth layer that captures system behavior and decision telemetry without influencing runtime outcomes.
2. Observability layer records structured immutable audit events for API requests, auth/authz outcomes, governance outcomes, query execution boundaries, ingestion events, and CI gate runs.
3. Observability layer provides deterministic end-to-end trace reconstruction using request correlation and trace hashing.

Hard rules:

1. Observability layer MUST NOT modify enforcement, persistence, query, API, auth, or governance logic.
2. Observability layer MUST NOT mutate API responses or decision outcomes.
3. Observability layer MUST remain append-only for audit storage and MUST NOT perform update/delete mutation of captured events.
4. If observability components fail, system execution MUST continue and only observability capture is degraded.

Separation reinforcement:

1. Observability is strictly downstream-only and consumes emitted runtime decisions passively.
2. Functional system behavior is identical pre/post observability integration.
3. Trace reconstruction capability is additive telemetry, not control-plane logic.

## Sprint 9 Production Externalization and Export Boundary

Export layer architectural role:

1. Export layer is a derived-only, read-only externalization surface over observability audit records.
2. Export layer provides deterministic trace replay, metrics derivation, SIEM formatting, and OpenTelemetry mapping for downstream platforms.
3. Export layer treats observability as the stable system-of-record and does not introduce new runtime decision paths.

Hard rules:

1. Export layer MUST NOT modify enforcement, persistence, query, API, auth, governance, or observability behavior.
2. Export layer MUST NOT alter API responses, query outputs, persistence state, or governance decisions.
3. Export layer MUST execute as offline/read-only transformation and MUST remain failure-isolated from core execution paths.
4. Sprint 9 extends external visibility only and MUST NOT extend functional system behavior.

Separation reinforcement:

1. Core system behavior remains frozen and immutable across all runtime control layers.
2. External integrations consume exported derived data, not live control-plane hooks.
3. Deterministic export contracts preserve trace replay and interoperability without internal mutation.

## Sprint 10 System Hardening and Drift Immunity Boundary

Hardening layer architectural role:

1. Hardening layer is a read-only analytical assurance layer that validates frozen-surface integrity across enforcement, persistence, query, API, auth, governance, and observability layers.
2. Hardening layer performs deterministic baseline hash comparison, schema consistency checks, dependency-boundary auditing, and immutability verification.
3. Hardening layer is offline/inspection-only and does not participate in request handling, query execution, or runtime decision making.

Hard rules:

1. Hardening layer MUST NOT introduce runtime hooks into enforcement, persistence, query, API, auth, governance, observability, or export execution paths.
2. Hardening layer MUST NOT mutate canonical data, API payloads, audit records, or CI/runtime policy outcomes.
3. Hardening layer MUST operate passively against artifacts, source files, and recorded baselines and MUST fail closed only at governance/compliance reporting boundaries.
4. Sprint 10 hardening scope MUST remain additive-only and MUST preserve frozen-layer behavior exactly.

Separation reinforcement:

1. Core runtime layers continue to produce behavior; hardening only observes and verifies drift-free integrity.
2. Drift detection and immutability reports are assurance outputs, not execution controls.
3. Baseline artifacts provide deterministic proof of no-change guarantees for frozen layers during sprint promotion.

## Sprint 11 Unified Compliance Orchestration Boundary

Unified orchestration architectural role:

1. Unified compliance orchestration layer executes a deterministic, single-entry gate sequence over SCI authority, EMV, CI kernel, and subsystem validation suites.
2. Compliance aggregation layer normalizes all gate outcomes into one fail-closed contract for CI and CLI consumption.
3. Single gate runner entrypoint is orchestration-only and remains external to runtime application request paths.

Hard rules:

1. Unified runner MUST NOT modify enforcement, persistence, query, API, auth, governance, observability, export, or hardening logic.
2. Unified runner MUST execute gates in fixed order with no parallel bypass path and no alternate sequencing branch.
3. Unified runner MUST fail closed on first gate violation and MUST emit one consolidated `GATE_FAILED` contract with rule references.
4. Unified runner MUST remain read-only and MUST NOT introduce runtime coupling to subsystem execution flows.

Separation reinforcement:

1. Subsystem execution remains independently verifiable by existing test suites and gate scripts.
2. Compliance aggregation is a reporting layer only and does not change subsystem outcomes.
3. Single gate entrypoint centralizes compliance execution semantics while preserving frozen subsystem behavior.

## Sprint 12 Cross-Sprint Integrity Stabilization Boundary

Cross-sprint stabilization architectural role:

1. Cross-sprint stabilization layer performs read-only verification across Sprint 10, Sprint 11, and Sprint 12 baseline artifacts.
2. Determinism certification engine verifies repeated unified compliance executions produce stable ordering and identical deterministic hashes.
3. Integrity certificate generator emits formal certification outputs (`drift_score`, `determinism_score`, `compliance_score`, and freeze verification status) for governance promotion checks.

Hard rules:

1. Stabilization layer MUST NOT modify enforcement, persistence, query, API, auth, governance, observability, export, hardening, or orchestration layers.
2. Stabilization layer MUST remain CI-only or CLI-only and MUST NOT register runtime hooks in request execution paths.
3. Stabilization layer MUST consume artifacts read-only and MUST fail closed when drift, schema divergence, dependency violations, or determinism mismatches are detected.
4. Stabilization layer MUST NOT alter unified runner behavior and MUST treat unified output as immutable input for certification.

Separation reinforcement:

1. Runtime subsystem behavior remains frozen and independently testable.
2. Multi-sprint verification is an analytical overlay and not a control-path mutation layer.
3. Integrity certification is an assurance boundary that reports system state without changing execution semantics.

## Transition to Nautobot Authority

Nautobot may become authoritative only when all are true:

1. SCI explicitly approves cutover.
2. Data model parity is verified.
3. Reconciliation controls are active.
4. Global automation guard and rollback procedures are tested.

Until SCI-approved cutover is completed, PostgreSQL remains canonical for validated runtime state.
