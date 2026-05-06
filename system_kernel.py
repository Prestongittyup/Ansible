from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from core.bootstrap_runtime import (
    BOOT_STATE_SCHEMA_VERSION,
    CANONICAL_READY_STATE_PATH,
    BootstrapCommandConfig,
    BootstrapRuntimeEngine,
    validate_ready_state,
)
from core.execution_command_model import (
    FULL_GATE_ORDER,
    ExecutionCommandConfig,
    ExecutionCommandEngine,
    ExecutionCommandError,
    parse_parity_window_days,
)
from core.runtime_engine import KERNEL_ANCHOR
from kernel.phase_resolver import phase_info_for
from kernel.audit_writer import write_audit
from kernel.state import EXIT_HARD_FAIL, RunState
from observability import context as obs_context
from observability.logger import classify_error, initialize_observability, log_event, log_span


class StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ExecutionCommandError(f"INVALID_CLI:{message}")


def _parse_bool(value: str) -> bool:
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    raise ExecutionCommandError(f"Invalid boolean value: {value}")


def _build_parser() -> argparse.ArgumentParser:
    parser = StrictArgumentParser(
        prog="system_kernel",
        description="Canonical lifecycle contract: system_kernel bootstrap -> run",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=StrictArgumentParser)

    bootstrap = subparsers.add_parser("bootstrap", help="Initialize deterministic runtime readiness")
    bootstrap.add_argument(
        "--mode",
        choices=["MOCK", "LIVE", "HYBRID"],
        required=True,
    )
    bootstrap.add_argument(
        "--phase",
        choices=["PHASE_1", "PHASE_2", "PHASE_3"],
        required=True,
    )
    bootstrap.add_argument("--env-path", required=True)
    bootstrap.add_argument("--validate-env", required=True)
    bootstrap.add_argument("--init-adapters", required=True)
    bootstrap.add_argument("--health-check", required=True)
    bootstrap.add_argument("--emit-ready-state", required=True)
    bootstrap.add_argument("--fail-closed", required=True)
    bootstrap.add_argument("--debug", default="false")

    run = subparsers.add_parser("run", help="Run deterministic system kernel")
    run.add_argument(
        "--mode",
        choices=["MOCK", "LIVE", "HYBRID"],
        required=True,
    )
    run.add_argument(
        "--phase",
        choices=["PHASE_1", "PHASE_2", "PHASE_3"],
        required=True,
    )
    run.add_argument("--source", choices=["logicmonitor", "mock"], required=True)
    run.add_argument("--target", choices=["nautobot", "mock"], required=True)
    run.add_argument("--parity-window", required=True)
    run.add_argument("--fail-closed", required=True)
    run.add_argument("--emit-audit", required=True)
    run.add_argument("--debug", default="false")

    return parser


def _extract_flag_value(argv: list[str], flag: str) -> str:
    for idx, token in enumerate(argv):
        if token == flag and idx + 1 < len(argv):
            return str(argv[idx + 1]).strip()
        if token.startswith(flag + "="):
            return token.split("=", 1)[1].strip()
    return ""


def _resolve_fallback_audit_path(argv: list[str]) -> str:
    candidate = _extract_flag_value(argv, "--emit-audit")
    return candidate or "logs"


def _resolve_fallback_ready_state_path(argv: list[str]) -> str:
    candidate = _extract_flag_value(argv, "--emit-ready-state")
    if candidate:
        return candidate
    return str(CANONICAL_READY_STATE_PATH)


def _normalize_ready_state_output_path(path_value: str) -> Path:
    path = Path(str(path_value).strip())
    if path.suffix.lower() != ".json":
        path = path / "runtime_ready_state.json"
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _write_boot_state_files(*, boot_state: dict[str, object], output_path: str) -> tuple[Path, Path]:
    primary = _normalize_ready_state_output_path(output_path)
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(json.dumps(boot_state, indent=2, sort_keys=True), encoding="utf-8")

    canonical = (Path.cwd() / CANONICAL_READY_STATE_PATH).resolve()
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(json.dumps(boot_state, indent=2, sort_keys=True), encoding="utf-8")

    return primary, canonical


def _build_runtime_failure_state(*, argv: list[str], error: Exception) -> RunState:
    fallback = RunState.bootstrap_failure(
        kernel_anchor=KERNEL_ANCHOR,
        reason=f"CLI_BOOTSTRAP_ERROR:{error}",
        fail_closed=True,
    )

    mode = (_extract_flag_value(argv, "--mode") or "UNKNOWN").upper()
    phase = (_extract_flag_value(argv, "--phase") or "UNKNOWN").upper()
    source = (_extract_flag_value(argv, "--source") or "UNKNOWN").lower()
    target = (_extract_flag_value(argv, "--target") or "UNKNOWN").lower()
    parity_window = _extract_flag_value(argv, "--parity-window") or "UNKNOWN"

    if phase in {"PHASE_1", "PHASE_2", "PHASE_3"}:
        phase_info = phase_info_for(phase)
        fallback.set_phase_context(
            source_phase=phase_info.phase,
            target_phase=phase_info.phase,
            sst_source=phase_info.sst_source,
            sst_target=phase_info.sst_source,
        )

    fallback.gate_sequence_expected = list(FULL_GATE_ORDER)
    fallback.data_stats["runtime_gate_sequence"] = list(FULL_GATE_ORDER)
    fallback.data_stats["contract_gate_sequence"] = list(FULL_GATE_ORDER)
    fallback.data_stats["kernel_mode"] = mode
    fallback.data_stats["execution_mode"] = "READ_ONLY"
    fallback.data_stats["execution_status"] = "BLOCKED"
    fallback.data_stats["fail_closed_triggered"] = True
    fallback.data_stats["drift_percentage"] = 0.0
    fallback.data_stats["identity_conflicts"] = []
    fallback.data_stats["parity_result"] = {
        "status": "NOT_EXECUTED",
        "reason": "CLI_BOOTSTRAP_ERROR",
    }
    fallback.data_stats["adapters_active"] = []
    fallback.data_stats["command_model"] = {
        "source": source,
        "target": target,
        "parity_window": parity_window,
        "emit_audit": _resolve_fallback_audit_path(argv),
        "phase": phase,
    }

    adapter_failure = {
        "status": "FAIL",
        "mode_used": mode,
        "details": {"error": str(error)},
    }
    fallback.data_stats["adapter_states"] = {
        "LogicMonitorAdapter": {"bootstrap": dict(adapter_failure)},
        "AnsibleAdapter": {"bootstrap": dict(adapter_failure)},
        "NautobotAdapter": {"bootstrap": dict(adapter_failure)},
        "PostgresAdapter": {"bootstrap": dict(adapter_failure)},
    }

    signature_payload = {
        "kernel_mode": mode,
        "phase": phase,
        "source": source,
        "target": target,
        "error": str(error),
    }
    rendered = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    fallback.data_stats["deterministic_signature"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    fallback.data_stats["trace_id"] = obs_context.get_trace_id()

    return fallback


def _build_bootstrap_blocked_state(*, argv: list[str], error: Exception) -> dict[str, object]:
    mode = (_extract_flag_value(argv, "--mode") or "UNKNOWN").upper()
    phase = (_extract_flag_value(argv, "--phase") or "UNKNOWN").upper()

    state: dict[str, object] = {
        "boot_id": "",
        "trace_id": obs_context.get_trace_id(),
        "mode": mode,
        "phase": phase,
        "environment_valid": False,
        "adapters_initialized": [],
        "adapters_failed": [{"adapter": "SYSTEM", "reason": str(error)}],
        "integration_status": {},
        "external_dependencies_ready": False,
        "live_execution_safe": False,
        "system_ready": False,
        "health_status": {},
        "fail_closed_triggered": True,
        "execution_status": "BLOCKED",
        "schema_version": BOOT_STATE_SCHEMA_VERSION,
        "reason": str(error),
        "runtime_lock_hash": "",
    }

    signature_payload = {
        "mode": mode,
        "phase": phase,
        "reason": str(error),
    }
    rendered = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    state["boot_id"] = "system-boot-" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]
    return state


def _emit_summary(*, run_id: str, decision: str, exit_code: int, audit_path: Path) -> None:
    print(
        json.dumps(
            {
                "run_id": run_id,
                "decision": decision,
                "exit_code": exit_code,
                "audit_path": str(audit_path),
            },
            sort_keys=True,
        )
    )


def _emit_bootstrap_summary(*, boot_state: dict[str, object], exit_code: int, ready_state_path: Path) -> None:
    print(
        json.dumps(
            {
                "boot_id": str(boot_state.get("boot_id", "")),
                "execution_status": str(boot_state.get("execution_status", "BLOCKED")),
                "system_ready": bool(boot_state.get("system_ready", False)),
                "exit_code": int(exit_code),
                "ready_state_path": str(ready_state_path),
            },
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command_hint = raw_argv[0].strip().lower() if raw_argv else ""

    try:
        parser = _build_parser()
        args = parser.parse_args(raw_argv)
        debug_enabled = _parse_bool(getattr(args, "debug", "false"))
        if args.command == "bootstrap":
            bootstrap_run_id = f"bootstrap:{args.mode}:{args.phase}"
            trace_id = initialize_observability(
                run_id=bootstrap_run_id,
                phase=args.phase,
                mode=args.mode,
                debug=debug_enabled,
            )

            log_event(
                level="INFO",
                component="execution_model",
                operation="cli.bootstrap_input",
                status="START",
                command="bootstrap",
                mode=args.mode,
                phase=args.phase,
                debug=debug_enabled,
            )

            bootstrap_engine = BootstrapRuntimeEngine(root_path=Path.cwd())
            with log_span(
                component="execution_model",
                operation="system_kernel.bootstrap",
                mode=args.mode,
                phase=args.phase,
            ):
                result = bootstrap_engine.bootstrap(
                    BootstrapCommandConfig(
                        mode=args.mode,
                        phase=args.phase,
                        env_path=args.env_path,
                        validate_env=_parse_bool(args.validate_env),
                        init_adapters=_parse_bool(args.init_adapters),
                        health_check=_parse_bool(args.health_check),
                        emit_ready_state=args.emit_ready_state,
                        fail_closed=_parse_bool(args.fail_closed),
                        root_path=str(Path.cwd()),
                        python_executable=sys.executable,
                        debug=debug_enabled,
                    )
                )

            resolved_trace_id = str(result.boot_state.get("trace_id") or trace_id)
            obs_context.update_run_context(
                run_id=str(result.boot_state.get("boot_id", bootstrap_run_id)),
                trace_id=resolved_trace_id,
                mode=args.mode,
                phase=args.phase,
            )
            log_event(
                level="INFO",
                component="execution_model",
                operation="bootstrap.ready_state_emit",
                status="SUCCESS" if result.exit_code == 0 else "FAIL",
                ready_state_path=str(result.boot_state_path),
                canonical_ready_state_path=str(result.canonical_state_path),
                execution_status=str(result.boot_state.get("execution_status", "")),
            )
            _emit_bootstrap_summary(
                boot_state=result.boot_state,
                exit_code=result.exit_code,
                ready_state_path=result.boot_state_path,
            )
            return result.exit_code

        if args.command != "run":
            raise ExecutionCommandError(f"Unsupported command: {args.command}")

        fail_closed = _parse_bool(args.fail_closed)
        if not fail_closed:
            raise ExecutionCommandError("FAIL_CLOSED_MUST_BE_TRUE")

        parity_window_days = parse_parity_window_days(args.parity_window)
        ready_state = validate_ready_state(
            path=(Path.cwd() / CANONICAL_READY_STATE_PATH).resolve(),
            mode=args.mode,
            phase=args.phase,
        )

        initialize_observability(
            run_id=str(ready_state.get("boot_id") or f"run:{args.mode}:{args.phase}"),
            phase=args.phase,
            mode=args.mode,
            trace_id=str(ready_state.get("trace_id") or ""),
            debug=debug_enabled,
        )
        log_event(
            level="INFO",
            component="execution_model",
            operation="cli.run_input",
            status="START",
            command="run",
            mode=args.mode,
            phase=args.phase,
            source=args.source,
            target=args.target,
            parity_window=args.parity_window,
            debug=debug_enabled,
        )

        engine = ExecutionCommandEngine(root_path=Path.cwd(), kernel_anchor=KERNEL_ANCHOR)
        with log_span(
            component="execution_model",
            operation="system_kernel.run",
            mode=args.mode,
            phase=args.phase,
            source=args.source,
            target=args.target,
        ):
            result = engine.execute(
                ExecutionCommandConfig(
                    mode=args.mode,
                    phase=args.phase,
                    source=args.source,
                    target=args.target,
                    parity_window_days=parity_window_days,
                    fail_closed=fail_closed,
                    emit_audit=args.emit_audit,
                    root_path=str(Path.cwd()),
                    python_executable=sys.executable,
                    debug=debug_enabled,
                )
            )

        obs_context.update_run_context(run_id=result.state.run_id)
        log_event(
            level="INFO",
            component="execution_model",
            operation="run.audit_emit",
            status="SUCCESS" if result.state.exit_code == 0 else "FAIL",
            run_id=result.state.run_id,
            audit_path=str(result.audit_path),
            decision=result.state.decision,
            exit_code=result.state.exit_code,
        )

        _emit_summary(
            run_id=result.state.run_id,
            decision=result.state.decision,
            exit_code=result.state.exit_code,
            audit_path=result.audit_path,
        )
        return result.state.exit_code

    except Exception as exc:
        mode_hint = (_extract_flag_value(raw_argv, "--mode") or "UNKNOWN").upper()
        phase_hint = (_extract_flag_value(raw_argv, "--phase") or "UNKNOWN").upper()
        debug_token = _extract_flag_value(raw_argv, "--debug") or "false"
        try:
            debug_enabled = _parse_bool(debug_token)
        except Exception:
            debug_enabled = False

        initialize_observability(
            run_id=f"error:{command_hint or 'unknown'}:{mode_hint}:{phase_hint}",
            phase=phase_hint,
            mode=mode_hint,
            debug=debug_enabled,
        )
        classified = classify_error(exc)
        log_event(
            level="ERROR",
            component="execution_model",
            operation=f"system_kernel.{command_hint or 'unknown'}",
            status="FAIL",
            **classified,
        )

        if command_hint == "bootstrap":
            fallback_ready_state_path = _resolve_fallback_ready_state_path(raw_argv)
            blocked_state = _build_bootstrap_blocked_state(argv=raw_argv, error=exc)
            try:
                output_path, _ = _write_boot_state_files(
                    boot_state=blocked_state,
                    output_path=fallback_ready_state_path,
                )
                _emit_bootstrap_summary(
                    boot_state=blocked_state,
                    exit_code=EXIT_HARD_FAIL,
                    ready_state_path=output_path,
                )
            except Exception:
                pass
            return EXIT_HARD_FAIL

        fallback_audit_path = _resolve_fallback_audit_path(raw_argv)
        fallback = _build_runtime_failure_state(argv=raw_argv, error=exc)

        try:
            audit_output = write_audit(fallback, Path(fallback_audit_path))
            _emit_summary(
                run_id=fallback.run_id,
                decision=fallback.decision,
                exit_code=fallback.exit_code,
                audit_path=audit_output,
            )
        except Exception:
            pass

        return EXIT_HARD_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
