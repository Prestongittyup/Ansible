from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from config.env_loader import AppConfig


@dataclass(frozen=True)
class CredentialCheck:
    adapter: str
    status: str
    missing: List[str]
    present: List[str]


class CredentialProvider:
    def __init__(self, app_config: AppConfig) -> None:
        self._config = app_config

    def evaluate(self, *, mode: str, active_adapters: List[str]) -> Dict[str, CredentialCheck]:
        mode_token = str(mode).strip().upper()
        results: Dict[str, CredentialCheck] = {}

        for adapter in active_adapters:
            if mode_token == "MOCK":
                results[adapter] = CredentialCheck(
                    adapter=adapter,
                    status="SKIPPED",
                    missing=[],
                    present=[],
                )
                continue

            missing: List[str] = []
            present: List[str] = []

            if adapter == "LogicMonitorAdapter":
                self._check_field("LOGICMONITOR_API_KEY", self._config.adapters.logicmonitor_api_key, missing, present)
                self._check_field("LOGICMONITOR_ACCOUNT", self._config.adapters.logicmonitor_account, missing, present)

            elif adapter == "AnsibleAdapter":
                endpoint = str(self._config.adapters.ansible_execution_endpoint or "").strip()
                runner_path = str(self._config.adapters.ansible_runner_path or "").strip()
                if endpoint:
                    present.append("ANSIBLE_EXECUTION_ENDPOINT")
                if runner_path:
                    present.append("ANSIBLE_RUNNER_PATH")
                if not endpoint and not runner_path:
                    missing.append("ANSIBLE_EXECUTION_ENDPOINT|ANSIBLE_RUNNER_PATH")

            elif adapter == "NautobotAdapter":
                self._check_field("NAUTOBOT_TOKEN", self._config.adapters.nautobot_token, missing, present)
                self._check_field("NAUTOBOT_URL", self._config.adapters.nautobot_base_url, missing, present)

            elif adapter == "PostgresAdapter":
                self._check_field(
                    "POSTGRES_CONNECTION_STRING",
                    self._config.adapters.postgres_connection_string,
                    missing,
                    present,
                )
                self._check_field("POSTGRES_DB", self._config.adapters.postgres_db, missing, present)
                self._check_field("POSTGRES_USER", self._config.adapters.postgres_user, missing, present)
                self._check_field("POSTGRES_PASSWORD", self._config.adapters.postgres_password, missing, present)

            status = "PASS" if not missing else "FAIL"
            results[adapter] = CredentialCheck(
                adapter=adapter,
                status=status,
                missing=missing,
                present=present,
            )

        return results

    @staticmethod
    def _check_field(name: str, value: str, missing: List[str], present: List[str]) -> None:
        if str(value or "").strip():
            present.append(name)
            return
        missing.append(name)
