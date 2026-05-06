# System Technical Specification

## 1. Scope and Method

This document is an implementation-backed technical specification for the deterministic fail-closed runtime in this repository.

Validation method used for this spec:

1. Recursive repository scan excluding generated/environment artifacts (`.venv`, `__pycache__`, `logs`).
2. Static module dependency graph extraction for Python modules.
3. Entrypoint and execution-path verification against runtime code.
4. Contract evidence validation from code and tests.

Primary scan artifacts:

- [artifacts/sprint21/repo_inventory_phase1.json](artifacts/sprint21/repo_inventory_phase1.json)
- [artifacts/sprint21/target_architecture_mapping_phase2.json](artifacts/sprint21/target_architecture_mapping_phase2.json)
- [artifacts/sprint21/move_plan_phase3.json](artifacts/sprint21/move_plan_phase3.json)

## 2. Repository Classification Summary

Scanned source files: 539

Classification counts:

- runtime/core+kernel active path: 17
- adapter layer: 6
- operations layer: 8
- observability layer: 8
- configuration: 2
- tests: 22
- legacy quarantine modules: 16 directories
- unknown/unclassified: retained only for editor/workflow metadata

Detected architecture authority state (Sprint 21):

- Kernel authority is singular at `system_kernel.py` (`bootstrap`, `run`).
- SCI enforcement authority is singular at `ci/sci_validator.py` for both CI and runtime-context validation.
- Schema authorities are separated by domain:
  - execution contract output schema: `legacy/execution_contract/schema.py`
  - observability event schema: `observability/schema.py`

Legacy and superseded sprint-era modules are quarantined under `legacy/` and excluded from canonical production runtime authority.

## 3. Module to Layer Mapping

Complete per-file mapping is in [logs/sprint18_5/module_architecture_map.json](logs/sprint18_5/module_architecture_map.json) and includes:

`MODULE -> LAYER -> RESPONSIBILITY -> DEPENDENCIES`

Representative canonical modules:

| Module | Layer | Responsibility | Key Dependencies |
| --- | --- | --- | --- |
| `system_kernel.py` | runtime core | Canonical `bootstrap -> run` CLI lifecycle | `core/bootstrap_runtime.py`, `core/execution_command_model.py`, `kernel/audit_writer.py` |
| `core/bootstrap_runtime.py` | runtime core | Runtime readiness generation and validation | adapters, `core/integration_runtime.py`, `config/env_loader.py` |
| `core/execution_command_model.py` | runtime core | Deterministic gate + adapter + persistence execution command model | adapters, `kernel/gates.py`, `kernel/parity_engine.py` |
| `kernel/gates.py` | runtime core | SCI/EMV/CI/AUTH/GOVERNANCE/DRIFT/PARITY gate implementations | CI scripts, phase model |
| `adapters/*.py` | adapter layer | Mode-aware integrations for LogicMonitor/Ansible/Nautobot/Postgres | `adapters/base.py`, `runtime/mock/fixtures.py` |
| `core/integration_runtime.py` | runtime core | Bootstrap integration dependency probe and failure classification | `core/connection_manager.py`, `core/external_client_registry.py` |
| `operations/service_runtime.py` | operations layer | Long-running service lifecycle, health/status HTTP, scheduler | `system_kernel.py`, lifecycle/state/event modules |
| `operations_cli.py` | operations layer | Operator control interface over service/runtime commands | `operations/service_entrypoint.py`, `operations/event_log.py` |
| `legacy/persistence/*.py` | legacy (quarantine) | Superseded persistence helper path retained for historical compatibility | legacy schema + persistence contracts |
| `tests/*.py` | tests | Contract and regression verification | runtime, operations, adapters, legacy compatibility modules |

## 4. Execution Path Trace

## 4.1 Canonical Runtime Flow

Canonical runtime path:

1. CLI call enters `system_kernel.py`.
2. `bootstrap` command executes `BootstrapRuntimeEngine.bootstrap(...)`.
3. Ready-state is emitted and mirrored to canonical `logs/runtime_ready_state.json`.
4. `run` command validates canonical ready-state (`validate_ready_state`).
5. `ExecutionCommandEngine.execute(...)` runs deterministic gates and adapter operations.
6. Canonical state is written through Postgres adapter contract.
7. Audit is emitted via `kernel/audit_writer.py`.

## 4.2 Operations Service Flow

Operations service wrapper path:

1. Operator invokes `operations_cli.py`.
2. CLI calls `python -m operations.service_entrypoint ...`.
3. `OperationsRuntimeController.run_service_loop()` transitions lifecycle and runs bootstrap.
4. Scheduler/manual run path calls `system_kernel.py run` only after bootstrap validation.
5. Health/status/audit endpoints expose persisted operational state.

## 4.3 Bypass and Alternate Entrypoint Analysis

Observed:

- Active service/runtime path does not bypass bootstrap validation before run.
- `system_kernel.py run` hard-validates canonical ready-state and mode/phase matching.
- Legacy alternative runtime entrypoint has been removed.

Conclusion:

- Canonical operations flow has no alternate kernel CLI authority in-repo.
- Runtime and SCI authority are now singular and explicit.

## 5. Contract Validation

## 5.1 Fail-Closed Contract

Enforcement evidence:

- `system_kernel.py` requires `--fail-closed true` and returns hard-fail on violations.
- `core/bootstrap_runtime.py` marks blocked boot state on any bootstrap exception.
- `operations/service_runtime.py` blocks run when bootstrap state validation fails.

Status: enforced in code and covered by tests.

## 5.2 Deterministic Execution Contract

Enforcement evidence:

- Deterministic signatures are generated in bootstrap/run pipelines.
- Audit payload includes deterministic signature and deterministic parity hash.
- Deterministic bootstrap behavior tested in `test_bootstrap_runtime_layer.py`.

Status: enforced.

## 5.3 SST Authority Contract

Enforcement evidence:

- `kernel/phase_resolver.py` defines phase-to-SST model.
- `kernel/gates.py` AUTH gate validates phase/SST alignment.
- Adapter write constraints (for example Nautobot write) require proper SST/phase.

Status: enforced.

## 5.4 Identity Contract (IP-based)

Enforcement evidence:

- Adapter base record validation rejects missing/duplicate `ip_address`.
- Parity normalization raises hard conflicts on identity violations.
- Runtime identity guard enforces uniqueness before parity and persistence.

Status: enforced.

## 5.5 Adapter Activation Contract

Enforcement evidence:

- `AdapterActivationRegistry` in `core/execution_command_model.py` enforces allowed active adapters by phase/source/target.
- Unauthorized adapter operations raise command errors.
- Integration runtime probes only active adapters for bootstrap safety.

Status: enforced.

## 5.6 Lifecycle Enforcement Contract

Enforcement evidence:

- `operations/lifecycle.py` defines valid transitions only.
- Invalid transitions raise `LifecycleTransitionError`.
- Operations runtime persists lifecycle and requires bootstrap before run.

Status: enforced.

## 6. Data Flow

Required data flow in current implementation:

1. Ingestion: LogicMonitor adapter provides discovery records.
2. Normalization: parity/normalization path enforces canonical fields keyed by IP.
3. Execution: Ansible adapter performs validation execution.
4. Persistence: Postgres adapter writes canonical inventory.
5. Audit: kernel audit writer emits full run artifact.

Pipeline B (Nautobot authority) is represented in phase logic but governed by phase and SST constraints.

## 7. Adapter Model

Adapter lifecycle contract:

1. `validate_credentials()`
2. `init()`
3. `register()`
4. `health_check()`
5. operation calls
6. `shutdown()`

Mode behavior:

- MOCK: fixture-backed deterministic behavior.
- LIVE: real dependency use; no fallback.
- HYBRID: live-first with explicit fallback gates.

Adapter activation and operation authorization are centralized in command model registry logic.

## 8. Parity and Drift Model

Parity engine responsibilities:

- canonical record normalization
- identity conflict detection
- hard/soft drift classification
- global drift percentage calculation
- deterministic parity hash generation

Stability model:

- tracker records parity pass history and deterministic hash per run
- parity gate checks stability and critical event constraints for cutover-sensitive transitions

## 9. Phase and SST Model

Defined phases:

- `PHASE_1`: `LOGICMONITOR`
- `PHASE_2`: `ANSIBLE_POSTGRES`
- `PHASE_3`: `NAUTOBOT`

Resolution behavior:

- explicit phase selection or `AUTO` inference from record attributes
- target phase transitions constrained to forward-safe transitions
- AUTH/GOVERNANCE gates enforce authority and mutation restrictions

## 10. Failure Model

Failure principles:

- fail closed on ambiguity or violation
- no silent self-healing in operations service
- blocked/degraded lifecycle states require explicit recovery path

Failure classification:

- standardized classes in `operations/failure_classifier.py`
- integration probe statuses include `adapter`, `phase`, `operation`, `root_cause`, `failure_class`

Recovery model:

1. Resolve dependency/configuration cause.
2. Re-run bootstrap until READY.
3. Run execution and verify deterministic/audit outcomes.

## 11. Unknowns and Risks

Identified risks:

1. LIVE readiness in this workspace remains dependency-bound (not code-path bound).

## 12. Verification Evidence Used

Key tests executed during this pass:

- `test_bootstrap_runtime_layer.py`
- `test_operations_architecture_layer.py`
- `test_live_validation_hardening.py`

Result: all selected contract tests passed (`Ran 16 tests ... OK`).
