# Deployment and Operations Architecture (Sprint 18)

## Scope

This document defines how the deterministic fail-closed control plane runs continuously in production without modifying kernel, adapter, or parity logic.

## Operational Truth

Exactly one operational truth exists at all times:

- `RUNNING`: lifecycle state is `RUNNING` and bootstrap readiness is valid.
- `NOT_RUNNING`: all other cases, including `READY`, `DEGRADED`, `BLOCKED`, and `STOPPED`.

## Deployment Model

Preferred deployment model is containerized service using [deploy/docker-compose.ops.yml](deploy/docker-compose.ops.yml).

### Startup Sequence

1. Container starts [deploy/container_entrypoint.py](deploy/container_entrypoint.py).
2. Entrypoint materializes runtime env (`/tmp/ops_runtime.env`) from env + injected secret files.
3. Entrypoint launches [operations/service_entrypoint.py](operations/service_entrypoint.py) `start`.
4. Service transitions:
   - `STOPPED -> STARTING -> BOOTSTRAPPING`
5. Service invokes `system_kernel bootstrap` (strict fail-closed).
6. If bootstrap is valid (`READY`), service transitions:
   - `BOOTSTRAPPING -> READY -> RUNNING`
7. HTTP health/status server starts on port `8088`.
8. Optional scheduler loop starts and checks bootstrap validity before each run.

### Shutdown Sequence

1. Operator sends stop signal (`operations_cli.py system stop`).
2. Service catches signal and stops scheduler loop.
3. HTTP server shuts down.
4. Service transitions to `STOPPED` and persists final state.

### Restart Recovery Behavior

- `operations_cli.py system restart` always performs `stop` then `start`.
- Every `start` forces bootstrap; no runtime bypass exists.
- No silent self-healing is performed.

### Health Probe Definition

Health endpoint: `GET /health`

- `READY`: state is ready for execution and bootstrap validity is intact.
- `DEGRADED`: partial failure state, execution paths blocked.
- `NOT_READY`: startup, blocked, stopped, or stale bootstrap.

## Required Entrypoints

### Bootstrap Execution Entrypoint

- `python operations_cli.py bootstrap force --override I_UNDERSTAND_FAIL_CLOSED ...`
- Internally calls `python -m operations.service_entrypoint bootstrap ...`.

### Run Execution Entrypoint

- `python operations_cli.py run execute ...`
- Internally calls `python -m operations.service_entrypoint run ...`.

### Health and Status Entrypoints

- `python operations_cli.py system health ...`
- `python operations_cli.py system status ...`
- HTTP endpoints:
  - `/health`
  - `/status`
  - `/status/bootstrap`
  - `/status/run`
  - `/status/adapters`
  - `/status/integration`

### Audit Retrieval Entrypoint

- `python operations_cli.py audit last ...`
- HTTP endpoint: `/audit/last`

### Entrypoint Rules

- Bootstrap must run before any run execution.
- Run is blocked if bootstrap state is stale/invalid.
- No implicit runtime auto-bootstrap exists.

## Operations Lifecycle Engine

Lifecycle states are implemented in [operations/lifecycle.py](operations/lifecycle.py):

- `STARTING`
- `BOOTSTRAPPING`
- `READY`
- `RUNNING`
- `DEGRADED`
- `BLOCKED`
- `STOPPED`

Deterministic transition rules are enforced by allowed transition map. Invalid transitions raise hard failure.

`DEGRADED` state explicitly blocks mutation operations and requires bootstrap re-validation before recovery.

## State Persistence Contract

Durable runtime state file: [logs/operations_state.json](logs/operations_state.json)

Persisted fields include:

- last bootstrap result (`last_bootstrap_result`)
- integration readiness status (`integration_readiness_status`)
- phase + SST state (`phase_state.phase`, `phase_state.sst_owner`)
- adapter health snapshot (`adapter_health_snapshot`)
- last successful run ID (`last_successful_run_id`)
- failure reason (`failure_reason`)

Properties:

- durable: written on every transition and major action
- replayable: complete JSON object including lifecycle + metrics
- deterministic: sorted-key JSON output with explicit schema version

Operator action audit log: [logs/operations_events.jsonl](logs/operations_events.jsonl)

## Health and Observability Layer

### Required Outputs

- system health: `/health`
- bootstrap status: `/status/bootstrap`
- run status: `/status/run`
- adapter health summary: `/status/adapters`
- integration readiness summary: `/status/integration`
- metrics stub: `/metrics`

### Determinism Rules

- Health responses are derived only from persisted state.
- No hidden in-memory-only decision paths are exposed as healthy.
- No implicit recovery decisions are made by endpoints.

## Failure Recovery Model

### Hard Rules

System does not self-heal silently.

Bootstrap re-validation is required after:

- dependency failure
- adapter failure
- DB failure
- SST mismatch

Restart always forces bootstrap.

### Recovery States

- clean restart: `STOPPED -> STARTING -> BOOTSTRAPPING` required
- partial failure: `DEGRADED` (execution blocked)
- persistent failure: `BLOCKED`

## Execution Scheduling Model

Allowed triggering models:

- manual CLI trigger (baseline)
- scheduled trigger (service loop with `--schedule --interval-seconds`)

Scheduling rules:

- scheduler validates bootstrap state before every run
- no bypass path exists around bootstrap validation
- each scheduled run emits audit artifact under `logs/operations/runs/<timestamp>/`

## Operator Interface Layer

Operational commands in [operations_cli.py](operations_cli.py):

- `system start`
- `system stop`
- `system restart`
- `system status`
- `system health`
- `bootstrap force` (explicit override required)
- `run execute`
- `audit last`

Safety rules:

- operator actions are auditable through events log
- unsafe action `bootstrap force` requires explicit override token
- overrides do not bypass fail-closed run/bootstrap constraints

## Deployment Artifacts

- deployment config: [deploy/docker-compose.ops.yml](deploy/docker-compose.ops.yml)
- container image spec: [deploy/Dockerfile.ops](deploy/Dockerfile.ops)
- environment wiring template: [deploy/.env.ops.example](deploy/.env.ops.example)
- secrets placeholders: [deploy/secrets/logicmonitor_api_key.txt](deploy/secrets/logicmonitor_api_key.txt), [deploy/secrets/nautobot_token.txt](deploy/secrets/nautobot_token.txt), [deploy/secrets/postgres_connection_string.txt](deploy/secrets/postgres_connection_string.txt)
- runtime service layer: [operations/service_runtime.py](operations/service_runtime.py), [operations/service_entrypoint.py](operations/service_entrypoint.py)
- operational CLI extension: [operations_cli.py](operations_cli.py)

## Fail-Closed Deployment Rules

Startup fails immediately if any of the following is true:

- bootstrap state cannot be produced/validated
- integration status is unhealthy
- adapter connectivity fails in LIVE mode
- Postgres transaction handshake fails
- SST state is ambiguous or mismatched
- readiness state cannot be computed deterministically

No partial startup is allowed.
