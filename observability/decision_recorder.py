"""Passive instrumentation hooks for API/auth/governance/query and system decisions."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, Tuple

from fastapi import FastAPI, Request

from legacy.api_layer.api_contracts import build_request_id
from observability.event_collector import EventCollector
from observability.schema import OBSERVABILITY_SCHEMA_VERSION, utc_now
from observability.trace_context import bind_trace_context, clear_trace_context, current_request_context, ensure_request_id

_QUERY_METHODS = frozenset(
    {
        "get_device",
        "list_devices_by_vendor",
        "list_devices_by_execution_state",
        "get_validation_evidence",
        "get_evidence_by_run",
        "get_evidence_by_time_range",
        "get_latest_validation_state",
    }
)


class DecisionRecorder:
    """Records passive decision events and isolates recorder failures from runtime flow."""

    def __init__(self, collector: EventCollector | None = None) -> None:
        self._collector = collector or EventCollector()

    @property
    def collector(self) -> EventCollector:
        return self._collector

    @staticmethod
    def _request_id(request: Request) -> str:
        query_pairs: Sequence[Tuple[str, str]] = [(key, value) for key, value in request.query_params.multi_items()]
        return build_request_id(request.method, request.url.path, query_pairs)

    @staticmethod
    def _extract_error(response: Any) -> tuple[str, str] | None:
        body = getattr(response, "body", None)
        if body is None:
            return None

        try:
            payload = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body))
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if not isinstance(error, dict):
            return None

        code = str(error.get("code", "")).strip()
        error_type = str(error.get("type", "")).strip().upper()
        if not code:
            return None
        return code, error_type

    def _emit(
        self,
        *,
        event_type: str,
        request_id: str,
        actor_role: str,
        endpoint: str,
        decision: str,
        reason_code: str,
        latency_ms: float,
    ) -> None:
        try:
            self._collector.emit(
                {
                    "event_id": "",
                    "timestamp": utc_now(),
                    "event_type": event_type,
                    "request_id": request_id,
                    "actor_role": actor_role,
                    "endpoint": endpoint,
                    "decision": decision,
                    "reason_code": reason_code,
                    "schema_version": OBSERVABILITY_SCHEMA_VERSION,
                    "latency_ms": latency_ms,
                    "trace_hash": "",
                }
            )
        except Exception:
            # Observability must never break the system path.
            return

    def install_api_hooks(self, app: FastAPI) -> None:
        """Attach passive request-level observability middleware."""

        @app.middleware("http")
        async def observability_tap(request: Request, call_next):
            started = time.perf_counter()
            request_id = self._request_id(request)
            access_context = getattr(request.state, "access_context", None)
            actor_role = "unknown"
            if isinstance(access_context, Mapping):
                actor_role = str(access_context.get("role", "unknown")) or "unknown"

            bind_trace_context(request_id=request_id, endpoint=request.url.path, actor_role=actor_role)

            try:
                response = await call_next(request)
            except Exception:
                latency_ms = (time.perf_counter() - started) * 1000.0
                self._emit(
                    event_type="API_REQUEST",
                    request_id=request_id,
                    actor_role=actor_role,
                    endpoint=request.url.path,
                    decision="FAIL",
                    reason_code="API_EXCEPTION",
                    latency_ms=latency_ms,
                )
                clear_trace_context()
                raise

            latency_ms = (time.perf_counter() - started) * 1000.0

            access_context = getattr(request.state, "access_context", None)
            if isinstance(access_context, Mapping):
                actor_role = str(access_context.get("role", "unknown")) or "unknown"

            error_info = self._extract_error(response)
            status_code = int(getattr(response, "status_code", 500))

            api_decision = "PASS" if status_code < 400 else ("BLOCKED" if status_code in {401, 403, 429} else "FAIL")
            api_reason = "API_OK" if api_decision == "PASS" else f"HTTP_{status_code}"
            self._emit(
                event_type="API_REQUEST",
                request_id=request_id,
                actor_role=actor_role,
                endpoint=request.url.path,
                decision=api_decision,
                reason_code=api_reason,
                latency_ms=latency_ms,
            )

            governance_context = getattr(request.state, "governance_context", None)
            if isinstance(governance_context, Mapping):
                self._emit(
                    event_type="GOVERNANCE_DECISION",
                    request_id=request_id,
                    actor_role=actor_role,
                    endpoint=str(governance_context.get("endpoint", request.url.path)),
                    decision="PASS",
                    reason_code="GOV_ALLOW",
                    latency_ms=latency_ms,
                )

            if error_info is not None:
                code, error_type = error_info
                if error_type == "AUTH":
                    self._emit(
                        event_type="AUTH_DECISION",
                        request_id=request_id,
                        actor_role=actor_role,
                        endpoint=request.url.path,
                        decision="FAIL",
                        reason_code=code,
                        latency_ms=latency_ms,
                    )
                elif code.startswith("GOV_"):
                    self._emit(
                        event_type="GOVERNANCE_DECISION",
                        request_id=request_id,
                        actor_role=actor_role,
                        endpoint=request.url.path,
                        decision="BLOCKED",
                        reason_code=code,
                        latency_ms=latency_ms,
                    )
                elif error_type == "AUTHZ":
                    self._emit(
                        event_type="AUTHZ_DECISION",
                        request_id=request_id,
                        actor_role=actor_role,
                        endpoint=request.url.path,
                        decision="BLOCKED",
                        reason_code=code,
                        latency_ms=latency_ms,
                    )
            elif isinstance(access_context, Mapping):
                self._emit(
                    event_type="AUTH_DECISION",
                    request_id=request_id,
                    actor_role=actor_role,
                    endpoint=request.url.path,
                    decision="PASS",
                    reason_code="AUTH_OK",
                    latency_ms=latency_ms,
                )
                self._emit(
                    event_type="AUTHZ_DECISION",
                    request_id=request_id,
                    actor_role=actor_role,
                    endpoint=request.url.path,
                    decision="PASS",
                    reason_code="AUTHZ_OK",
                    latency_ms=latency_ms,
                )

            clear_trace_context()
            return response

    def wrap_query_service(self, query_service: Any) -> Any:
        """Return a passive proxy that records query-service calls and outcomes."""

        recorder = self

        class _QueryServiceProxy:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            def __getattr__(self, name: str) -> Any:
                target = getattr(self._inner, name)
                if name not in _QUERY_METHODS or not callable(target):
                    return target

                @wraps(target)
                def _wrapped(*args, **kwargs):
                    started = time.perf_counter()
                    request_context = current_request_context()
                    request_id = ensure_request_id(request_context.get("request_id", ""), seed=name)
                    actor_role = request_context.get("actor_role", "unknown") or "unknown"
                    decision = "PASS"
                    reason_code = "QUERY_OK"
                    try:
                        return target(*args, **kwargs)
                    except Exception:
                        decision = "FAIL"
                        reason_code = "QUERY_EXCEPTION"
                        raise
                    finally:
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        recorder._emit(
                            event_type="QUERY_EXECUTION",
                            request_id=request_id,
                            actor_role=actor_role,
                            endpoint=name,
                            decision=decision,
                            reason_code=reason_code,
                            latency_ms=latency_ms,
                        )

                return _wrapped

        return _QueryServiceProxy(query_service)

    def install_ingestion_hook(self, module: Any) -> bool:
        """Attach passive ingestion-completion event tap via runtime wrapper."""
        original = getattr(module, "run_ingestion", None)
        if not callable(original):
            return False
        if bool(getattr(original, "_obs_wrapped", False)):
            return True

        recorder = self

        @wraps(original)
        def _wrapped_run_ingestion(*args, **kwargs):
            started = time.perf_counter()
            decision = "PASS"
            reason_code = "INGEST_OK"
            try:
                result = original(*args, **kwargs)
                if isinstance(result, Mapping):
                    status = str(result.get("ingestion_status", "FAIL")).upper()
                    if status != "PASS":
                        decision = "FAIL"
                        reason_code = "INGEST_FAIL_STATUS"
                else:
                    decision = "FAIL"
                    reason_code = "INGEST_RESULT_INVALID"
                return result
            except Exception:
                decision = "FAIL"
                reason_code = "INGEST_EXCEPTION"
                raise
            finally:
                latency_ms = (time.perf_counter() - started) * 1000.0
                recorder._emit(
                    event_type="INGESTION_EVENT",
                    request_id=ensure_request_id(seed="ingestion"),
                    actor_role="system",
                    endpoint="persistence.run_ingestion",
                    decision=decision,
                    reason_code=reason_code,
                    latency_ms=latency_ms,
                )

        setattr(_wrapped_run_ingestion, "_obs_wrapped", True)
        setattr(module, "run_ingestion", _wrapped_run_ingestion)
        return True

    def run_emv_with_hook(self, python_executable: str | None = None) -> tuple[int, dict[str, Any]]:
        exe = python_executable or sys.executable
        command = [exe, "ci/meta/ci_enforcement_meta_validator.py"]
        started = time.perf_counter()
        proc = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True)
        latency_ms = (time.perf_counter() - started) * 1000.0

        payload: dict[str, Any]
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        status = str(payload.get("emv_status", "FAIL")).upper()
        decision = "PASS" if proc.returncode == 0 and status == "PASS" else "FAIL"
        reason_code = "EMV_OK" if decision == "PASS" else "EMV_FAIL"

        self._emit(
            event_type="EMV_RUN",
            request_id=ensure_request_id(seed="emv"),
            actor_role="system",
            endpoint="ci/meta/ci_enforcement_meta_validator.py",
            decision=decision,
            reason_code=reason_code,
            latency_ms=latency_ms,
        )

        return proc.returncode, payload

    def run_ci_kernel_with_hook(self, python_executable: str | None = None) -> tuple[int, dict[str, Any]]:
        exe = python_executable or sys.executable
        command = [exe, "ci/run_ci_kernel.py"]
        started = time.perf_counter()
        proc = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True)
        latency_ms = (time.perf_counter() - started) * 1000.0

        payload: dict[str, Any]
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        status = str(payload.get("ci_status", "FAIL")).upper()
        decision = "PASS" if proc.returncode == 0 and status == "PASS" else "FAIL"
        reason_code = "CI_KERNEL_OK" if decision == "PASS" else "CI_KERNEL_FAIL"

        self._emit(
            event_type="CI_KERNEL_RUN",
            request_id=ensure_request_id(seed="ci-kernel"),
            actor_role="system",
            endpoint="ci/run_ci_kernel.py",
            decision=decision,
            reason_code=reason_code,
            latency_ms=latency_ms,
        )

        return proc.returncode, payload
