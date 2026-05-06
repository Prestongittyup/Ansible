# Deterministic Fail-Closed Network Enforcement System

This repository implements a deterministic, fail-closed execution system for network discovery, validation, parity/drift enforcement, and audited runtime operations.

## What This System Is

The system enforces a strict lifecycle:

1. Bootstrap runtime readiness and integration safety.
2. Run deterministic gate and adapter execution.
3. Persist canonical validated state.
4. Emit immutable audit artifacts.

It is designed to block on ambiguity, contract violations, or dependency failures instead of attempting silent recovery.

### Problem It Solves

- Prevents unsafe network automation execution when prerequisites are invalid.
- Enforces identity (`ip_address`), SST ownership, and deterministic parity checks.
- Produces auditable run evidence for governance and operations.

### High-Level Architecture

Pipeline A (active): `LogicMonitor -> Normalization -> Ansible validation -> PostgreSQL canonical state`

Pipeline B (future authority path): `Nautobot -> Ansible execution -> Devices` (authority only after approved cutover)

Normative authority is in [SYSTEM_CONTRACT_INDEX.md](SYSTEM_CONTRACT_INDEX.md).

## Repository Structure (Sprint 21)

Canonical production-maintained layout:

- `system_kernel.py` (single kernel lifecycle entrypoint)
- `core/`
- `adapters/`
- `operations/`
- `observability/`
- `config/`
- `tests/`
- `docs/`
- `deploy/`
- `logs/`
- `artifacts/`

Legacy or superseded sprint-era modules are quarantined under `legacy/` to preserve auditability and backward investigation without polluting active runtime paths.

## How To Run It

### 1. Configure Environment

Create an environment file (default `.env`) and set at minimum:

- `KERNEL_MODE=MOCK|LIVE|HYBRID`
- `PHASE=PHASE_1|PHASE_2|PHASE_3`
- `POSTGRES_CONNECTION_STRING` (for LIVE/HYBRID)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (for LIVE/HYBRID)
- `LOGICMONITOR_API_KEY`, `LOGICMONITOR_ACCOUNT` (for LIVE/HYBRID LogicMonitor)
- `ANSIBLE_EXECUTION_ENDPOINT` or `ANSIBLE_RUNNER_PATH` (for LIVE/HYBRID Ansible)
- `NAUTOBOT_TOKEN`, `NAUTOBOT_URL` (for LIVE/HYBRID Nautobot)

### 2. Bootstrap

Run canonical bootstrap via kernel CLI:

```bash
python system_kernel.py bootstrap \
  --mode MOCK \
  --phase PHASE_1 \
  --env-path .env \
  --validate-env true \
  --init-adapters true \
  --health-check true \
  --emit-ready-state logs/bootstrap \
  --fail-closed true
```

Expected: `execution_status=READY` in `runtime_ready_state.json`.

### 3. Run

Run deterministic execution only after bootstrap is READY:

```bash
python system_kernel.py run \
  --mode MOCK \
  --phase PHASE_1 \
  --source mock \
  --target mock \
  --parity-window 30d \
  --fail-closed true \
  --emit-audit logs/runs/manual
```

Expected: an audit file at `logs/runs/manual/system_enforcement_audit.json`.

### 4. Check Health

For long-running operations service:

```bash
python operations_cli.py system start --mode MOCK --phase PHASE_1
python operations_cli.py system health
python operations_cli.py system status
```

Service health endpoint:

```text
GET http://127.0.0.1:8088/health
```

### MOCK vs LIVE

- `MOCK`: uses deterministic fixture-backed adapter behavior and is safe for local contract validation.
- `LIVE`: requires real credentials/dependencies and blocks on any integration/health failure.
- `HYBRID`: prefers LIVE behavior and allows fallback only when explicitly enabled by adapter fallback flags.

## Execution Model

### Lifecycle

`bootstrap -> run` is mandatory.

- `run` is blocked unless canonical ready state exists and validates mode/phase compatibility.
- Operations service re-checks bootstrap validity before every run.

### Fail-Closed Behavior

- Any contract violation, missing prerequisite, or failed gate returns a hard-fail decision.
- Bootstrap writes a blocked state artifact when failing, including reason and integration details.
- Run writes audit output even for failure paths.

### Audit Output

Run audits include:

- run metadata and deterministic run id
- gate results
- parity/drift metrics
- identity conflict data
- adapter operation snapshots
- final decision and exit code

## Safety Model

### SST Enforcement

- `PHASE_1`: SST `LOGICMONITOR`
- `PHASE_2`: SST `ANSIBLE_POSTGRES`
- `PHASE_3`: SST `NAUTOBOT`

Authority mismatches fail the AUTH gate.

### Identity Rules

- `ip_address` is the only identity key.
- Missing or duplicate `ip_address` is treated as a hard violation.

### Mutation Controls

- Default execution mode is `READ_ONLY`.
- Mutation paths require governance approval and phase constraints.
- `DEGRADED` lifecycle state blocks mutation operations.

## Canonical Runtime Authorities

- Canonical runtime CLI: [system_kernel.py](system_kernel.py)
- Canonical operations wrapper: [operations_cli.py](operations_cli.py)
- Canonical SCI validator (CI and runtime contexts): [ci/sci_validator.py](ci/sci_validator.py)

Legacy alternate entrypoints and duplicate SCI/workflow authorities were removed in Sprint 18.6.

## Documentation Index

- [SYSTEM_CONTRACT_INDEX.md](SYSTEM_CONTRACT_INDEX.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/data-authority-policy.md](docs/data-authority-policy.md)
- [docs/operational-guardrails.md](docs/operational-guardrails.md)
- [docs/deployment_operations_architecture.md](docs/deployment_operations_architecture.md)
- [docs/system_technical_spec.md](docs/system_technical_spec.md)
- [docs/production_readiness_checklist.md](docs/production_readiness_checklist.md)
