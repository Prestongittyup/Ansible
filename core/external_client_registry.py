from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict
from urllib import error as urllib_error
from urllib import request as urllib_request

from config.env_loader import AppConfig


class ExternalClientError(RuntimeError):
    """Raised when an external integration client fails."""


@dataclass(frozen=True)
class ExternalHttpClientConfig:
    name: str
    base_url: str
    headers: Dict[str, str]


class ExternalHttpClient:
    def __init__(self, config: ExternalHttpClientConfig) -> None:
        self.name = str(config.name)
        self.base_url = str(config.base_url).rstrip("/")
        self.headers = dict(config.headers)

    def request_json(
        self,
        *,
        path: str,
        method: str = "GET",
        payload: Dict[str, Any] | None = None,
        timeout_seconds: int = 20,
    ) -> Any:
        endpoint = self.base_url + path
        body = None
        headers = dict(self.headers)
        headers["Accept"] = "application/json"
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib_request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            raise ExternalClientError(f"{self.name} request failed: HTTP {exc.code}") from exc
        except urllib_error.URLError as exc:
            raise ExternalClientError(f"{self.name} request failed: {exc}") from exc

        if not raw.strip():
            return {}

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExternalClientError(f"{self.name} returned non-JSON response") from exc


def _resolve_logicmonitor_base_url(*, account: str, base_url: str) -> str:
    account_value = str(account or "").strip()
    base_url_value = str(base_url or "").strip()
    if base_url_value:
        return base_url_value.rstrip("/")
    if account_value:
        return f"https://{account_value}.logicmonitor.com/santaba/rest"
    return ""


class ExternalClientRegistry:
    def __init__(self, clients: Dict[str, ExternalHttpClient]) -> None:
        self._clients = dict(clients)

    @classmethod
    def from_app_config(cls, app_config: AppConfig) -> "ExternalClientRegistry":
        clients: Dict[str, ExternalHttpClient] = {}

        logicmonitor_url = _resolve_logicmonitor_base_url(
            account=app_config.adapters.logicmonitor_account,
            base_url=app_config.adapters.logicmonitor_base_url,
        )
        if logicmonitor_url:
            clients["LogicMonitorAdapter"] = ExternalHttpClient(
                ExternalHttpClientConfig(
                    name="LogicMonitorAdapter",
                    base_url=logicmonitor_url,
                    headers={
                        "Authorization": f"Bearer {app_config.adapters.logicmonitor_api_key}",
                    },
                )
            )

        if app_config.adapters.ansible_execution_endpoint:
            clients["AnsibleAdapter"] = ExternalHttpClient(
                ExternalHttpClientConfig(
                    name="AnsibleAdapter",
                    base_url=app_config.adapters.ansible_execution_endpoint,
                    headers={},
                )
            )

        if app_config.adapters.nautobot_base_url:
            clients["NautobotAdapter"] = ExternalHttpClient(
                ExternalHttpClientConfig(
                    name="NautobotAdapter",
                    base_url=app_config.adapters.nautobot_base_url,
                    headers={
                        "Authorization": f"Token {app_config.adapters.nautobot_token}",
                    },
                )
            )

        return cls(clients)

    def has_client(self, adapter_name: str) -> bool:
        return adapter_name in self._clients

    def get_client(self, adapter_name: str) -> ExternalHttpClient:
        client = self._clients.get(adapter_name)
        if client is None:
            raise ExternalClientError(f"Client not registered: {adapter_name}")
        return client
