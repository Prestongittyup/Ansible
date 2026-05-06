"""LogicMonitor ingestion client for Sprint 1.

This module is intentionally scoped to discovery ingestion only.
No database writes, Ansible integration, or control-plane behavior is included.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class LogicMonitorConfig:
    """LogicMonitor API configuration with placeholder defaults."""

    base_url: str = "https://<LM_INSTANCE>/api/v3"
    account: str = "<ACCOUNT_ID>"
    token: str = "<TOKEN>"


@dataclass(frozen=True)
class FetchResult:
    """Result payload for paginated fetch operations."""

    devices: List[Dict[str, Any]]
    pages_fetched: int
    total_records: int
    retries_performed: int


class LogicMonitorClient:
    """Paginated LogicMonitor device client with deterministic retry behavior."""

    def __init__(
        self,
        config: LogicMonitorConfig,
        *,
        timeout_seconds: int = 20,
        page_limit: int = 500,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        simulate: bool = True,
        simulated_device_total: int = 2500,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._page_limit = page_limit
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._simulate = simulate
        self._simulated_device_total = simulated_device_total
        self._logger = logger or logging.getLogger(__name__)
        self._retry_counter = 0

    def fetch_devices(self) -> FetchResult:
        """Fetch devices from LogicMonitor using offset/limit pagination."""
        offset = 0
        pages_fetched = 0
        devices: List[Dict[str, Any]] = []
        expected_total: Optional[int] = None

        while True:
            payload = self._fetch_page_with_retry(offset=offset, limit=self._page_limit)
            items = self._extract_items(payload)
            page_total = self._extract_total(payload, offset=offset, item_count=len(items))

            if expected_total is None:
                expected_total = page_total

            if not items:
                break

            devices.extend(items)
            pages_fetched += 1
            offset += len(items)

            if offset >= page_total:
                break

        total_records = expected_total if expected_total is not None else len(devices)
        self._log_event(
            "logicmonitor_fetch_complete",
            {
                "pages_fetched": pages_fetched,
                "total_records": total_records,
                "returned_records": len(devices),
                "retries_performed": self._retry_counter,
                "simulate": self._simulate,
            },
        )

        return FetchResult(
            devices=devices,
            pages_fetched=pages_fetched,
            total_records=total_records,
            retries_performed=self._retry_counter,
        )

    def _fetch_page_with_retry(self, *, offset: int, limit: int) -> Mapping[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                if self._simulate:
                    return self._fetch_page_simulated(offset=offset, limit=limit)
                return self._fetch_page_live(offset=offset, limit=limit)
            except Exception as exc:
                if attempt >= self._max_retries:
                    raise RuntimeError("LogicMonitor page fetch failed after retries") from exc
                self._retry_counter += 1
                sleep_seconds = self._backoff_base_seconds * (2 ** attempt)
                self._log_event(
                    "logicmonitor_fetch_retry",
                    {
                        "offset": offset,
                        "limit": limit,
                        "attempt": attempt + 1,
                        "sleep_seconds": sleep_seconds,
                        "error": str(exc),
                    },
                )
                time.sleep(sleep_seconds)

        raise RuntimeError("Unexpected retry loop termination")

    def _fetch_page_live(self, *, offset: int, limit: int) -> Mapping[str, Any]:
        query = urllib.parse.urlencode({"offset": offset, "size": limit})
        endpoint = f"{self._config.base_url}/device/devices?{query}"
        request = urllib.request.Request(
            endpoint,
            headers={
                "Accept": "application/json",
                "X-Account": self._config.account,
                "Authorization": f"Bearer {self._config.token}",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("LogicMonitor live request failed") from exc

    def _fetch_page_simulated(self, *, offset: int, limit: int) -> Mapping[str, Any]:
        total = self._simulated_device_total
        start = offset
        end = min(offset + limit, total)
        items = [self._build_simulated_device(index) for index in range(start, end)]
        return {
            "items": items,
            "total": total,
        }

    @staticmethod
    def _extract_items(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        candidates = payload.get("items")
        if isinstance(candidates, list):
            return [item for item in candidates if isinstance(item, dict)]

        fallback = payload.get("data")
        if isinstance(fallback, list):
            return [item for item in fallback if isinstance(item, dict)]

        return []

    @staticmethod
    def _extract_total(payload: Mapping[str, Any], *, offset: int, item_count: int) -> int:
        value = payload.get("total")
        if isinstance(value, int) and value >= 0:
            return value
        return offset + item_count

    def _build_simulated_device(self, index: int) -> Dict[str, Any]:
        vendors = [
            ("ArubaOS-CX 10.12", ".1.3.6.1.4.1.14823"),
            ("Cisco IOS XE 17.9", ".1.3.6.1.4.1.9"),
            ("Fortinet FortiGate 7.2", ".1.3.6.1.4.1.12356"),
            ("Juniper Junos 22.4", ".1.3.6.1.4.1.2636"),
            ("Meraki MX Appliance", ".1.3.6.1.4.1.29671"),
        ]
        descr, object_id = vendors[index % len(vendors)]

        ip_value: Optional[str]
        if index % 17 == 0:
            ip_value = None
        else:
            second = ((index // 400) % 250) + 1
            third = ((index // 200) % 250) + 1
            fourth = (index % 200) + 1
            ip_value = f"10.{second}.{third}.{fourth}"

        return {
            "id": str(100000 + index),
            "displayName": f"device-{index:05d}",
            "ip": ip_value,
            "systemProperties": {
                "system.sysDescr": descr,
                "system.sysObjectID": object_id,
            },
        }

    def _log_event(self, event: str, payload: Mapping[str, Any]) -> None:
        message = {
            "event": event,
            "stage": "ingestion",
            "component": "logicmonitor_client",
            **payload,
        }
        self._logger.info(json.dumps(message, sort_keys=True))
