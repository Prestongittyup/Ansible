from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from config.env_loader import AppConfig
from core.connection_manager import ConnectionManager
from core.credential_provider import CredentialProvider
from core.external_client_registry import ExternalClientError, ExternalClientRegistry
from operations.failure_classifier import classify_failure


@dataclass(frozen=True)
class IntegrationRuntimeResult:
    integration_status: Dict[str, Dict[str, Any]]
    external_dependencies_ready: bool
    live_execution_safe: bool


class IntegrationRuntimeEvaluator:
    def __init__(self) -> None:
        self._connections = ConnectionManager(timeout_seconds=5)

    def evaluate(
        self,
        *,
        app_config: AppConfig,
        mode: str,
        phase: str,
        active_adapters: List[str],
    ) -> IntegrationRuntimeResult:
        mode_token = str(mode).strip().upper()
        credential_checks = CredentialProvider(app_config).evaluate(
            mode=mode_token,
            active_adapters=active_adapters,
        )
        registry = ExternalClientRegistry.from_app_config(app_config)

        integration_status: Dict[str, Dict[str, Any]] = {}
        for adapter_name in active_adapters:
            credential_check = credential_checks[adapter_name]
            if mode_token == "MOCK":
                integration_status[adapter_name] = self._finalize_status(
                    adapter_name=adapter_name,
                    phase=str(phase),
                    payload={
                    "status": "SKIPPED",
                    "mode": mode_token,
                    "operation": "bootstrap_integration_probe",
                    "reason": "mock_mode",
                    "credentials": {
                        "status": credential_check.status,
                        "missing": list(credential_check.missing),
                        "present": list(credential_check.present),
                    },
                    },
                )
                continue

            if credential_check.status != "PASS":
                integration_status[adapter_name] = self._finalize_status(
                    adapter_name=adapter_name,
                    phase=str(phase),
                    payload={
                    "status": "FAIL",
                    "mode": mode_token,
                    "operation": "credential_validation",
                    "reason": "missing_credentials",
                    "credentials": {
                        "status": credential_check.status,
                        "missing": list(credential_check.missing),
                        "present": list(credential_check.present),
                    },
                    },
                )
                continue

            if adapter_name == "LogicMonitorAdapter":
                integration_status[adapter_name] = self._evaluate_logicmonitor(registry)
            elif adapter_name == "AnsibleAdapter":
                integration_status[adapter_name] = self._evaluate_ansible(app_config, registry)
            elif adapter_name == "NautobotAdapter":
                integration_status[adapter_name] = self._evaluate_nautobot(registry)
            elif adapter_name == "PostgresAdapter":
                integration_status[adapter_name] = self._evaluate_postgres(app_config)
            else:
                integration_status[adapter_name] = {
                    "status": "FAIL",
                    "operation": "bootstrap_integration_probe",
                    "reason": f"Unknown adapter in integration runtime: {adapter_name}",
                }

            integration_status[adapter_name]["credentials"] = {
                "status": credential_check.status,
                "missing": list(credential_check.missing),
                "present": list(credential_check.present),
            }
            integration_status[adapter_name]["mode"] = mode_token
            integration_status[adapter_name] = self._finalize_status(
                adapter_name=adapter_name,
                phase=str(phase),
                payload=integration_status[adapter_name],
            )

        external_dependencies_ready = all(
            str(status.get("status", "")).upper() in {"PASS", "SKIPPED"}
            for status in integration_status.values()
        )
        live_execution_safe = external_dependencies_ready

        return IntegrationRuntimeResult(
            integration_status=integration_status,
            external_dependencies_ready=external_dependencies_ready,
            live_execution_safe=live_execution_safe,
        )

    def _evaluate_logicmonitor(self, registry: ExternalClientRegistry) -> Dict[str, Any]:
        try:
            client = registry.get_client("LogicMonitorAdapter")
            connectivity = self._connections.probe_endpoint(endpoint=client.base_url)
            payload = client.request_json(path="/device/devices?size=1&offset=0")

            schema_ok = False
            if isinstance(payload, dict):
                if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("items"), list):
                    schema_ok = True
                if isinstance(payload.get("records"), list):
                    schema_ok = True
            elif isinstance(payload, list):
                schema_ok = True

            status = "PASS" if connectivity["status"] == "PASS" and schema_ok else "FAIL"
            reason = ""
            if status == "FAIL":
                if connectivity["status"] != "PASS":
                    reason = str(connectivity)
                elif not schema_ok:
                    reason = "LogicMonitor schema handshake failed"
            return {
                "status": status,
                "operation": "logicmonitor_bootstrap_probe",
                "dependency": "logicmonitor_api",
                "reason": reason,
                "connectivity": connectivity,
                "auth": {
                    "status": "PASS" if connectivity["status"] == "PASS" else "FAIL",
                },
                "schema_handshake": {
                    "status": "PASS" if schema_ok else "FAIL",
                },
            }
        except ExternalClientError as exc:
            return {
                "status": "FAIL",
                "operation": "logicmonitor_bootstrap_probe",
                "dependency": "logicmonitor_api",
                "reason": str(exc),
            }

    def _evaluate_ansible(self, app_config: AppConfig, registry: ExternalClientRegistry) -> Dict[str, Any]:
        if app_config.adapters.ansible_execution_endpoint:
            try:
                client = registry.get_client("AnsibleAdapter")
                connectivity = self._connections.probe_endpoint(endpoint=client.base_url)
                health_payload = client.request_json(path="/health")
                health_token = str(health_payload.get("status") or "PASS").upper() if isinstance(health_payload, dict) else "PASS"
                inventory_payload = client.request_json(path="/inventory")
                schema_ok = isinstance(inventory_payload, (dict, list))

                status = "PASS"
                if connectivity["status"] != "PASS" or health_token not in {"PASS", "OK", "HEALTHY"} or not schema_ok:
                    status = "FAIL"

                reason = ""
                if status == "FAIL":
                    if connectivity["status"] != "PASS":
                        reason = str(connectivity)
                    elif health_token not in {"PASS", "OK", "HEALTHY"}:
                        reason = "Ansible health endpoint status is not PASS"
                    elif not schema_ok:
                        reason = "Ansible inventory schema handshake failed"

                return {
                    "status": status,
                    "operation": "ansible_endpoint_bootstrap_probe",
                    "dependency": "ansible_execution_endpoint",
                    "reason": reason,
                    "connectivity": connectivity,
                    "auth": {
                        "status": "PASS" if health_token in {"PASS", "OK", "HEALTHY"} else "FAIL",
                        "health": health_payload,
                    },
                    "schema_handshake": {
                        "status": "PASS" if schema_ok else "FAIL",
                    },
                }
            except ExternalClientError as exc:
                return {
                    "status": "FAIL",
                    "operation": "ansible_endpoint_bootstrap_probe",
                    "dependency": "ansible_execution_endpoint",
                    "reason": str(exc),
                }

        runner_path = Path(str(app_config.adapters.ansible_runner_path or "")).resolve()
        if not runner_path.exists() or not runner_path.is_dir():
            return {
                "status": "FAIL",
                "operation": "ansible_runner_bootstrap_probe",
                "dependency": "ansible_runner_path",
                "reason": f"Runner path missing: {runner_path}",
            }

        return {
            "status": "PASS",
            "operation": "ansible_runner_bootstrap_probe",
            "dependency": "ansible_runner_path",
            "connectivity": {
                "status": "PASS",
                "path": str(runner_path),
            },
            "auth": {"status": "PASS"},
            "schema_handshake": {"status": "PASS"},
        }

    def _evaluate_nautobot(self, registry: ExternalClientRegistry) -> Dict[str, Any]:
        try:
            client = registry.get_client("NautobotAdapter")
            connectivity = self._connections.probe_endpoint(endpoint=client.base_url)
            status_payload = client.request_json(path="/api/status/")
            devices_payload = client.request_json(path="/api/dcim/devices/?limit=1")

            auth_ok = isinstance(status_payload, dict)
            schema_ok = isinstance(devices_payload, dict) and isinstance(devices_payload.get("results"), list)

            status = "PASS" if connectivity["status"] == "PASS" and auth_ok and schema_ok else "FAIL"
            reason = ""
            if status == "FAIL":
                if connectivity["status"] != "PASS":
                    reason = str(connectivity)
                elif not auth_ok:
                    reason = "Nautobot auth/status payload is invalid"
                elif not schema_ok:
                    reason = "Nautobot schema handshake failed"
            return {
                "status": status,
                "operation": "nautobot_bootstrap_probe",
                "dependency": "nautobot_api",
                "reason": reason,
                "connectivity": connectivity,
                "auth": {
                    "status": "PASS" if auth_ok else "FAIL",
                },
                "schema_handshake": {
                    "status": "PASS" if schema_ok else "FAIL",
                },
            }
        except ExternalClientError as exc:
            return {
                "status": "FAIL",
                "operation": "nautobot_bootstrap_probe",
                "dependency": "nautobot_api",
                "reason": str(exc),
            }

    def _evaluate_postgres(self, app_config: AppConfig) -> Dict[str, Any]:
        connectivity = {
            "status": "SKIPPED",
        }
        if app_config.adapters.postgres_host:
            dns = self._connections.dns_lookup(host=app_config.adapters.postgres_host)
            tcp = self._connections.tcp_connect(
                host=app_config.adapters.postgres_host,
                port=app_config.adapters.postgres_port,
            )
            connectivity = {
                "status": "PASS" if dns["status"] == "PASS" and tcp["status"] == "PASS" else "FAIL",
                "dns": dns,
                "tcp": tcp,
            }

        transaction = self._connections.postgres_transaction_handshake(
            connection_string=app_config.adapters.postgres_connection_string,
        )
        status = "PASS" if connectivity["status"] in {"PASS", "SKIPPED"} and transaction["status"] == "PASS" else "FAIL"

        reason = ""
        if status == "FAIL":
            if transaction["status"] != "PASS":
                reason = str(transaction.get("reason") or "Postgres transaction handshake failed")
            elif connectivity["status"] not in {"PASS", "SKIPPED"}:
                reason = str(connectivity)

        return {
            "status": status,
            "operation": "postgres_bootstrap_probe",
            "dependency": "postgres",
            "reason": reason,
            "connectivity": connectivity,
            "transaction_handshake": transaction,
            "schema_handshake": {
                "status": transaction["status"],
            },
            "auth": {
                "status": transaction["status"],
            },
        }

    def _resolve_root_cause(self, payload: Dict[str, Any]) -> str:
        reason = str(payload.get("reason") or "").strip()
        if reason:
            return reason

        transaction = payload.get("transaction_handshake")
        if isinstance(transaction, dict) and str(transaction.get("status", "")).upper() == "FAIL":
            return str(transaction.get("reason") or "Postgres transaction handshake failed")

        connectivity = payload.get("connectivity")
        if isinstance(connectivity, dict) and str(connectivity.get("status", "")).upper() == "FAIL":
            dns = connectivity.get("dns")
            tcp = connectivity.get("tcp")
            if isinstance(dns, dict) and str(dns.get("status", "")).upper() == "FAIL":
                return str(dns.get("reason") or "DNS lookup failed")
            if isinstance(tcp, dict) and str(tcp.get("status", "")).upper() == "FAIL":
                return str(tcp.get("reason") or "TCP connectivity failed")
            return "Connectivity probe failed"

        auth = payload.get("auth")
        if isinstance(auth, dict) and str(auth.get("status", "")).upper() == "FAIL":
            health = auth.get("health")
            if health is not None:
                return "Authentication/health validation failed"
            return "Authentication validation failed"

        schema_handshake = payload.get("schema_handshake")
        if isinstance(schema_handshake, dict) and str(schema_handshake.get("status", "")).upper() == "FAIL":
            return "Schema handshake failed"

        return "Integration dependency probe failed"

    def _finalize_status(self, *, adapter_name: str, phase: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        finalized = dict(payload)
        finalized["adapter"] = str(adapter_name)
        finalized["phase"] = str(phase)
        finalized.setdefault("operation", "bootstrap_integration_probe")

        status_token = str(finalized.get("status", "")).upper()
        if status_token in {"PASS", "SKIPPED"}:
            finalized["root_cause"] = ""
            finalized["failure_class"] = ""
            return finalized

        root_cause = self._resolve_root_cause(finalized)
        finalized["root_cause"] = root_cause
        finalized["failure_class"] = classify_failure(root_cause=root_cause, payload=finalized)
        finalized.setdefault("reason", root_cause)
        return finalized
