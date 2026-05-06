from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List
from urllib import error as urllib_error
from urllib import request as urllib_request

from adapters.base import AdapterBase, AdapterError, AdapterRuntimeConfig
from observability.logger import classify_error, log_event
from runtime.mock import fixtures


@dataclass(frozen=True)
class AnsibleConfig:
    mode: str
    inventory_source_path: str = ""
    playbook_command: str = ""
    execution_endpoint: str = ""
    runner_path: str = ""


class AnsibleAdapter(AdapterBase):
    def __init__(self, config: AnsibleConfig) -> None:
        super().__init__(AdapterRuntimeConfig(mode=config.mode, name="AnsibleAdapter"))
        self._inventory_source_path = str(config.inventory_source_path or "").strip()
        self._playbook_command = str(config.playbook_command or "").strip()
        self._execution_endpoint = str(config.execution_endpoint or "").strip()
        self._runner_path = str(config.runner_path or "").strip()

    def _endpoint(self, suffix: str) -> str:
        if not self._execution_endpoint:
            raise AdapterError("ANSIBLE_EXECUTION_ENDPOINT is not configured")
        return self._execution_endpoint.rstrip("/") + suffix

    def _request_json(self, *, method: str, endpoint: str, payload: Dict[str, Any] | None = None) -> Any:
        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="ansible",
            operation=f"{method.upper()} {endpoint}",
            status="START",
            external_system="ansible",
        )

        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib_request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            with urllib_request.urlopen(request, timeout=25) as response:
                content = response.read().decode("utf-8")
                http_status = int(getattr(response, "status", 200) or 200)
        except urllib_error.URLError as exc:
            classified = classify_error(exc)
            latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
            log_event(
                level="ERROR",
                component="adapter",
                adapter="ansible",
                operation=f"{method.upper()} {endpoint}",
                status="FAIL",
                duration_ms=latency_ms,
                latency_ms=latency_ms,
                external_system="ansible",
                **classified,
            )
            raise AdapterError(f"Ansible execution endpoint request failed: {exc}") from exc
        except Exception as exc:
            classified = classify_error(exc)
            latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
            log_event(
                level="ERROR",
                component="adapter",
                adapter="ansible",
                operation=f"{method.upper()} {endpoint}",
                status="FAIL",
                duration_ms=latency_ms,
                latency_ms=latency_ms,
                external_system="ansible",
                **classified,
            )
            raise AdapterError(f"Ansible execution endpoint request failed: {exc}") from exc

        latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
        log_event(
            level="INFO",
            component="adapter",
            adapter="ansible",
            operation=f"{method.upper()} {endpoint}",
            status="SUCCESS",
            duration_ms=latency_ms,
            latency_ms=latency_ms,
            http_status=http_status,
            external_system="ansible",
        )

        if not content.strip():
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise AdapterError("Ansible endpoint returned invalid JSON") from exc

    def _normalize_inventory_records(self, payload: Any) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
            records = [item for item in payload["records"] if isinstance(item, dict)]

        if not records:
            raise AdapterError("Ansible live inventory payload does not contain records")

        normalized: List[Dict[str, Any]] = []
        for row in records:
            normalized.append(
                {
                    "ip_address": str(row.get("ip_address") or row.get("ip") or "").strip(),
                    "hostname": str(row.get("hostname") or row.get("host") or "").strip(),
                    "serial_number": str(row.get("serial_number") or row.get("serial") or "").strip(),
                    "vendor": str(row.get("vendor") or "").strip(),
                    "model": str(row.get("model") or "").strip(),
                    "site": str(row.get("site") or "").strip(),
                    "status": str(row.get("status") or "unknown").strip(),
                    "authority_owner": "ANSIBLE_POSTGRES",
                    "ansible_validated": True,
                    "sst_source": "ANSIBLE_POSTGRES",
                }
            )

        normalized.sort(key=lambda item: item.get("ip_address", ""))
        return self._validate_records(normalized, source="live_normalized")

    def _load_live_inventory_payload(self) -> Any:
        if self._execution_endpoint:
            return self._request_json(
                method="GET",
                endpoint=self._endpoint("/inventory"),
            )

        if not self._inventory_source_path:
            raise AdapterError("ANSIBLE_INVENTORY_SOURCE_PATH is not configured")
        source_path = Path(self._inventory_source_path)
        if not source_path.exists() or not source_path.is_file():
            raise AdapterError(f"Ansible inventory source file missing: {source_path}")
        return json.loads(source_path.read_text(encoding="utf-8"))

    def _mock_fetch_validated_inventory(self) -> List[Dict[str, Any]]:
        records = fixtures.ansible_validated_inventory_records()
        return self._validate_records(records, source="mock")

    def _live_fetch_validated_inventory(self) -> List[Dict[str, Any]]:
        payload = self._load_live_inventory_payload()
        return self._normalize_inventory_records(payload)

    def _mock_execute_playbook(self, *, operation: str, validated_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "operation": str(operation),
            "status": "PASS",
            "records_processed": len(validated_records),
            "execution_mode": "MOCK",
        }

    def _live_execute_playbook(
        self,
        *,
        operation: str,
        validated_records: List[Dict[str, Any]],
        governance_approved: bool,
    ) -> Dict[str, Any]:
        operation_token = str(operation).strip().upper()
        mutation_requested = operation_token.startswith("MUTATE")
        if mutation_requested and not governance_approved:
            raise AdapterError("Governance approval required for mutation operation")

        if self._execution_endpoint:
            response = self._request_json(
                method="POST",
                endpoint=self._endpoint("/run"),
                payload={
                    "operation": str(operation),
                    "records": validated_records,
                    "governance_approved": bool(governance_approved),
                },
            )

            if not isinstance(response, dict):
                raise AdapterError("Ansible execution endpoint returned non-object response")

            if "status" not in response:
                raise AdapterError("Ansible execution endpoint response missing status field")

            status = str(response.get("status") or "FAIL").upper().strip()
            if status not in {"PASS", "FAIL"}:
                raise AdapterError("Ansible execution endpoint response has invalid status value")

            exit_code = int(response.get("exit_code") or (0 if status == "PASS" else 1))
            if status == "PASS" and exit_code != 0:
                raise AdapterError("Ansible execution endpoint returned PASS with non-zero exit_code")

            return {
                "operation": str(operation),
                "status": "PASS" if status == "PASS" else "FAIL",
                "records_processed": len(validated_records),
                "execution_mode": "LIVE_ENDPOINT",
                "exit_code": exit_code,
                "stdout_excerpt": str(response.get("stdout") or "")[:1200],
                "stderr_excerpt": str(response.get("stderr") or "")[:1200],
                "result": response,
            }

        if not self._runner_path:
            raise AdapterError("ANSIBLE_EXECUTION_ENDPOINT or ANSIBLE_RUNNER_PATH must be configured")

        if not self._playbook_command:
            raise AdapterError("ANSIBLE_PLAYBOOK_COMMAND is required when using ANSIBLE_RUNNER_PATH")

        try:
            import ansible_runner
        except Exception as exc:
            raise AdapterError("ansible_runner is unavailable for live execution") from exc

        inventory = self._inventory_source_path if self._inventory_source_path else None
        result = ansible_runner.run(
            private_data_dir=self._runner_path,
            playbook=self._playbook_command,
            inventory=inventory,
            quiet=True,
        )

        rc_value = int(getattr(result, "rc", 1))
        status = str(getattr(result, "status", "failed")).upper()
        stdout_value = str(getattr(result, "stdout", "") or "")
        return {
            "operation": str(operation),
            "status": "PASS" if rc_value == 0 and status in {"SUCCESSFUL", "PASS"} else "FAIL",
            "records_processed": len(validated_records),
            "execution_mode": "LIVE_RUNNER",
            "exit_code": rc_value,
            "stdout_excerpt": stdout_value[:1200],
            "stderr_excerpt": "",
            "result": {
                "status": status,
                "rc": rc_value,
            },
        }

    def fetch_validated_inventory(self) -> List[Dict[str, Any]]:
        return self._run_mode_operation(
            operation="fetch_validated_inventory",
            live_callable=self._live_fetch_validated_inventory,
            mock_callable=self._mock_fetch_validated_inventory,
        )

    def execute_playbook(
        self,
        *,
        operation: str,
        records: List[Dict[str, Any]],
        governance_approved: bool = False,
    ) -> Dict[str, Any]:
        validated = self._validate_records(records, source="execution_input")
        return self._run_mode_operation(
            operation="execute_playbook",
            live_callable=lambda: self._live_execute_playbook(
                operation=operation,
                validated_records=validated,
                governance_approved=governance_approved,
            ),
            mock_callable=lambda: self._mock_execute_playbook(
                operation=operation,
                validated_records=validated,
            ),
        )

    def validate_credentials(self) -> Dict[str, Any]:
        if self.mode == "MOCK":
            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "live_ready": False,
                "message": "mock mode does not require credentials",
            }
            self._record_operation_status(
                operation="validate_credentials",
                status="PASS",
                mode_used=self.mode,
                details={"live_ready": False},
            )
            return status

        missing: List[str] = []
        if not self._execution_endpoint and not self._runner_path:
            missing.append("ANSIBLE_EXECUTION_ENDPOINT|ANSIBLE_RUNNER_PATH")

        if missing and self.mode == "LIVE":
            self._record_operation_status(
                operation="validate_credentials",
                status="FAIL",
                mode_used=self.mode,
                details={"missing": missing},
            )
            raise AdapterError("Ansible missing credentials: " + ", ".join(missing))

        status_token = "PASS" if not missing else "WARN"
        self._record_operation_status(
            operation="validate_credentials",
            status=status_token,
            mode_used=self.mode,
            details={"missing": missing, "live_ready": len(missing) == 0},
        )
        return {
            "status": status_token,
            "adapter": self.name,
            "mode": self.mode,
            "missing": missing,
            "live_ready": len(missing) == 0,
        }

    def init(self) -> Dict[str, Any]:
        try:
            if self.mode == "LIVE" and not self._execution_endpoint and not self._runner_path:
                raise AdapterError("ANSIBLE_EXECUTION_ENDPOINT or ANSIBLE_RUNNER_PATH is not configured")

            if self.mode in {"LIVE", "HYBRID"} and self._inventory_source_path:
                source_path = Path(self._inventory_source_path)
                if not source_path.exists() or not source_path.is_file():
                    raise AdapterError(f"Ansible inventory source file missing: {source_path}")

            if self._runner_path:
                runner_path = Path(self._runner_path)
                if not runner_path.exists() or not runner_path.is_dir():
                    raise AdapterError(f"Ansible runner path missing: {runner_path}")

            if self._execution_endpoint:
                health_payload = self._request_json(
                    method="GET",
                    endpoint=self._endpoint("/health"),
                )
                if isinstance(health_payload, dict):
                    status = str(health_payload.get("status") or "PASS").upper()
                    if status not in {"PASS", "OK", "HEALTHY"}:
                        raise AdapterError("Ansible execution endpoint health is not PASS")

            base_status = super().init()
            base_status["inventory_source"] = self._inventory_source_path
            base_status["backend"] = "endpoint" if self._execution_endpoint else "runner"
            return base_status
        except Exception as exc:
            self._record_operation_status(
                operation="init",
                status="FAIL",
                mode_used=self.mode,
                details={"error": str(exc)},
            )
            raise

    def health_check(self, *, phase: str = "") -> Dict[str, Any]:
        try:
            if self._execution_endpoint:
                health_payload = self._request_json(
                    method="GET",
                    endpoint=self._endpoint("/health"),
                )
                if isinstance(health_payload, dict):
                    status = str(health_payload.get("status") or "PASS").upper()
                    if status not in {"PASS", "OK", "HEALTHY"}:
                        raise AdapterError("Ansible execution endpoint health is not PASS")

            records = self.fetch_validated_inventory()
            if not records:
                raise AdapterError("Ansible health check returned no validated inventory")

            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "phase": str(phase),
                "records": len(records),
                "check": "dummy_inventory_pull",
                "backend": "endpoint" if self._execution_endpoint else "runner",
            }
            self._record_operation_status(
                operation="health_check",
                status="PASS",
                mode_used=self.mode,
                details={"records": len(records), "phase": str(phase)},
            )
            return status
        except Exception as exc:
            self._record_operation_status(
                operation="health_check",
                status="FAIL",
                mode_used=self.mode,
                details={"error": str(exc), "phase": str(phase)},
            )
            raise

    def register(self) -> Dict[str, Any]:
        return super().register()

    def shutdown(self) -> Dict[str, Any]:
        return super().shutdown()
