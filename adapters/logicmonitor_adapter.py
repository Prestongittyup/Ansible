from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Dict, List
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from urllib import request as urllib_request

from adapters.base import AdapterBase, AdapterError, AdapterRuntimeConfig
from observability.logger import classify_error, log_event
from runtime.mock import fixtures


@dataclass(frozen=True)
class LogicMonitorConfig:
    mode: str
    api_key: str
    account: str = ""
    base_url: str = ""
    live_payload_path: str = ""


class LogicMonitorAdapter(AdapterBase):
    def __init__(self, config: LogicMonitorConfig) -> None:
        super().__init__(AdapterRuntimeConfig(mode=config.mode, name="LogicMonitorAdapter"))
        self._api_key = str(config.api_key or "")
        self._account = str(config.account or "").strip()
        self._base_url = str(config.base_url or "").strip()
        self._live_payload_path = str(config.live_payload_path or "").strip()

    def _resolve_live_base_url(self) -> str:
        if self._base_url:
            return self._base_url.rstrip("/")
        if self._account:
            return f"https://{self._account}.logicmonitor.com/santaba/rest"
        raise AdapterError("LOGICMONITOR_BASE_URL is not configured")

    def _live_api_get_json(self, endpoint: str, *, max_retries: int = 3) -> Any:
        if not self._api_key:
            raise AdapterError("LOGICMONITOR_API_KEY is not configured")

        request_started = time.perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="logicmonitor",
            operation=f"GET {endpoint}",
            status="START",
            external_system="logicmonitor",
        )

        for attempt in range(max_retries + 1):
            request = urllib_request.Request(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            try:
                with urllib_request.urlopen(request, timeout=20) as response:
                    body = response.read().decode("utf-8")
                    http_status = int(getattr(response, "status", 200) or 200)

                latency_ms = int(round((time.perf_counter() - request_started) * 1000.0, 0))
                log_event(
                    level="INFO",
                    component="adapter",
                    adapter="logicmonitor",
                    operation=f"GET {endpoint}",
                    status="SUCCESS",
                    duration_ms=latency_ms,
                    latency_ms=latency_ms,
                    http_status=http_status,
                    external_system="logicmonitor",
                )
                return json.loads(body)
            except urllib_error.HTTPError as exc:
                classified = classify_error(message=f"HTTP {exc.code}", http_status=int(exc.code))
                latency_ms = int(round((time.perf_counter() - request_started) * 1000.0, 0))
                log_event(
                    level="WARN" if exc.code == 429 and attempt < max_retries else "ERROR",
                    component="adapter",
                    adapter="logicmonitor",
                    operation=f"GET {endpoint}",
                    status="FAIL",
                    duration_ms=latency_ms,
                    latency_ms=latency_ms,
                    http_status=int(exc.code),
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    external_system="logicmonitor",
                    **classified,
                )
                if exc.code == 429 and attempt < max_retries:
                    retry_after = str(exc.headers.get("Retry-After") or "1").strip()
                    try:
                        delay = max(1, int(retry_after))
                    except ValueError:
                        delay = 1
                    time.sleep(delay)
                    continue
                raise AdapterError(f"LogicMonitor API request failed: HTTP {exc.code}") from exc
            except urllib_error.URLError as exc:
                classified = classify_error(exc)
                latency_ms = int(round((time.perf_counter() - request_started) * 1000.0, 0))
                log_event(
                    level="WARN" if attempt < max_retries else "ERROR",
                    component="adapter",
                    adapter="logicmonitor",
                    operation=f"GET {endpoint}",
                    status="FAIL",
                    duration_ms=latency_ms,
                    latency_ms=latency_ms,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    external_system="logicmonitor",
                    **classified,
                )
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                raise AdapterError(f"LogicMonitor API request failed: {exc}") from exc
            except Exception as exc:
                classified = classify_error(exc)
                latency_ms = int(round((time.perf_counter() - request_started) * 1000.0, 0))
                log_event(
                    level="WARN" if attempt < max_retries else "ERROR",
                    component="adapter",
                    adapter="logicmonitor",
                    operation=f"GET {endpoint}",
                    status="FAIL",
                    duration_ms=latency_ms,
                    latency_ms=latency_ms,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    external_system="logicmonitor",
                    **classified,
                )
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                raise AdapterError(f"LogicMonitor API request failed: {exc}") from exc

        raise AdapterError("LogicMonitor API request exhausted retry budget")

    def _normalize_live_records(self, payload: Any) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("records", "devices", "items"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    records = [item for item in candidate if isinstance(item, dict)]
                    break

        if not records:
            raise AdapterError("LogicMonitor live payload does not contain records")

        normalized: List[Dict[str, Any]] = []
        for row in records:
            normalized.append(
                {
                    "ip_address": str(row.get("ip_address") or row.get("ip") or "").strip(),
                    "hostname": str(row.get("hostname") or row.get("display_name") or "").strip(),
                    "serial_number": str(row.get("serial_number") or row.get("serial") or "").strip(),
                    "vendor": str(row.get("vendor") or "").strip(),
                    "model": str(row.get("model") or "").strip(),
                    "site": str(row.get("site") or "").strip(),
                    "status": str(row.get("status") or "unknown").strip(),
                    "authority_owner": "LOGICMONITOR",
                    "sst_source": "LOGICMONITOR",
                }
            )

        normalized.sort(key=lambda item: item.get("ip_address", ""))
        return self._validate_records(normalized, source="live_normalized")

    def _load_live_payload_from_file(self) -> Any:
        if not self._live_payload_path:
            raise AdapterError("LOGICMONITOR_LIVE_PAYLOAD_PATH is not configured")

        payload_path = Path(self._live_payload_path)
        if not payload_path.exists() or not payload_path.is_file():
            raise AdapterError(f"LogicMonitor live payload file missing: {payload_path}")
        return json.loads(payload_path.read_text(encoding="utf-8"))

    def _load_live_payload_from_api(self) -> Any:
        base_url = self._resolve_live_base_url()
        page_size = 200
        offset = 0
        page_count = 0
        max_pages = 1000
        all_records: List[Dict[str, Any]] = []

        while True:
            page_count += 1
            if page_count > max_pages:
                raise AdapterError("LogicMonitor pagination limit exceeded")

            params = urllib_parse.urlencode({"offset": offset, "size": page_size})
            endpoint = base_url + "/device/devices?" + params
            payload = self._live_api_get_json(endpoint)

            page_items: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                page_items = [item for item in payload if isinstance(item, dict)]
            elif isinstance(payload, dict):
                data_section = payload.get("data")
                if isinstance(data_section, dict):
                    items = data_section.get("items")
                    if isinstance(items, list):
                        page_items = [item for item in items if isinstance(item, dict)]
                if not page_items:
                    for key in ("records", "devices", "items"):
                        candidate = payload.get(key)
                        if isinstance(candidate, list):
                            page_items = [item for item in candidate if isinstance(item, dict)]
                            break

            if not page_items:
                break

            all_records.extend(page_items)
            if len(page_items) < page_size:
                break
            offset += page_size

        return {"records": all_records}

    def _mock_fetch_discovery_records(self) -> List[Dict[str, Any]]:
        records = fixtures.logicmonitor_discovery_records()
        return self._validate_records(records, source="mock")

    def _live_fetch_discovery_records(self) -> List[Dict[str, Any]]:
        if self.mode == "LIVE":
            payload = self._load_live_payload_from_api()
        else:
            payload = self._load_live_payload_from_file() if self._live_payload_path else self._load_live_payload_from_api()
        return self._normalize_live_records(payload)

    def fetch_discovery_records(self) -> List[Dict[str, Any]]:
        return self._run_mode_operation(
            operation="fetch_discovery_records",
            live_callable=self._live_fetch_discovery_records,
            mock_callable=self._mock_fetch_discovery_records,
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
        if not self._api_key:
            missing.append("LOGICMONITOR_API_KEY")
        if not self._account:
            missing.append("LOGICMONITOR_ACCOUNT")

        if missing and self.mode == "LIVE":
            self._record_operation_status(
                operation="validate_credentials",
                status="FAIL",
                mode_used=self.mode,
                details={"missing": missing},
            )
            raise AdapterError("LogicMonitor missing credentials: " + ", ".join(missing))

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
            if self.mode == "LIVE" and self._live_payload_path:
                raise AdapterError("LOGICMONITOR_LIVE_PAYLOAD_PATH is not allowed in LIVE mode")

            if self.mode in {"LIVE", "HYBRID"} and self._live_payload_path:
                payload_path = Path(self._live_payload_path)
                if not payload_path.exists() or not payload_path.is_file():
                    raise AdapterError(f"LogicMonitor live payload file missing: {payload_path}")

            base_status = super().init()
            base_status["init_mode"] = "LIVE_PAYLOAD" if self._live_payload_path else "LIVE_API"
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
            records = self.fetch_discovery_records()
            if not records:
                raise AdapterError("LogicMonitor health check returned no records")

            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "phase": str(phase),
                "records": len(records),
                "check": "dummy_inventory_pull",
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
