from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List

from core.bootstrap_runtime import read_boot_state
from kernel.state import EXIT_HARD_FAIL, EXIT_SUCCESS
from operations.failure_classifier import classify_failure, flatten_integration_failures


@dataclass(frozen=True)
class LiveValidationConfig:
    root_path: str
    env_path: str
    mode: str
    phase: str
    source: str
    target: str
    parity_window: str
    output_dir: str
    python_executable: str


class LiveValidationError(RuntimeError):
    """Raised for sprint 19 live validation contract violations."""


class LiveValidationRunner:
    def __init__(self, config: LiveValidationConfig) -> None:
        self.config = config
        self.root = Path(config.root_path).resolve()
        self.output_dir = self._resolve_path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, value: str) -> Path:
        path = Path(str(value))
        if path.is_absolute():
            return path
        return (self.root / path).resolve()

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_summary(self, stdout: str) -> Dict[str, Any]:
        for raw_line in reversed(str(stdout).splitlines()):
            line = raw_line.strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def _run_kernel(self, *, args: List[str]) -> Dict[str, Any]:
        command = [self.config.python_executable, "system_kernel.py", *args]
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        summary = self._parse_summary(completed.stdout)
        return {
            "command": command,
            "exit_code": int(completed.returncode),
            "stdout": str(completed.stdout or ""),
            "stderr": str(completed.stderr or ""),
            "summary": summary,
        }

    def _write_json(self, *, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _hardening_changeset(self) -> List[Dict[str, str]]:
        return [
            {
                "reason": "Bootstrap failures needed deterministic and structured failure classes",
                "impacted_component": "core/integration_runtime.py",
                "validation_proof": "integration_status now includes adapter, operation, root_cause, failure_class",
            },
            {
                "reason": "LogicMonitor live pagination required bounded safety under malformed page contracts",
                "impacted_component": "adapters/logicmonitor_adapter.py",
                "validation_proof": "pagination loop now fails closed with explicit limit error",
            },
            {
                "reason": "Ansible endpoint responses required stricter execution-result contract enforcement",
                "impacted_component": "adapters/ansible_adapter.py",
                "validation_proof": "live endpoint now rejects missing/invalid status and PASS with non-zero exit code",
            },
        ]

    def _build_report_base(self) -> Dict[str, Any]:
        return {
            "bootstrap_result": "FAIL",
            "run_result": "FAIL",
            "integration_status": "UNHEALTHY",
            "external_dependencies_ready": False,
            "deterministic_match": False,
            "drift_percentage": 0.0,
            "identity_conflicts": 0,
            "failures": [],
            "hardening_actions": self._hardening_changeset(),
            "verified_audit_artifacts": {
                "bootstrap_audit": "",
                "run_audit": "",
                "deterministic_comparison": "",
            },
            "final_system_state": {
                "state": "BLOCKED",
                "ready_for_trust": "NO",
            },
            "generated_at": self._utc_now(),
        }

    def execute(self) -> Dict[str, Any]:
        report = self._build_report_base()

        bootstrap_emit = self.output_dir / "bootstrap"
        step1 = self._run_kernel(
            args=[
                "bootstrap",
                "--mode",
                self.config.mode,
                "--phase",
                self.config.phase,
                "--env-path",
                self.config.env_path,
                "--validate-env",
                "true",
                "--init-adapters",
                "true",
                "--health-check",
                "true",
                "--emit-ready-state",
                str(bootstrap_emit),
                "--fail-closed",
                "true",
            ]
        )

        boot_state_path = bootstrap_emit / "runtime_ready_state.json"
        report["verified_audit_artifacts"]["bootstrap_audit"] = str(boot_state_path)

        boot_state: Dict[str, Any] = {}
        if boot_state_path.exists() and boot_state_path.is_file():
            boot_state = read_boot_state(boot_state_path)

        integration_status = boot_state.get("integration_status")
        if not isinstance(integration_status, dict):
            integration_status = {}

        report["external_dependencies_ready"] = bool(boot_state.get("external_dependencies_ready", False))
        report["integration_status"] = "HEALTHY" if report["external_dependencies_ready"] else "UNHEALTHY"

        failures = flatten_integration_failures(
            integration_status=integration_status,
            phase=str(self.config.phase),
        )

        if str(boot_state.get("execution_status", "")).upper() != "READY" or int(step1["exit_code"]) != 0:
            if not failures:
                root_cause = str(boot_state.get("reason") or step1.get("stderr") or "bootstrap_failed")
                failures.append(
                    {
                        "adapter": "SYSTEM",
                        "phase": str(self.config.phase),
                        "operation": "bootstrap",
                        "root_cause": root_cause,
                        "failure_class": classify_failure(root_cause=root_cause),
                    }
                )

            report["failures"] = failures
            report_path = self.output_dir / "live_validation_report.json"
            self._write_json(path=report_path, payload=report)
            report["report_path"] = str(report_path)
            return report

        report["bootstrap_result"] = "PASS"

        run_one_dir = self.output_dir / "run_1"
        step5_run_one = self._run_kernel(
            args=[
                "run",
                "--mode",
                self.config.mode,
                "--phase",
                self.config.phase,
                "--source",
                self.config.source,
                "--target",
                self.config.target,
                "--parity-window",
                self.config.parity_window,
                "--fail-closed",
                "true",
                "--emit-audit",
                str(run_one_dir),
            ]
        )

        run_one_summary = step5_run_one.get("summary") or {}
        run_one_audit_path = str(run_one_summary.get("audit_path") or "")
        report["verified_audit_artifacts"]["run_audit"] = run_one_audit_path

        if int(step5_run_one["exit_code"]) != 0 or not run_one_audit_path:
            root_cause = str(run_one_summary.get("decision") or step5_run_one.get("stderr") or "run_failed")
            report["failures"] = [
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "run",
                    "root_cause": root_cause,
                    "failure_class": classify_failure(root_cause=root_cause),
                }
            ]
            report_path = self.output_dir / "live_validation_report.json"
            self._write_json(path=report_path, payload=report)
            report["report_path"] = str(report_path)
            return report

        run_one_audit_file = Path(run_one_audit_path)
        if not run_one_audit_file.is_absolute():
            run_one_audit_file = (self.root / run_one_audit_file).resolve()
        run_one_audit = json.loads(run_one_audit_file.read_text(encoding="utf-8"))

        drift_value = float(
            run_one_audit.get("drift_percentage")
            if run_one_audit.get("drift_percentage") is not None
            else run_one_audit.get("drift_metrics", {}).get("global_drift_pct", 0.0)
        )
        identity_conflicts = int(
            run_one_audit.get("identity_conflict_count")
            if run_one_audit.get("identity_conflict_count") is not None
            else len(run_one_audit.get("identity_conflicts", []))
        )

        report["drift_percentage"] = drift_value
        report["identity_conflicts"] = identity_conflicts

        audit_failures: List[Dict[str, str]] = []
        if not isinstance(run_one_audit.get("gate_results"), list) or not run_one_audit.get("gate_results"):
            root_cause = "gate_results missing or empty"
            audit_failures.append(
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "audit_verification",
                    "root_cause": root_cause,
                    "failure_class": classify_failure(root_cause=root_cause),
                }
            )

        if str(run_one_audit.get("sst_owner", "")).strip() != str(boot_state.get("sst_owner", "")).strip():
            root_cause = "SST ownership mismatch between bootstrap and run audit"
            audit_failures.append(
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "audit_verification",
                    "root_cause": root_cause,
                    "failure_class": classify_failure(root_cause=root_cause),
                }
            )

        if identity_conflicts > 0:
            root_cause = "Identity conflicts detected in LIVE run audit"
            audit_failures.append(
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "data_integrity_verification",
                    "root_cause": root_cause,
                    "failure_class": "DATA_INTEGRITY_VIOLATION",
                }
            )

        run_two_dir = self.output_dir / "run_2"
        step8_run_two = self._run_kernel(
            args=[
                "run",
                "--mode",
                self.config.mode,
                "--phase",
                self.config.phase,
                "--source",
                self.config.source,
                "--target",
                self.config.target,
                "--parity-window",
                self.config.parity_window,
                "--fail-closed",
                "true",
                "--emit-audit",
                str(run_two_dir),
            ]
        )

        run_two_summary = step8_run_two.get("summary") or {}
        run_two_audit_path = str(run_two_summary.get("audit_path") or "")
        deterministic_match = False

        if int(step8_run_two["exit_code"]) == 0 and run_two_audit_path:
            run_two_audit_file = Path(run_two_audit_path)
            if not run_two_audit_file.is_absolute():
                run_two_audit_file = (self.root / run_two_audit_file).resolve()
            run_two_audit = json.loads(run_two_audit_file.read_text(encoding="utf-8"))

            deterministic_one = str(run_one_audit.get("deterministic_signature") or "")
            deterministic_two = str(run_two_audit.get("deterministic_signature") or "")

            drift_two = float(
                run_two_audit.get("drift_percentage")
                if run_two_audit.get("drift_percentage") is not None
                else run_two_audit.get("drift_metrics", {}).get("global_drift_pct", 0.0)
            )
            identity_two = int(
                run_two_audit.get("identity_conflict_count")
                if run_two_audit.get("identity_conflict_count") is not None
                else len(run_two_audit.get("identity_conflicts", []))
            )

            deterministic_match = (
                bool(deterministic_one)
                and deterministic_one == deterministic_two
                and drift_value == drift_two
                and identity_conflicts == identity_two
            )

            comparison_payload = {
                "deterministic_signature_run_1": deterministic_one,
                "deterministic_signature_run_2": deterministic_two,
                "drift_run_1": drift_value,
                "drift_run_2": drift_two,
                "identity_conflicts_run_1": identity_conflicts,
                "identity_conflicts_run_2": identity_two,
                "deterministic_match": deterministic_match,
            }
            comparison_path = self.output_dir / "deterministic_comparison.json"
            self._write_json(path=comparison_path, payload=comparison_payload)
            report["verified_audit_artifacts"]["deterministic_comparison"] = str(comparison_path)
        else:
            root_cause = str(run_two_summary.get("decision") or step8_run_two.get("stderr") or "second_run_failed")
            audit_failures.append(
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "determinism_validation",
                    "root_cause": root_cause,
                    "failure_class": classify_failure(root_cause=root_cause),
                }
            )

        report["deterministic_match"] = deterministic_match
        if not deterministic_match:
            audit_failures.append(
                {
                    "adapter": "SYSTEM",
                    "phase": str(self.config.phase),
                    "operation": "determinism_validation",
                    "root_cause": "Deterministic signatures or parity metrics mismatch across identical LIVE runs",
                    "failure_class": "DETERMINISM_FAILURE",
                }
            )

        report["failures"] = audit_failures
        if not audit_failures:
            report["run_result"] = "PASS"
            report["final_system_state"] = {
                "state": "LIVE_VALIDATED",
                "ready_for_trust": "YES",
            }
        else:
            report["run_result"] = "FAIL"
            report["final_system_state"] = {
                "state": "BLOCKED",
                "ready_for_trust": "NO",
            }

        report_path = self.output_dir / "live_validation_report.json"
        self._write_json(path=report_path, payload=report)
        report["report_path"] = str(report_path)
        return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operations.live_validation_runner",
        description="Sprint 19 strict LIVE validation and hardening runner",
    )
    parser.add_argument("--root-path", default=".")
    parser.add_argument("--env-path", required=True)
    parser.add_argument("--mode", choices=["LIVE"], default="LIVE")
    parser.add_argument("--phase", choices=["PHASE_1", "PHASE_2", "PHASE_3"], default="PHASE_2")
    parser.add_argument("--source", choices=["logicmonitor", "mock"], default="logicmonitor")
    parser.add_argument("--target", choices=["nautobot", "mock"], default="nautobot")
    parser.add_argument("--parity-window", default="30d")
    parser.add_argument("--output-dir", default="logs/sprint19_validation")
    parser.add_argument("--python-executable", default=sys.executable)
    return parser


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    config = LiveValidationConfig(
        root_path=str(args.root_path),
        env_path=str(args.env_path),
        mode=str(args.mode).strip().upper(),
        phase=str(args.phase).strip().upper(),
        source=str(args.source).strip().lower(),
        target=str(args.target).strip().lower(),
        parity_window=str(args.parity_window).strip().lower(),
        output_dir=str(args.output_dir),
        python_executable=str(args.python_executable),
    )

    report = LiveValidationRunner(config).execute()
    print(json.dumps(report, sort_keys=True))

    final_state = str(report.get("final_system_state", {}).get("state", "BLOCKED"))
    return EXIT_SUCCESS if final_state == "LIVE_VALIDATED" else EXIT_HARD_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
