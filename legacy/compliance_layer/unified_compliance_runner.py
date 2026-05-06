"""Unified fail-closed compliance orchestration runner for Sprint 11."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from legacy.compliance_layer.compliance_aggregation_engine import build_compliance_report
from legacy.compliance_layer.gate_sequencer import (
    GateDefinition,
    default_gate_definitions,
    expected_gate_sequence,
    required_artifact_paths,
    validate_gate_sequence,
)

RunnerExecutor = Callable[[GateDefinition, Path, int], dict[str, Any]]
NowProvider = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(now_provider: NowProvider) -> str:
    return now_provider().isoformat()


def _hash_output(stdout: str, stderr: str) -> str:
    payload = f"{stdout}\n{stderr}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None

    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    first = raw.find("{")
    last = raw.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None

    candidate = raw[first : last + 1]
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if isinstance(loaded, dict):
        return loaded
    return None


def _truncate(text: str, limit: int = 1600) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[:limit]


class UnifiedComplianceRunner:
    """Runs deterministic gate checks and emits one normalized compliance contract."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        python_executable: str | None = None,
        timeout_seconds: int = 300,
        gates: list[GateDefinition] | None = None,
        executor: RunnerExecutor | None = None,
        now_provider: NowProvider | None = None,
    ) -> None:
        self.root = root or Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.now_provider = now_provider or _utc_now
        self.gates = gates or default_gate_definitions(python_executable)
        self.executor = executor or self._execute_subprocess_gate

    def _build_gate_result(
        self,
        *,
        gate: GateDefinition,
        status: str,
        violation_count: int,
        violations: list[dict[str, Any]],
        started_at: str,
        finished_at: str,
        command: list[str] | None = None,
        exit_code: int = 0,
        output_hash: str = "",
        stdout_excerpt: str = "",
        stderr_excerpt: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        return {
            "layer": gate.layer,
            "rule_id": gate.rule_id,
            "status": status,
            "violation_count": int(violation_count),
            "violations": violations,
            "started_at": started_at,
            "finished_at": finished_at,
            "command": list(command or []),
            "exit_code": int(exit_code),
            "output_hash": output_hash,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
            "note": note,
        }

    def _run_sci_authority_gate(self, gate: GateDefinition) -> dict[str, Any]:
        started_at = _iso(self.now_provider)
        violations: list[dict[str, Any]] = []

        for artifact_path in required_artifact_paths(self.root):
            if not artifact_path.exists() or not artifact_path.is_file():
                violations.append(
                    {
                        "rule_id": gate.rule_id,
                        "code": "SCI_ARTIFACT_MISSING",
                        "layer": gate.layer,
                        "message": f"required artifact missing: {artifact_path.relative_to(self.root).as_posix()}",
                    }
                )
                continue
            try:
                artifact_path.read_text(encoding="utf-8")
            except Exception as exc:
                violations.append(
                    {
                        "rule_id": gate.rule_id,
                        "code": "SCI_ARTIFACT_UNREADABLE",
                        "layer": gate.layer,
                        "message": f"artifact unreadable: {artifact_path.relative_to(self.root).as_posix()} ({exc})",
                    }
                )

        finished_at = _iso(self.now_provider)
        if violations:
            return self._build_gate_result(
                gate=gate,
                status="FAIL",
                violation_count=len(violations),
                violations=violations,
                started_at=started_at,
                finished_at=finished_at,
                command=["artifact_readability_check"],
                output_hash=_hash_output(json.dumps(violations, sort_keys=True), ""),
                note="SCI authority check failed",
            )

        return self._build_gate_result(
            gate=gate,
            status="PASS",
            violation_count=0,
            violations=[],
            started_at=started_at,
            finished_at=finished_at,
            command=["artifact_readability_check"],
            output_hash=_hash_output("SCI_ARTIFACTS_OK", ""),
            note="SCI authority check passed",
        )

    def _execute_subprocess_gate(self, gate: GateDefinition, root: Path, timeout_seconds: int) -> dict[str, Any]:
        if gate.command is None:
            return self._run_sci_authority_gate(gate)

        started_at = _iso(self.now_provider)
        process = subprocess.run(
            list(gate.command),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        finished_at = _iso(self.now_provider)

        stdout = process.stdout or ""
        stderr = process.stderr or ""
        output_hash = _hash_output(stdout, stderr)
        violations: list[dict[str, Any]] = []

        if gate.expects_json:
            payload = _extract_json_payload(stdout)
            if payload is None:
                violations.append(
                    {
                        "rule_id": gate.rule_id,
                        "code": "INVALID_JSON_OUTPUT",
                        "layer": gate.layer,
                        "message": "gate did not emit parseable JSON output",
                    }
                )
            elif gate.name == "EMV":
                emv_status = str(payload.get("emv_status", "")).upper()
                violation_count = int(payload.get("violation_count", -1))
                if emv_status != "PASS" or violation_count != 0:
                    violations.append(
                        {
                            "rule_id": gate.rule_id,
                            "code": "EMV_NOT_CLEAN",
                            "layer": gate.layer,
                            "message": f"EMV expected PASS/0 but received status={emv_status} violations={violation_count}",
                        }
                    )
            elif gate.name == "CI_KERNEL":
                ci_status = str(payload.get("ci_status", "")).upper()
                violation_count = int(payload.get("violation_count", -1))
                if ci_status != "PASS" or violation_count != 0:
                    violations.append(
                        {
                            "rule_id": gate.rule_id,
                            "code": "CI_NOT_CLEAN",
                            "layer": gate.layer,
                            "message": f"CI expected PASS/0 but received status={ci_status} violations={violation_count}",
                        }
                    )

        if process.returncode != 0:
            violations.append(
                {
                    "rule_id": gate.rule_id,
                    "code": "SUBPROCESS_NON_ZERO",
                    "layer": gate.layer,
                    "message": f"gate command exited with code {process.returncode}",
                }
            )

        if violations:
            return self._build_gate_result(
                gate=gate,
                status="FAIL",
                violation_count=len(violations),
                violations=violations,
                started_at=started_at,
                finished_at=finished_at,
                command=list(gate.command),
                exit_code=process.returncode,
                output_hash=output_hash,
                stdout_excerpt=_truncate(stdout),
                stderr_excerpt=_truncate(stderr),
                note="gate execution failed",
            )

        return self._build_gate_result(
            gate=gate,
            status="PASS",
            violation_count=0,
            violations=[],
            started_at=started_at,
            finished_at=finished_at,
            command=list(gate.command),
            exit_code=process.returncode,
            output_hash=output_hash,
            stdout_excerpt=_truncate(stdout),
            stderr_excerpt=_truncate(stderr),
            note="gate execution passed",
        )

    def _skipped_gate_result(self, gate: GateDefinition, reason_gate: str) -> dict[str, Any]:
        now = _iso(self.now_provider)
        return self._build_gate_result(
            gate=gate,
            status="SKIPPED",
            violation_count=0,
            violations=[],
            started_at=now,
            finished_at=now,
            command=list(gate.command or []),
            note=f"blocked due to fail-closed gate: {reason_gate}",
        )

    def _sequence_failure_report(self, issues: list[str]) -> dict[str, Any]:
        started_at = _iso(self.now_provider)
        completed_at = _iso(self.now_provider)

        layer_results: dict[str, dict[str, Any]] = {}
        execution_trace: list[dict[str, Any]] = []

        sequence_gate = GateDefinition(
            name="SCI_AUTHORITY",
            layer="sequencer",
            rule_id="UCO-SEQ-001",
            command=None,
            expects_json=False,
        )

        violations = [
            {
                "rule_id": sequence_gate.rule_id,
                "code": "NON_DETERMINISTIC_ORDER",
                "layer": sequence_gate.layer,
                "message": issue,
            }
            for issue in issues
        ]

        layer_results[sequence_gate.name] = self._build_gate_result(
            gate=sequence_gate,
            status="FAIL",
            violation_count=len(violations),
            violations=violations,
            started_at=started_at,
            finished_at=completed_at,
            command=["sequence_validation"],
            output_hash=_hash_output(json.dumps(issues, sort_keys=True), ""),
            note="sequence contract failure",
        )

        execution_trace.append(
            {
                "step": 1,
                "gate": sequence_gate.name,
                "layer": sequence_gate.layer,
                "status": "FAIL",
                "rule_id": sequence_gate.rule_id,
            }
        )

        for step, gate_name in enumerate(expected_gate_sequence()[1:], start=2):
            gate = next((item for item in self.gates if item.name == gate_name), None)
            if gate is None:
                gate = GateDefinition(gate_name, "sequencer", "UCO-SEQ-001", None, False)
            layer_results[gate_name] = self._skipped_gate_result(gate, "SCI_AUTHORITY")
            execution_trace.append(
                {
                    "step": step,
                    "gate": gate_name,
                    "layer": gate.layer,
                    "status": "SKIPPED",
                    "rule_id": gate.rule_id,
                }
            )

        return build_compliance_report(
            layer_results=layer_results,
            execution_trace=execution_trace,
            started_at=started_at,
            completed_at=completed_at,
            failed_gate="SCI_AUTHORITY",
        )

    def run(self) -> dict[str, Any]:
        sequence_ok, issues = validate_gate_sequence(self.gates)
        if not sequence_ok:
            return self._sequence_failure_report(issues)

        started_at = _iso(self.now_provider)
        layer_results: dict[str, dict[str, Any]] = {}
        execution_trace: list[dict[str, Any]] = []
        failed_gate: str | None = None

        for index, gate in enumerate(self.gates, start=1):
            gate_result = self.executor(gate, self.root, self.timeout_seconds)
            layer_results[gate.name] = gate_result
            execution_trace.append(
                {
                    "step": index,
                    "gate": gate.name,
                    "layer": gate.layer,
                    "status": gate_result.get("status", "UNKNOWN"),
                    "rule_id": gate.rule_id,
                }
            )

            if gate_result.get("status") != "PASS":
                failed_gate = gate.name
                break

        if failed_gate:
            start_index = next(i for i, gate in enumerate(self.gates) if gate.name == failed_gate)
            for gate in self.gates[start_index + 1 :]:
                layer_results[gate.name] = self._skipped_gate_result(gate, failed_gate)
                execution_trace.append(
                    {
                        "step": len(execution_trace) + 1,
                        "gate": gate.name,
                        "layer": gate.layer,
                        "status": "SKIPPED",
                        "rule_id": gate.rule_id,
                    }
                )

        completed_at = _iso(self.now_provider)
        return build_compliance_report(
            layer_results=layer_results,
            execution_trace=execution_trace,
            started_at=started_at,
            completed_at=completed_at,
            failed_gate=failed_gate,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sprint 11 unified compliance gate runner")
    parser.add_argument("--root", default=".", help="Workspace root path")
    parser.add_argument("--python-executable", default=sys.executable, help="Python interpreter used for gate subprocesses")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Per-gate subprocess timeout in seconds")
    parser.add_argument("--output", default="", help="Optional JSON output file path")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    runner = UnifiedComplianceRunner(
        root=Path(args.root).resolve(),
        python_executable=args.python_executable,
        timeout_seconds=args.timeout_seconds,
    )
    report = runner.run()

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("compliance_status") == "COMPLIANT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
