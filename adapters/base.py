from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Dict, List

from observability.logger import classify_error, log_event


class AdapterError(RuntimeError):
    """Adapter contract violation or runtime integration error."""


ALLOWED_MODES = {"MOCK", "LIVE", "HYBRID"}


def normalize_mode(mode: str) -> str:
    token = str(mode).strip().upper()
    if token not in ALLOWED_MODES:
        raise AdapterError(f"Invalid adapter mode: {mode}")
    return token


@dataclass(frozen=True)
class AdapterRuntimeConfig:
    mode: str
    name: str


class AdapterBase:
    def __init__(self, config: AdapterRuntimeConfig) -> None:
        self.config = AdapterRuntimeConfig(mode=normalize_mode(config.mode), name=config.name)
        self._operation_status: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
        self._registered = False

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def name(self) -> str:
        return self.config.name

    def _ensure_live_supported(self) -> None:
        raise AdapterError(f"{self.name} live mode is not implemented")

    def _record_operation_status(
        self,
        *,
        operation: str,
        status: str,
        mode_used: str,
        details: Dict[str, Any] | None = None,
    ) -> None:
        self._operation_status[str(operation)] = {
            "status": str(status),
            "mode_used": str(mode_used),
            "details": dict(details or {}),
        }

    def status_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._operation_status.items()}

    def lifecycle_snapshot(self) -> Dict[str, Any]:
        return {
            "initialized": bool(self._initialized),
            "registered": bool(self._registered),
        }

    def validate_credentials(self) -> Dict[str, Any]:
        status = {
            "status": "PASS",
            "adapter": self.name,
            "mode": self.mode,
            "message": "credentials validated",
        }
        self._record_operation_status(
            operation="validate_credentials",
            status="PASS",
            mode_used=self.mode,
            details={"message": status["message"]},
        )
        return status

    def init(self) -> Dict[str, Any]:
        self._initialized = True
        status = {
            "status": "PASS",
            "adapter": self.name,
            "mode": self.mode,
            "message": "initialized",
        }
        self._record_operation_status(
            operation="init",
            status="PASS",
            mode_used=self.mode,
            details={"message": status["message"]},
        )
        return status

    def health_check(self, *, phase: str = "") -> Dict[str, Any]:
        status = {
            "status": "PASS",
            "adapter": self.name,
            "mode": self.mode,
            "phase": str(phase),
            "message": "healthy",
        }
        self._record_operation_status(
            operation="health_check",
            status="PASS",
            mode_used=self.mode,
            details={"phase": str(phase), "message": status["message"]},
        )
        return status

    def register(self) -> Dict[str, Any]:
        if not self._initialized:
            self._record_operation_status(
                operation="register",
                status="FAIL",
                mode_used=self.mode,
                details={"error": "ADAPTER_NOT_INITIALIZED"},
            )
            raise AdapterError(f"{self.name} cannot register before initialization")

        self._registered = True
        status = {
            "status": "PASS",
            "adapter": self.name,
            "mode": self.mode,
            "message": "registered",
        }
        self._record_operation_status(
            operation="register",
            status="PASS",
            mode_used=self.mode,
            details={"message": status["message"]},
        )
        return status

    def shutdown(self) -> Dict[str, Any]:
        self._initialized = False
        self._registered = False
        status = {
            "status": "PASS",
            "adapter": self.name,
            "mode": self.mode,
            "message": "shutdown",
        }
        self._record_operation_status(
            operation="shutdown",
            status="PASS",
            mode_used=self.mode,
            details={"message": status["message"]},
        )
        return status

    def _run_mode_operation(
        self,
        *,
        operation: str,
        live_callable: Callable[[], Any] | None,
        mock_callable: Callable[[], Any],
    ) -> Any:
        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            operation=str(operation),
            status="START",
            adapter=self.name,
            adapter_mode=self.mode,
        )

        if self.mode == "MOCK":
            result = mock_callable()
            self._record_operation_status(
                operation=operation,
                status="PASS",
                mode_used="MOCK",
                details={"fallback": False},
            )
            log_event(
                level="INFO",
                component="adapter",
                operation=str(operation),
                status="SUCCESS",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                adapter=self.name,
                adapter_mode=self.mode,
                mode_used="MOCK",
                fallback=False,
            )
            return result

        if self.mode == "LIVE":
            if live_callable is None:
                self._record_operation_status(
                    operation=operation,
                    status="FAIL",
                    mode_used="LIVE",
                    details={"error": "LIVE_HANDLER_NOT_DEFINED"},
                )
                log_event(
                    level="ERROR",
                    component="adapter",
                    operation=str(operation),
                    status="FAIL",
                    duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                    adapter=self.name,
                    adapter_mode=self.mode,
                    mode_used="LIVE",
                    **classify_error(message="LIVE_HANDLER_NOT_DEFINED"),
                )
                raise AdapterError(f"{self.name} live handler not defined for operation={operation}")

            try:
                result = live_callable()
            except Exception as exc:
                self._record_operation_status(
                    operation=operation,
                    status="FAIL",
                    mode_used="LIVE",
                    details={"error": str(exc)},
                )
                log_event(
                    level="ERROR",
                    component="adapter",
                    operation=str(operation),
                    status="FAIL",
                    duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                    adapter=self.name,
                    adapter_mode=self.mode,
                    mode_used="LIVE",
                    **classify_error(exc),
                )
                raise

            self._record_operation_status(
                operation=operation,
                status="PASS",
                mode_used="LIVE",
                details={"fallback": False},
            )
            log_event(
                level="INFO",
                component="adapter",
                operation=str(operation),
                status="SUCCESS",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                adapter=self.name,
                adapter_mode=self.mode,
                mode_used="LIVE",
                fallback=False,
            )
            return result

        if live_callable is not None:
            try:
                result = live_callable()
                self._record_operation_status(
                    operation=operation,
                    status="PASS",
                    mode_used="LIVE",
                    details={"fallback": False},
                )
                log_event(
                    level="INFO",
                    component="adapter",
                    operation=str(operation),
                    status="SUCCESS",
                    duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                    adapter=self.name,
                    adapter_mode=self.mode,
                    mode_used="LIVE",
                    fallback=False,
                )
                return result
            except Exception as exc:
                fallback_result = mock_callable()
                self._record_operation_status(
                    operation=operation,
                    status="PASS",
                    mode_used="MOCK",
                    details={
                        "fallback": True,
                        "fallback_reason": str(exc),
                    },
                )
                log_event(
                    level="WARN",
                    component="adapter",
                    operation=str(operation),
                    status="SUCCESS",
                    duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                    adapter=self.name,
                    adapter_mode=self.mode,
                    mode_used="MOCK",
                    fallback=True,
                    fallback_reason=str(exc),
                )
                return fallback_result

        fallback_result = mock_callable()
        self._record_operation_status(
            operation=operation,
            status="PASS",
            mode_used="MOCK",
            details={
                "fallback": True,
                "fallback_reason": "LIVE_HANDLER_NOT_DEFINED",
            },
        )
        log_event(
            level="WARN",
            component="adapter",
            operation=str(operation),
            status="SUCCESS",
            duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
            adapter=self.name,
            adapter_mode=self.mode,
            mode_used="MOCK",
            fallback=True,
            fallback_reason="LIVE_HANDLER_NOT_DEFINED",
        )
        return fallback_result

    def _validate_records(self, records: List[Dict[str, Any]], *, source: str) -> List[Dict[str, Any]]:
        if not isinstance(records, list):
            raise AdapterError(f"{self.name} produced non-list records from {source}")

        validated: List[Dict[str, Any]] = []
        seen_ips: set[str] = set()

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise AdapterError(
                    f"{self.name} produced non-dict record at index {index} from {source}"
                )

            ip_address = str(record.get("ip_address", "")).strip()
            if not ip_address:
                raise AdapterError(
                    f"{self.name} produced record missing ip_address at index {index} from {source}"
                )

            if ip_address in seen_ips:
                raise AdapterError(
                    f"{self.name} produced duplicate ip_address={ip_address} from {source}"
                )
            seen_ips.add(ip_address)
            validated.append(dict(record))

        return validated
