# EXECUTION_PRECEDENCE_CONTRACT

## SCI Binding and Scope

This document is subordinate only to SYSTEM_CONTRACT_INDEX.md and is binding for runtime execution precedence.
If any statement in this document conflicts with SCI, SCI is authoritative and this document is descriptive for the conflicting scope.

This contract is mandatory for Sprint 2 and all later sprints that execute validation runtime paths.

## Non-Reorderability Declaration

Execution precedence defined in this contract is deterministic and non-reorderable.
Any ordering deviation is a system integrity failure and MUST fail closed.

Pre-runtime precedence rule:

CI SCI enforcement is the highest pre-runtime gate.
Runtime execution is prohibited unless CI enforcement reports zero violations.

## 0. Pre-Check Status (Fail-Closed Baseline)

Pre-check verification status:

1. SCI exists and is readable: PASS (`SYSTEM_CONTRACT_INDEX.md`)
2. Bootstrap module exists: PASS (`bootstrap.py`)
3. Runtime guard module exists: PASS (`execution_contract/runtime_guard.py`)
4. Schema gate module exists: PASS (`execution_contract/schema.py`)
5. Mapper module exists: PASS (`execution_contract/ansible_error_mapper.py`)
6. Runner module exists: PASS (`ansible_validation/runners/run_validation.py`)
7. Sprint 2 runtime model evidence exists: PASS (bootstrap-first, orchestration-only runner, schema version `2.1` gate)

If any status changes to FAIL, this contract requires execution blocking until restored.

## 1. Current State Verification

### 1.1 Active Enforcement Layers

Active enforcement layers currently present:

1. Bootstrap boundary (`bootstrap.py`)
2. Pre-import runtime guard (`execution_contract/runtime_guard.py`)
3. Deterministic mapper (`execution_contract/ansible_error_mapper.py`)
4. Runtime schema validation gate (`execution_contract/schema.py`)
5. Orchestration-only runner (`ansible_validation/runners/run_validation.py`)

### 1.2 Runtime Initiation Point

Execution begins at `bootstrap.py`.
`run_validation.py` direct execution is explicitly blocked and returns `FAIL_RUNTIME` with `BOOTSTRAP_REQUIRED`.

### 1.3 Runtime Validation Location

Runtime validation occurs in `execution_contract/runtime_guard.py` via:

1. `enforce_preimport_boundary`
2. `enforce_python_runtime_311`
3. `enforce_ansible_runtime_311`
4. `enforce_preimport_runtime_311`

### 1.4 Schema Validation Location

Schema validation occurs in `execution_contract/schema.py` via:

1. `validate_result`
2. `validate_results`
3. `write_validation_output`

### 1.5 Output Emission Location

Output emission occurs only in `execution_contract/schema.py` function `write_validation_output` after runtime schema validation passes.

### 1.6 Ordering Gaps and Drift Risks

Known drift risks that this contract addresses:

1. External code could import runner internals without bootstrap unless policy blocks it.
2. Enforcement ownership can drift if mapper, schema, and runner responsibilities are mixed.
3. Future module reorder by automation tools can bypass intended guard sequencing unless precedence is explicitly enforced.

## 2. Target Production Execution Model

Production target model requirements:

1. Single authoritative runtime ordering contract: this document
2. Strict pre-import enforcement requirement: runtime guard executes before any application module loading beyond bootstrap
3. Fail-closed execution behavior at every boundary
4. Deterministic module ordering with no alternate branch order
5. Zero overlapping authority among bootstrap, runtime guard, runner, mapper, and schema

## 3. Gap Analysis

### 3.1 Missing Contract Definition Risk

Risk: Execution ordering without explicit contract allows reinterpretation.
Control: This file defines the only valid runtime ordering.

### 3.2 Overlapping Responsibility Risk

Risk: Runtime checks, mapping, and schema checks can be duplicated across modules.
Control: Authority precedence rules in this contract prohibit overlap.

### 3.3 Boundary Ambiguity Risk

Ambiguity controls:

1. Bootstrap vs runtime_guard: bootstrap invokes runtime_guard; runtime_guard cannot start execution flow.
2. Schema vs mapper: mapper classifies normalized failures only; schema validates and gates output only.
3. Runner vs execution control: runner orchestrates only; runner has no runtime enforcement authority.

### 3.4 Copilot Reordering Risk

Risk: Automated refactors can reorder imports or module calls.
Control: Any reorder outside Section 4.1 order is SCI violation and MUST block merge or runtime.

## 4. Implementation Contract

### 4.1 Execution Order (Non-Negotiable)

Valid execution order is exactly:

1. CI SCI enforcement gate (pre-runtime)
2. Bootstrap start
3. Runtime Guard execution (pre-import enforcement)
4. Import Boundary Lock
5. Runner orchestration start
6. Mapper deterministic transformation
7. Schema validation gate (versioned contract enforcement)
8. Output emission

This order is deterministic, non-reorderable, and fail-closed if violated.

### 4.2 Authority Precedence Rules

1. CI SCI enforcement is highest authority before runtime initiation.
2. Bootstrap is highest authority at runtime initiation.
3. Runtime Guard cannot override Bootstrap failure state.
4. Schema cannot accept invalid or unversioned output.
5. Mapper cannot influence execution state machine flow control.
6. Runner is orchestration-only and has zero enforcement authority.

### 4.3 Forbidden Execution Patterns

The following are prohibited:

1. Importing bootstrap after runtime initialization.
2. Runner performing runtime validation logic.
3. Runner performing schema definition or schema gate ownership.
4. Schema performing execution logic or mapper logic.
5. Mapper performing branching control of orchestration flow.
6. Runtime guard executing after application imports begin.
7. Any cross-layer duplication of enforcement responsibility.

### 4.4 Fail-Closed Behavior Chain

Mandatory failure propagation:

1. CI gate failure -> merge blocked, sprint entry blocked, runtime execution blocked.
2. Bootstrap failure -> immediate process termination and no runner/module imports.
3. Runtime Guard failure -> runner execution blocked.
4. Mapper failure -> schema validation and output emission blocked.
5. Schema failure -> output write blocked.
6. Runner failure -> execution halts and no persistence/output promotion.

### 4.5 Contract Enforcement Rules

1. This contract is SCI-binding and non-optional.
2. Any violation is system integrity failure.
3. All `execution_contract` modules MUST reference this document as runtime authority source.
4. CI and runtime gates MUST treat this file as authoritative precedence definition.

## 5. SCI Compliance Gate Checklist

All checks MUST be true before execution is allowed:

1. Execution order is unambiguous and exactly matches Section 4.1.
2. No overlapping authority exists across bootstrap, runtime guard, runner, mapper, schema.
3. Fail-closed behavior is defined at every boundary.
4. No implied execution flexibility exists.
5. Bootstrap-first model is intact and enforced.

If any check fails, execution is BLOCKED.
