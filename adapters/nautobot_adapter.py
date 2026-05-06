from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from urllib import request as urllib_request

from adapters.base import AdapterBase, AdapterError, AdapterRuntimeConfig
from kernel.phase_resolver import PHASE_3
from observability.logger import classify_error, log_event
from runtime.mock import fixtures


@dataclass(frozen=True)
class NautobotConfig:
    mode: str
    token: str
    base_url: str = ""
    desired_state_path: str = ""


class NautobotAdapter(AdapterBase):
    def __init__(self, config: NautobotConfig) -> None:
        super().__init__(AdapterRuntimeConfig(mode=config.mode, name="NautobotAdapter"))
        self._token = str(config.token or "")
        self._base_url = str(config.base_url or "").strip()
        self._desired_state_path = str(config.desired_state_path or "").strip()

    def _request_json(self, *, endpoint: str, method: str = "GET", payload: Dict[str, Any] | None = None) -> Any:
        if not self._base_url:
            raise AdapterError("NAUTOBOT_BASE_URL is not configured")
        if not self._token:
            raise AdapterError("NAUTOBOT_TOKEN is not configured")

        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="nautobot",
            operation=f"{method.upper()} {endpoint}",
            status="START",
            external_system="nautobot",
        )

        body = None
        headers = {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib_request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                http_status = int(getattr(response, "status", 200) or 200)
        except urllib_error.URLError as exc:
            classified = classify_error(exc)
            latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
            log_event(
                level="ERROR",
                component="adapter",
                adapter="nautobot",
                operation=f"{method.upper()} {endpoint}",
                status="FAIL",
                duration_ms=latency_ms,
                latency_ms=latency_ms,
                external_system="nautobot",
                **classified,
            )
            raise AdapterError(f"Nautobot API request failed: {exc}") from exc
        except Exception as exc:
            classified = classify_error(exc)
            latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
            log_event(
                level="ERROR",
                component="adapter",
                adapter="nautobot",
                operation=f"{method.upper()} {endpoint}",
                status="FAIL",
                duration_ms=latency_ms,
                latency_ms=latency_ms,
                external_system="nautobot",
                **classified,
            )
            raise AdapterError(f"Nautobot API request failed: {exc}") from exc

        latency_ms = int(round((perf_counter() - started) * 1000.0, 0))
        log_event(
            level="INFO",
            component="adapter",
            adapter="nautobot",
            operation=f"{method.upper()} {endpoint}",
            status="SUCCESS",
            duration_ms=latency_ms,
            latency_ms=latency_ms,
            http_status=http_status,
            external_system="nautobot",
        )

        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdapterError("Nautobot API returned invalid JSON") from exc

    def _fetch_paginated(self, *, resource_path: str) -> List[Dict[str, Any]]:
        page_size = 200
        offset = 0
        results: List[Dict[str, Any]] = []

        while True:
            params = urllib_parse.urlencode({"limit": page_size, "offset": offset})
            endpoint = self._base_url.rstrip("/") + resource_path + "?" + params
            payload = self._request_json(endpoint=endpoint)

            page_items: List[Dict[str, Any]] = []
            if isinstance(payload, dict):
                for key in ("results", "items", "records"):
                    candidate = payload.get(key)
                    if isinstance(candidate, list):
                        page_items = [item for item in candidate if isinstance(item, dict)]
                        break
            elif isinstance(payload, list):
                page_items = [item for item in payload if isinstance(item, dict)]

            if not page_items:
                break

            results.extend(page_items)
            if len(page_items) < page_size:
                break
            offset += page_size

        return results

    def _normalize_authoritative_records(self, payload: Any) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("records", "devices", "results", "items"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    records = [item for item in candidate if isinstance(item, dict)]
                    break

        if not records:
            raise AdapterError("Nautobot live payload does not contain records")

        normalized: List[Dict[str, Any]] = []
        for row in records:
            normalized.append(
                {
                    "ip_address": str(row.get("ip_address") or row.get("primary_ip") or "").strip(),
                    "hostname": str(row.get("hostname") or row.get("name") or "").strip(),
                    "serial_number": str(row.get("serial_number") or row.get("serial") or "").strip(),
                    "vendor": str(row.get("vendor") or "").strip(),
                    "model": str(row.get("model") or "").strip(),
                    "site": str(row.get("site") or "").strip(),
                    "status": str(row.get("status") or "unknown").strip(),
                    "authority_owner": "NAUTOBOT",
                    "nautobot_authoritative": True,
                    "sst_source": "NAUTOBOT",
                }
            )

        normalized.sort(key=lambda item: item.get("ip_address", ""))
        return self._validate_records(normalized, source="live_normalized")

    def _load_live_payload_from_file(self) -> Any:
        if not self._desired_state_path:
            raise AdapterError("NAUTOBOT_DESIRED_STATE_PATH is not configured")
        source_path = Path(self._desired_state_path)
        if not source_path.exists() or not source_path.is_file():
            raise AdapterError(f"Nautobot desired-state file missing: {source_path}")
        return json.loads(source_path.read_text(encoding="utf-8"))

    def _load_live_payload_from_api(self) -> Any:
        devices = self._fetch_paginated(resource_path="/api/dcim/devices/")
        sites = self._fetch_paginated(resource_path="/api/dcim/sites/")
        interfaces = self._fetch_paginated(resource_path="/api/dcim/interfaces/")
        return {
            "records": devices,
            "sites": sites,
            "interfaces": interfaces,
        }

    def _mock_fetch_authoritative_inventory(self) -> List[Dict[str, Any]]:
        records = fixtures.nautobot_authoritative_records_for_phase3()
        return self._validate_records(records, source="mock")

    def _live_fetch_authoritative_inventory(self) -> List[Dict[str, Any]]:
        if self.mode == "LIVE":
            payload = self._load_live_payload_from_api()
        else:
            payload = self._load_live_payload_from_file() if self._desired_state_path else self._load_live_payload_from_api()
        return self._normalize_authoritative_records(payload)

    def _mock_write_authoritative_inventory(self, validated: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "status": "PASS",
            "records_written": len(validated),
            "write_mode": "MOCK",
        }

    def _live_write_authoritative_inventory(
        self,
        *,
        validated: List[Dict[str, Any]],
        phase: str,
        governance_approved: bool,
        bootstrap_ready: bool,
        sst_owner: str,
    ) -> Dict[str, Any]:
        if str(phase).strip().upper() != PHASE_3:
            raise AdapterError("Nautobot writes are only allowed in PHASE_3")
        if str(sst_owner).strip().upper() != "NAUTOBOT":
            raise AdapterError("Nautobot writes require SST authority NAUTOBOT")
        if not bootstrap_ready:
            raise AdapterError("Nautobot writes require valid bootstrap READY state")
        if not governance_approved:
            raise AdapterError("Governance approval required for Nautobot writes")

        if self._desired_state_path:
            raise AdapterError("NAUTOBOT_DESIRED_STATE_PATH is not allowed in LIVE mode")

        endpoint = self._base_url.rstrip("/") + "/api/dcim/devices/sync/"
        self._request_json(
            endpoint=endpoint,
            method="POST",
            payload={"records": validated},
        )

        return {
            "status": "PASS",
            "records_written": len(validated),
            "write_mode": "API",
            "target": endpoint,
        }

    def fetch_authoritative_inventory(self) -> List[Dict[str, Any]]:
        return self._run_mode_operation(
            operation="fetch_authoritative_inventory",
            live_callable=self._live_fetch_authoritative_inventory,
            mock_callable=self._mock_fetch_authoritative_inventory,
        )

    def write_authoritative_inventory(
        self,
        records: List[Dict[str, Any]],
        *,
        phase: str,
        governance_approved: bool,
        bootstrap_ready: bool = False,
        sst_owner: str = "",
    ) -> Dict[str, Any]:
        validated = self._validate_records(records, source="write_input")
        return self._run_mode_operation(
            operation="write_authoritative_inventory",
            live_callable=lambda: self._live_write_authoritative_inventory(
                validated=validated,
                phase=phase,
                governance_approved=governance_approved,
                bootstrap_ready=bootstrap_ready,
                sst_owner=sst_owner,
            ),
            mock_callable=lambda: self._mock_write_authoritative_inventory(validated),
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
        if not self._token:
            missing.append("NAUTOBOT_TOKEN|NAUTOBOT_API_TOKEN")
        if not self._base_url:
            missing.append("NAUTOBOT_URL|NAUTOBOT_BASE_URL")

        if missing and self.mode == "LIVE":
            self._record_operation_status(
                operation="validate_credentials",
                status="FAIL",
                mode_used=self.mode,
                details={"missing": missing},
            )
            raise AdapterError("Nautobot missing credentials: " + ", ".join(missing))

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
            if self.mode == "LIVE" and self._desired_state_path:
                raise AdapterError("NAUTOBOT_DESIRED_STATE_PATH is not allowed in LIVE mode")

            if self.mode in {"LIVE", "HYBRID"} and self._desired_state_path:
                source_path = Path(self._desired_state_path)
                if not source_path.exists() or not source_path.is_file():
                    raise AdapterError(f"Nautobot desired-state file missing: {source_path}")

            if self.mode in {"LIVE", "HYBRID"}:
                endpoint = self._base_url.rstrip("/") + "/api/status/"
                self._request_json(endpoint=endpoint)

            base_status = super().init()
            base_status["init_mode"] = "LIVE_FILE" if self._desired_state_path else "LIVE_API"
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
        phase_token = str(phase).strip().upper()
        if phase_token and phase_token != PHASE_3:
            status = {
                "status": "SKIPPED",
                "adapter": self.name,
                "mode": self.mode,
                "phase": phase_token,
                "check": "phase3_reachability",
                "message": "Nautobot health checks only run in PHASE_3",
            }
            self._record_operation_status(
                operation="health_check",
                status="PASS",
                mode_used=self.mode,
                details={"phase": phase_token, "skipped": True},
            )
            return status

        try:
            records = self.fetch_authoritative_inventory()
            if not records:
                raise AdapterError("Nautobot health check returned no authoritative records")

            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "phase": phase_token,
                "records": len(records),
                "check": "phase3_api_reachability",
            }
            self._record_operation_status(
                operation="health_check",
                status="PASS",
                mode_used=self.mode,
                details={"records": len(records), "phase": phase_token},
            )
            return status
        except Exception as exc:
            self._record_operation_status(
                operation="health_check",
                status="FAIL",
                mode_used=self.mode,
                details={"error": str(exc), "phase": phase_token},
            )
            raise

    def register(self) -> Dict[str, Any]:
        return super().register()

    def shutdown(self) -> Dict[str, Any]:
        return super().shutdown()
