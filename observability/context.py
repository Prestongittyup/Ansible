from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Dict, List

_CONTEXT_LOCK = threading.RLock()

_run_context: Dict[str, Any] = {
    "trace_id": "",
    "run_id": "",
    "phase": "",
    "mode": "",
    "debug": False,
}
_span_stack: List[str] = []
_span_counter = 0
_execution_timeline: List[Dict[str, Any]] = []
_external_failures: List[Dict[str, Any]] = []
_failure_point = ""

_TIMELINE_MAX_ENTRIES = 20000


def reset_observability_context() -> None:
    global _span_counter, _failure_point
    with _CONTEXT_LOCK:
        _run_context.clear()
        _run_context.update(
            {
                "trace_id": "",
                "run_id": "",
                "phase": "",
                "mode": "",
                "debug": False,
            }
        )
        _span_stack.clear()
        _span_counter = 0
        _execution_timeline.clear()
        _external_failures.clear()
        _failure_point = ""


def initialize_run_context(
    *,
    run_id: str,
    phase: str,
    mode: str,
    trace_id: str = "",
    debug: bool = False,
) -> None:
    global _span_counter, _failure_point
    with _CONTEXT_LOCK:
        _run_context.update(
            {
                "trace_id": str(trace_id or "").strip(),
                "run_id": str(run_id or "").strip(),
                "phase": str(phase or "").strip().upper(),
                "mode": str(mode or "").strip().upper(),
                "debug": bool(debug),
            }
        )
        _span_stack.clear()
        _span_counter = 0
        _execution_timeline.clear()
        _external_failures.clear()
        _failure_point = ""


def update_run_context(**fields: Any) -> None:
    with _CONTEXT_LOCK:
        for key, value in fields.items():
            if key == "debug":
                _run_context[key] = bool(value)
            elif key in {"phase", "mode"}:
                _run_context[key] = str(value or "").strip().upper()
            else:
                _run_context[key] = str(value or "").strip()


def get_run_context() -> Dict[str, Any]:
    with _CONTEXT_LOCK:
        return dict(_run_context)


def get_trace_id() -> str:
    with _CONTEXT_LOCK:
        return str(_run_context.get("trace_id", ""))


def get_run_id() -> str:
    with _CONTEXT_LOCK:
        return str(_run_context.get("run_id", ""))


def get_phase() -> str:
    with _CONTEXT_LOCK:
        return str(_run_context.get("phase", ""))


def get_mode() -> str:
    with _CONTEXT_LOCK:
        return str(_run_context.get("mode", ""))


def is_debug_enabled() -> bool:
    with _CONTEXT_LOCK:
        return bool(_run_context.get("debug", False))


def next_span_sequence() -> int:
    global _span_counter
    with _CONTEXT_LOCK:
        _span_counter += 1
        return _span_counter


def push_span(span_id: str) -> None:
    with _CONTEXT_LOCK:
        _span_stack.append(str(span_id or "").strip())


def pop_span(span_id: str | None = None) -> str:
    with _CONTEXT_LOCK:
        if not _span_stack:
            return ""
        if span_id is None:
            return _span_stack.pop()

        token = str(span_id).strip()
        if _span_stack[-1] == token:
            return _span_stack.pop()

        for index in range(len(_span_stack) - 1, -1, -1):
            if _span_stack[index] == token:
                return _span_stack.pop(index)

        return ""


def current_span_id() -> str:
    with _CONTEXT_LOCK:
        if not _span_stack:
            return ""
        return _span_stack[-1]


def add_timeline_entry(entry: Dict[str, Any]) -> None:
    with _CONTEXT_LOCK:
        _execution_timeline.append(deepcopy(dict(entry)))
        if len(_execution_timeline) > _TIMELINE_MAX_ENTRIES:
            overflow = len(_execution_timeline) - _TIMELINE_MAX_ENTRIES
            del _execution_timeline[0:overflow]


def get_execution_timeline() -> List[Dict[str, Any]]:
    with _CONTEXT_LOCK:
        return [deepcopy(item) for item in _execution_timeline]


def mark_failure_point(value: str) -> None:
    global _failure_point
    token = str(value or "").strip()
    if not token:
        return
    with _CONTEXT_LOCK:
        if not _failure_point:
            _failure_point = token


def get_failure_point() -> str:
    with _CONTEXT_LOCK:
        return str(_failure_point)


def add_external_failure(payload: Dict[str, Any]) -> None:
    with _CONTEXT_LOCK:
        _external_failures.append(deepcopy(dict(payload)))


def get_external_failures() -> List[Dict[str, Any]]:
    with _CONTEXT_LOCK:
        return [deepcopy(item) for item in _external_failures]
