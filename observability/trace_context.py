"""Trace context propagation and deterministic request correlation map."""

from __future__ import annotations

import contextvars
import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("obs_request_id", default="")
_endpoint_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("obs_endpoint", default="")
_actor_role_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("obs_actor_role", default="unknown")

_map_lock = threading.Lock()
_correlation_map: dict[str, list[str]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback_request_id(seed: str | None = None) -> str:
    material = f"{seed or 'trace'}|{_utc_now()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def ensure_request_id(request_id: str | None = None, seed: str | None = None) -> str:
    candidate = str(request_id or "").strip()
    if candidate:
        return candidate

    existing = _request_id_ctx.get().strip()
    if existing:
        return existing

    return _fallback_request_id(seed=seed)


def bind_trace_context(*, request_id: str | None, endpoint: str, actor_role: str) -> str:
    resolved = ensure_request_id(request_id=request_id, seed=f"{endpoint}|{actor_role}")
    _request_id_ctx.set(resolved)
    _endpoint_ctx.set(endpoint)
    _actor_role_ctx.set(actor_role or "unknown")

    with _map_lock:
        _correlation_map.setdefault(resolved, [])

    return resolved


def current_request_context() -> dict[str, str]:
    return {
        "request_id": _request_id_ctx.get(),
        "endpoint": _endpoint_ctx.get(),
        "actor_role": _actor_role_ctx.get(),
    }


def add_event_link(request_id: str, event_id: str) -> None:
    req = str(request_id).strip()
    evt = str(event_id).strip()
    if not req or not evt:
        return

    with _map_lock:
        bucket = _correlation_map.setdefault(req, [])
        bucket.append(evt)


def correlation_snapshot() -> dict[str, list[str]]:
    with _map_lock:
        return {key: list(value) for key, value in sorted(_correlation_map.items())}


def clear_trace_context() -> None:
    _request_id_ctx.set("")
    _endpoint_ctx.set("")
    _actor_role_ctx.set("unknown")


def reset_correlation_map() -> None:
    with _map_lock:
        _correlation_map.clear()
