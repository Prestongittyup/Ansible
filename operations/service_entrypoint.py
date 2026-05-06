from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Optional

from kernel.state import EXIT_HARD_FAIL, EXIT_SUCCESS
from operations.service_runtime import OperationsConfig, OperationsRuntimeController, OperationsRuntimeError


class OperationsArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise OperationsRuntimeError(f"INVALID_OPERATIONS_CLI:{message}")


def _build_config(args: argparse.Namespace) -> OperationsConfig:
    return OperationsConfig(
        root_path=str(Path(args.root_path).resolve()),
        env_path=str(args.env_path),
        mode=str(args.mode).strip().upper(),
        phase=str(args.phase).strip().upper(),
        source=str(args.source).strip().lower(),
        target=str(args.target).strip().lower(),
        parity_window=str(args.parity_window).strip().lower(),
        fail_closed=True,
        scheduler_enabled=bool(args.schedule),
        scheduler_interval_seconds=max(1, int(args.interval_seconds)),
        health_host=str(args.health_host),
        health_port=int(args.health_port),
        state_path=str(args.state_path),
        event_log_path=str(args.event_log_path),
        pid_path=str(args.pid_path),
        bootstrap_emit_ready_state=str(args.bootstrap_output),
        run_audit_root=str(args.run_audit_root),
        python_executable=str(args.python_executable),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = OperationsArgumentParser(
        prog="operations.service_entrypoint",
        description="Sprint 18 deployment and operations architecture entrypoint",
    )

    parser.add_argument("command", choices=["start", "bootstrap", "run", "status", "health", "audit-last"])
    parser.add_argument("--root-path", default=".")
    parser.add_argument("--env-path", default=".env")
    parser.add_argument("--mode", choices=["MOCK", "LIVE", "HYBRID"], default="MOCK")
    parser.add_argument("--phase", choices=["PHASE_1", "PHASE_2", "PHASE_3"], default="PHASE_1")
    parser.add_argument("--source", choices=["logicmonitor", "mock"], default="mock")
    parser.add_argument("--target", choices=["nautobot", "mock"], default="mock")
    parser.add_argument("--parity-window", default="30d")

    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--health-host", default="127.0.0.1")
    parser.add_argument("--health-port", type=int, default=8088)

    parser.add_argument("--state-path", default="logs/operations_state.json")
    parser.add_argument("--event-log-path", default="logs/operations_events.jsonl")
    parser.add_argument("--pid-path", default="logs/operations_service.pid")
    parser.add_argument("--bootstrap-output", default="logs/operations/bootstrap")
    parser.add_argument("--run-audit-root", default="logs/operations/runs")
    parser.add_argument("--python-executable", default=sys.executable)

    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    try:
        parser = _build_parser()
        args = parser.parse_args(raw)
        config = _build_config(args)
        controller = OperationsRuntimeController(config)

        if args.command == "status":
            _emit(controller.get_status())
            return EXIT_SUCCESS
        if args.command == "health":
            _emit(controller.get_health_payload())
            return EXIT_SUCCESS
        if args.command == "audit-last":
            _emit(controller.get_last_audit())
            return EXIT_SUCCESS
        if args.command == "bootstrap":
            result = controller.bootstrap_once(trigger="manual")
            _emit(result)
            return EXIT_SUCCESS if str(result.get("status", "")).upper() == "PASS" else EXIT_HARD_FAIL
        if args.command == "run":
            result = controller.run_once(trigger="manual")
            _emit(result)
            return EXIT_SUCCESS if str(result.get("status", "")).upper() == "PASS" else EXIT_HARD_FAIL

        def _handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
            controller.request_shutdown()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        return controller.run_service_loop()

    except Exception as exc:
        _emit(
            {
                "status": "FAIL",
                "error": str(exc),
            }
        )
        return EXIT_HARD_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
