from __future__ import annotations

import atexit
import json
import logging
import queue
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterator

from observability import context as obs_context
from observability import trace as obs_trace

_LOGGER_NAME = "system_observability"
_LOGGER_LOCK = threading.RLock()
_QUEUE_LISTENER: QueueListener | None = None

_SECRET_KEYWORDS = {
    "token",
    "password",
    "secret",
    "api_key",
    "authorization",
    "auth",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int_ms(value: float) -> int:
    if value <= 0:
        return 0
    return int(round(value, 0))


def _level_number(level: str) -> int:
    token = str(level or "INFO").strip().upper()
    if token == "DEBUG":
        return logging.DEBUG
    if token == "WARN":
        return logging.WARNING
    if token == "ERROR":
        return logging.ERROR
    return logging.INFO


def _safe_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    return str(value)


def sanitize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(secret in lowered for secret in _SECRET_KEYWORDS):
                sanitized[key_text] = "***"
            else:
                sanitized[key_text] = sanitize_payload(value)
        return sanitized

    if isinstance(payload, list):
        return [sanitize_payload(item) for item in payload]

    if isinstance(payload, tuple):
        return [sanitize_payload(item) for item in payload]

    return _safe_json(payload)


def classify_error(
    exc: BaseException | None = None,
    *,
    message: str = "",
    http_status: int | None = None,
) -> Dict[str, Any]:
    text = str(message or exc or "").strip()
    lowered = text.lower()

    if http_status == 401 or "401" in lowered or "auth" in lowered:
        return {
            "error_type": "AUTH_FAILURE",
            "error_message": text,
            "retryable": False,
        }

    if "timeout" in lowered or "timed out" in lowered:
        return {
            "error_type": "TIMEOUT",
            "error_message": text,
            "retryable": True,
        }

    if "connection" in lowered or "unreachable" in lowered or "urlerror" in lowered:
        return {
            "error_type": "CONNECTION_ERROR",
            "error_message": text,
            "retryable": True,
        }

    if "schema" in lowered or "validation" in lowered:
        return {
            "error_type": "SCHEMA_ERROR",
            "error_message": text,
            "retryable": False,
        }

    return {
        "error_type": "UNKNOWN",
        "error_message": text,
        "retryable": False,
    }


def _build_message(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False)


def _logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.propagate = False
    return logger


def shutdown_logging() -> None:
    global _QUEUE_LISTENER

    with _LOGGER_LOCK:
        listener = _QUEUE_LISTENER
        _QUEUE_LISTENER = None

        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

        logger = _logger()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)


def configure_logging(*, debug: bool, log_file_path: str | Path = "logs/system.log") -> None:
    global _QUEUE_LISTENER

    with _LOGGER_LOCK:
        shutdown_logging()

        logger = _logger()
        logger.setLevel(logging.DEBUG if debug else logging.INFO)

        log_path = Path(log_file_path)
        if not log_path.is_absolute():
            log_path = (Path.cwd() / log_path).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        queue_handler = QueueHandler(log_queue)
        queue_handler.setLevel(logging.DEBUG)
        logger.addHandler(queue_handler)

        formatter = logging.Formatter("%(message)s")

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.DEBUG if debug else logging.INFO)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)

        listener = QueueListener(
            log_queue,
            stream_handler,
            file_handler,
            respect_handler_level=True,
        )
        listener.start()
        _QUEUE_LISTENER = listener


def initialize_observability(
    *,
    run_id: str,
    phase: str,
    mode: str,
    trace_id: str = "",
    debug: bool = False,
    log_file_path: str | Path = "logs/system.log",
) -> str:
    configure_logging(debug=debug, log_file_path=log_file_path)

    obs_context.initialize_run_context(
        run_id=run_id,
        phase=phase,
        mode=mode,
        trace_id=trace_id,
        debug=debug,
    )

    resolved_trace = obs_trace.ensure_trace_id(run_id=run_id, phase=phase, mode=mode)
    obs_context.update_run_context(trace_id=resolved_trace)
    return resolved_trace


def _base_payload(
    *,
    level: str,
    component: str,
    operation: str,
    status: str,
    duration_ms: int,
    span_id: str,
) -> Dict[str, Any]:
    run_context = obs_context.get_run_context()

    return {
        "timestamp": _utc_now(),
        "level": str(level).strip().upper(),
        "trace_id": str(run_context.get("trace_id", "")),
        "span_id": str(span_id or ""),
        "run_id": str(run_context.get("run_id", "")),
        "phase": str(run_context.get("phase", "")),
        "mode": str(run_context.get("mode", "")),
        "component": str(component or ""),
        "operation": str(operation or ""),
        "status": str(status or "").strip().upper(),
        "duration_ms": int(duration_ms),
    }


def log_event(
    *,
    level: str,
    component: str,
    operation: str,
    status: str,
    duration_ms: int = 0,
    span_id: str | None = None,
    include_timeline: bool = True,
    **fields: Any,
) -> Dict[str, Any]:
    if str(level).strip().upper() == "DEBUG" and not obs_context.is_debug_enabled():
        return {}

    logger = _logger()
    if not logger.handlers:
        configure_logging(debug=obs_context.is_debug_enabled())

    resolved_span = str(span_id or obs_trace.current_span_id() or "")
    payload = _base_payload(
        level=level,
        component=component,
        operation=operation,
        status=status,
        duration_ms=duration_ms,
        span_id=resolved_span,
    )

    sanitized = sanitize_payload(fields)
    if isinstance(sanitized, dict):
        for key in sorted(sanitized.keys()):
            payload[str(key)] = sanitized[key]

    logger.log(_level_number(level), _build_message(payload))

    if include_timeline:
        obs_context.add_timeline_entry(payload)

    if payload["status"] == "FAIL":
        obs_context.mark_failure_point(f"{payload['component']}::{payload['operation']}")
        external_signal = (
            payload.get("component") == "adapter"
            or "http_status" in payload
            or "external_system" in payload
            or payload.get("error_type") in {"AUTH_FAILURE", "TIMEOUT", "CONNECTION_ERROR"}
        )
        if external_signal:
            obs_context.add_external_failure(
                {
                    "timestamp": payload["timestamp"],
                    "component": payload["component"],
                    "operation": payload["operation"],
                    "error_type": payload.get("error_type", "UNKNOWN"),
                    "error_message": payload.get("error_message", ""),
                    "retryable": bool(payload.get("retryable", False)),
                    "http_status": payload.get("http_status"),
                }
            )

    return payload


@contextmanager
def log_span(
    *,
    component: str,
    operation: str,
    level: str = "INFO",
    **fields: Any,
) -> Iterator[str]:
    span_id = obs_trace.begin_span(component=component, operation=operation)
    started = perf_counter()

    log_event(
        level=level,
        component=component,
        operation=operation,
        status="START",
        duration_ms=0,
        span_id=span_id,
        **fields,
    )

    try:
        yield span_id
    except Exception as exc:
        duration_ms = _to_int_ms((perf_counter() - started) * 1000.0)
        classified = classify_error(exc)
        log_event(
            level="ERROR",
            component=component,
            operation=operation,
            status="FAIL",
            duration_ms=duration_ms,
            span_id=span_id,
            **classified,
            **fields,
        )
        raise
    else:
        duration_ms = _to_int_ms((perf_counter() - started) * 1000.0)
        log_event(
            level=level,
            component=component,
            operation=operation,
            status="SUCCESS",
            duration_ms=duration_ms,
            span_id=span_id,
            **fields,
        )
    finally:
        obs_trace.end_span(span_id)


def log_debug_payload(*, component: str, operation: str, payload: Any, **fields: Any) -> None:
    if not obs_context.is_debug_enabled():
        return

    log_event(
        level="DEBUG",
        component=component,
        operation=operation,
        status="SUCCESS",
        duration_ms=0,
        payload=sanitize_payload(payload),
        **fields,
    )


atexit.register(shutdown_logging)
