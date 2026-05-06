from __future__ import annotations

import hashlib

from observability import context as obs_context


def generate_trace_id(*, run_id: str, phase: str, mode: str) -> str:
    material = "|".join(
        [
            str(run_id or "").strip(),
            str(phase or "").strip().upper(),
            str(mode or "").strip().upper(),
            "OBS_TRACE_V1",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def ensure_trace_id(*, run_id: str, phase: str, mode: str) -> str:
    existing = obs_context.get_trace_id()
    if existing:
        return existing

    generated = generate_trace_id(run_id=run_id, phase=phase, mode=mode)
    obs_context.update_run_context(trace_id=generated)
    return generated


def _span_material(*, trace_id: str, component: str, operation: str, sequence: int, parent_span: str) -> str:
    return "|".join(
        [
            str(trace_id or "").strip(),
            str(parent_span or "").strip(),
            str(component or "").strip(),
            str(operation or "").strip(),
            str(sequence),
            "OBS_SPAN_V1",
        ]
    )


def next_span_id(*, component: str, operation: str) -> str:
    run_context = obs_context.get_run_context()
    trace_id = str(run_context.get("trace_id") or "").strip()
    if not trace_id:
        trace_id = ensure_trace_id(
            run_id=str(run_context.get("run_id") or ""),
            phase=str(run_context.get("phase") or ""),
            mode=str(run_context.get("mode") or ""),
        )

    sequence = obs_context.next_span_sequence()
    parent_span = obs_context.current_span_id()
    material = _span_material(
        trace_id=trace_id,
        component=component,
        operation=operation,
        sequence=sequence,
        parent_span=parent_span,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def begin_span(*, component: str, operation: str) -> str:
    span_id = next_span_id(component=component, operation=operation)
    obs_context.push_span(span_id)
    return span_id


def end_span(span_id: str) -> None:
    obs_context.pop_span(span_id)


def current_span_id() -> str:
    return obs_context.current_span_id()
