#!/usr/bin/env python3
"""Sprint 2 Ansible validation runner (orchestration only)."""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import logging
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import sys

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from legacy.ansible_validation.inventory.dynamic_inventory import InventoryLoadError, load_ip_targets
from legacy.execution_contract.ansible_error_mapper import MapperInputError, map_normalized_ansible_result
from legacy.execution_contract.failure_taxonomy import FailureCategory, classify_failure_category
from legacy.execution_contract.schema import build_error, build_result, write_validation_output

ALLOWED_MODES = {"READ_ONLY", "VALIDATION_ONLY", "CHANGE_ENABLED"}
IN_SCOPE_VENDORS = {"aruba", "cisco", "fortinet", "juniper", "meraki", "unknown"}

FORBIDDEN_PLAYBOOK_TOKENS = {
    "ios_config:",
    "nxos_config:",
    "junos_config:",
    "eos_config:",
    "arubaoss_config:",
    "aoscx_config:",
    "fortios_config:",
    "meraki_config:",
    "cli_config:",
    "ansible.builtin.copy:",
    "ansible.builtin.template:",
    "ansible.builtin.lineinfile:",
    "ansible.builtin.replace:",
}


class ValidationSystemError(RuntimeError):
    """Raised for systemic runner failures requiring fail-closed stop."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "VALIDATION_SYSTEM_ERROR",
        stage: str = "runner",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.details = dict(details or {})


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("ansible_validation_runner")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _log(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, sort_keys=True))


def _trim_text(value: str, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _normalize_vendor(vendor: str) -> str:
    normalized = str(vendor or "unknown").strip().lower()
    if normalized in IN_SCOPE_VENDORS:
        return normalized
    return "unknown"


def _ensure_read_only_playbook(playbook_path: Path) -> None:
    if not playbook_path.exists() or not playbook_path.is_file():
        raise ValidationSystemError("validation playbook missing", code="PLAYBOOK_MISSING", stage="preflight")

    content = playbook_path.read_text(encoding="utf-8").lower()
    for token in FORBIDDEN_PLAYBOOK_TOKENS:
        if token in content:
            raise ValidationSystemError(
                f"forbidden configuration module detected: {token}",
                code="PLAYBOOK_MUTATION_BLOCKED",
                stage="preflight",
            )


def _load_vendor_map(inventory_path: Path) -> Dict[str, str]:
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationSystemError(
            f"inventory json parse failed: {exc}",
            code="INVENTORY_PARSE_ERROR",
            stage="inventory",
        ) from exc

    if not isinstance(payload, Mapping):
        raise ValidationSystemError("inventory root must be object", code="INVENTORY_SCHEMA_ERROR", stage="inventory")

    devices = payload.get("devices")
    if not isinstance(devices, list):
        raise ValidationSystemError("inventory devices must be list", code="INVENTORY_SCHEMA_ERROR", stage="inventory")

    vendor_map: Dict[str, str] = {}

    for index, record in enumerate(devices):
        if not isinstance(record, Mapping):
            raise ValidationSystemError(
                f"inventory devices[{index}] is not an object",
                code="INVENTORY_SCHEMA_ERROR",
                stage="inventory",
            )

        ip_raw = record.get("ip_address")
        if not isinstance(ip_raw, str) or not ip_raw.strip():
            raise ValidationSystemError(
                f"inventory devices[{index}] missing ip_address",
                code="INVENTORY_SCHEMA_ERROR",
                stage="inventory",
            )

        try:
            ip_normalized = str(ipaddress.ip_address(ip_raw.strip()))
        except ValueError as exc:
            raise ValidationSystemError(
                f"inventory devices[{index}] invalid ip_address",
                code="INVENTORY_SCHEMA_ERROR",
                stage="inventory",
            ) from exc

        vendor_raw = record.get("vendor") if isinstance(record.get("vendor"), str) else "unknown"
        vendor_map[ip_normalized] = _normalize_vendor(vendor_raw)

    return vendor_map


def _ordered_targets(ip_targets: Sequence[str], vendor_map: Mapping[str, str]) -> List[str]:
    aruba = [ip for ip in ip_targets if vendor_map.get(ip, "unknown") == "aruba"]
    others = [ip for ip in ip_targets if vendor_map.get(ip, "unknown") != "aruba"]
    return aruba + others


def _write_temp_inventory(ip_address: str, username: str, password: str) -> Path:
    content = (
        "[validation_targets]\n"
        f"{ip_address} "
        f"ansible_host={ip_address} "
        f"ansible_user={username} "
        f"ansible_password={password} "
        "ansible_connection=ssh "
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'\n"
    )

    tmp_file = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".ini")
    tmp_file.write(content)
    tmp_file.flush()
    tmp_file.close()
    return Path(tmp_file.name)


def _normalized_ansible_failure(
    *,
    outcome: str,
    failure_text: str,
    return_code: Optional[int],
) -> Dict[str, Any]:
    return {
        "outcome": outcome,
        "failure_text": _trim_text(failure_text),
        "return_code": return_code,
        "source": "ansible_runner",
    }


def _run_ansible_once(
    *,
    ip_address: str,
    username: str,
    password: str,
    timeout_seconds: int,
    playbook_path: Path,
) -> Tuple[bool, Mapping[str, Any]]:
    inventory_file = _write_temp_inventory(ip_address, username, password)

    command = [
        "ansible-playbook",
        str(playbook_path),
        "-i",
        str(inventory_file),
        "--limit",
        ip_address,
        "--extra-vars",
        f"validation_timeout={timeout_seconds}",
    ]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(5, timeout_seconds + 10),
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValidationSystemError(
            "ansible-playbook executable not found",
            code="ANSIBLE_EXECUTION_FAILURE",
            stage="ansible_execution",
        ) from exc
    except subprocess.TimeoutExpired:
        return False, _normalized_ansible_failure(
            outcome="TIMEOUT",
            failure_text="ansible execution timeout",
            return_code=None,
        )
    finally:
        try:
            inventory_file.unlink(missing_ok=True)
        except Exception:
            pass

    if proc.returncode == 0:
        return True, {}

    stderr_text = (proc.stderr or "").strip()
    stdout_text = (proc.stdout or "").strip()
    failure_text = stderr_text or stdout_text or "ansible validation failed"

    return False, _normalized_ansible_failure(
        outcome="PLAYBOOK_FAILED",
        failure_text=failure_text,
        return_code=proc.returncode,
    )


def _validate_aruba_device(
    ip_address: str,
    vendor: str,
    *,
    username: str,
    password: str,
    timeout_seconds: int,
    max_retries: int,
    playbook_path: Path,
    logger: logging.Logger,
) -> Dict[str, Any]:
    last_result: Optional[Dict[str, Any]] = None

    for attempt in range(1, max_retries + 1):
        success, normalized_failure = _run_ansible_once(
            ip_address=ip_address,
            username=username,
            password=password,
            timeout_seconds=timeout_seconds,
            playbook_path=playbook_path,
        )

        if success:
            return build_result(
                ip_address=ip_address,
                vendor=vendor,
                execution_state="SUCCESS",
                connectivity_status="REACHABLE",
                validation_status="PASSED",
                facts_collected={"source": "ansible", "method": "gather_facts", "read_only": True},
                error=None,
                attempt_count=attempt,
            )

        try:
            mapped = map_normalized_ansible_result(normalized_failure)
        except MapperInputError as exc:
            classification = classify_failure_category(FailureCategory.RUNTIME)
            mapped_details = {
                "normalized_ansible_result": dict(normalized_failure),
                "mapper_error": str(exc),
            }
            last_result = build_result(
                ip_address=ip_address,
                vendor=vendor,
                execution_state=classification.execution_state,
                connectivity_status=classification.connectivity_status,
                validation_status=classification.validation_status,
                facts_collected=None,
                error=build_error(
                    code="MAPPER_INPUT_INVALID",
                    message="normalized ansible result is invalid",
                    source="ansible_error_mapper",
                    details=mapped_details,
                ),
                attempt_count=attempt,
            )
            return last_result

        last_result = build_result(
            ip_address=ip_address,
            vendor=vendor,
            execution_state=mapped.execution_state,
            connectivity_status=mapped.connectivity_status,
            validation_status=mapped.validation_status,
            facts_collected=None,
            error=build_error(
                code=mapped.error_code,
                message=mapped.error_message,
                source="ansible_error_mapper",
                details=mapped.details,
            ),
            attempt_count=attempt,
        )

        _log(
            logger,
            "device_validation_retry",
            ip_address=ip_address,
            stage="ansible_execution",
            failure_code=mapped.error_code,
            attempt=attempt,
        )

        if mapped.category in {FailureCategory.RUNTIME, FailureCategory.VALIDATION}:
            return last_result

        if attempt < max_retries:
            time.sleep(0.5 * attempt)

    if last_result is None:
        classification = classify_failure_category(FailureCategory.RUNTIME)
        last_result = build_result(
            ip_address=ip_address,
            vendor=vendor,
            execution_state=classification.execution_state,
            connectivity_status=classification.connectivity_status,
            validation_status=classification.validation_status,
            facts_collected=None,
            error=build_error(
                code="ANSIBLE_RUNTIME_INCONSISTENT",
                message="ansible execution failed without deterministic classification",
                source="ansible_error_mapper",
                details={
                    "normalized_ansible_result": {
                        "outcome": "UNKNOWN",
                        "failure_text": "ansible execution failed without output",
                        "return_code": None,
                        "source": "ansible_runner",
                    }
                },
            ),
            attempt_count=max_retries,
        )

    _log(
        logger,
        "device_validation_failed",
        ip_address=ip_address,
        stage="ansible_execution",
        failure_code=(last_result["error"] or {}).get("code"),
    )
    return last_result


def _not_executed_result(ip_address: str, vendor: str) -> Dict[str, Any]:
    classification = classify_failure_category(FailureCategory.NOT_EXECUTED)
    return build_result(
        ip_address=ip_address,
        vendor=vendor,
        execution_state=classification.execution_state,
        connectivity_status=classification.connectivity_status,
        validation_status=classification.validation_status,
        facts_collected=None,
        error=build_error(
            code="VENDOR_PATH_NOT_IMPLEMENTED",
            message="vendor path not implemented in Sprint 2",
            source="runner",
            details={"vendor": _normalize_vendor(vendor)},
        ),
        attempt_count=0,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sprint 2 Ansible validation runner")
    parser.add_argument("--inventory-input", default="inventory_output.json", help="Sprint 1 inventory output path")
    parser.add_argument(
        "--output",
        default="ansible_validation/outputs/validation_results.json",
        help="Validation results output path",
    )
    parser.add_argument("--username", default="<SSH_USERNAME>", help="SSH username")
    parser.add_argument("--password", default="<SSH_PASSWORD>", help="SSH password")
    parser.add_argument("--execution-mode", default="VALIDATION_ONLY", help="Execution mode")
    parser.add_argument("--max-concurrency", type=int, default=20, help="Max concurrent sessions (<=50)")
    parser.add_argument("--max-retries", type=int, default=3, help="Per-device retry limit (<=3)")
    parser.add_argument("--timeout", type=int, default=20, help="SSH validation timeout in seconds")
    parser.add_argument("--max-targets", type=int, default=0, help="Optional cap for processed targets (0 = all)")
    return parser


def run_orchestration(args: argparse.Namespace, *, runtime_standard: str, python_runtime: str) -> Dict[str, Any]:
    """Execute orchestration only, assuming bootstrap already validated runtime."""
    logger = _build_logger()
    run_id = datetime.now(timezone.utc).strftime("validation-%Y%m%dT%H%M%SZ")
    _log(logger, "validation_run_start", run_id=run_id, stage="runner")

    mode = str(args.execution_mode).strip().upper()
    if mode not in ALLOWED_MODES:
        raise ValidationSystemError("invalid execution mode", code="INVALID_EXECUTION_MODE", stage="preflight")
    if mode != "VALIDATION_ONLY":
        raise ValidationSystemError(
            "Sprint 2 runner requires VALIDATION_ONLY mode",
            code="INVALID_EXECUTION_MODE",
            stage="preflight",
        )

    max_concurrency = max(1, min(int(args.max_concurrency), 50))
    max_retries = max(1, min(int(args.max_retries), 3))

    inventory_path = Path(args.inventory_input)
    playbook_path = Path("ansible_validation/playbooks/validate_device.yml")

    _ensure_read_only_playbook(playbook_path)
    ip_targets = load_ip_targets(inventory_path)
    vendor_map = _load_vendor_map(inventory_path)

    if args.max_targets > 0:
        ip_targets = ip_targets[: args.max_targets]

    ordered_targets = _ordered_targets(ip_targets, vendor_map)
    aruba_targets = [ip for ip in ordered_targets if vendor_map.get(ip, "unknown") == "aruba"]
    non_aruba_targets = [ip for ip in ordered_targets if vendor_map.get(ip, "unknown") != "aruba"]

    results: List[Dict[str, Any]] = []

    for ip_address in non_aruba_targets:
        results.append(_not_executed_result(ip_address, vendor_map.get(ip_address, "unknown")))

    if aruba_targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            future_map = {
                executor.submit(
                    _validate_aruba_device,
                    ip_address,
                    vendor_map.get(ip_address, "unknown"),
                    username=args.username,
                    password=args.password,
                    timeout_seconds=int(args.timeout),
                    max_retries=max_retries,
                    playbook_path=playbook_path,
                    logger=logger,
                ): ip_address
                for ip_address in aruba_targets
            }
            for future in concurrent.futures.as_completed(future_map):
                results.append(future.result())

    results.sort(key=lambda rec: rec["ip_address"])

    output_path = Path(args.output)
    write_validation_output(
        output_path=output_path,
        run_id=run_id,
        execution_mode=mode,
        runtime_standard=runtime_standard,
        python_runtime=python_runtime,
        processed_targets=len(ordered_targets),
        aruba_targets=len(aruba_targets),
        non_aruba_targets=len(non_aruba_targets),
        max_concurrency=max_concurrency,
        max_retries=max_retries,
        results=results,
    )

    has_failure = any(result["execution_state"].startswith("FAIL_") for result in results)
    status = "FAIL" if has_failure else "PASS"

    _log(
        logger,
        "validation_run_complete",
        run_id=run_id,
        stage="runner",
        processed_targets=len(ordered_targets),
        status=status,
        output_file=str(output_path),
    )

    return {
        "status": status,
        "output_file": str(output_path),
        "run_id": run_id,
        "exit_code": 2 if has_failure else 0,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "status": "FAIL",
                "execution_state": "FAIL_RUNTIME",
                "error": {
                    "code": "BOOTSTRAP_REQUIRED",
                    "message": "run_validation.py cannot execute directly; use bootstrap.py entrypoint",
                    "source": "runner",
                    "details": {},
                },
            },
            sort_keys=True,
        )
    )
    raise SystemExit(2)
