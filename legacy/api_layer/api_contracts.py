"""Request/response contracts for Sprint 5 API boundary."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Dict, List, Sequence, Tuple

API_SCHEMA_VERSION = "2.1"


def _normalize_timestamp(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return text


def _parse_iso_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(_normalize_timestamp(value))


def _collect_timestamps(data: Any) -> List[str]:
    candidates: List[str] = []

    if isinstance(data, dict):
        for key in ("timestamp", "last_updated", "last_seen"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
    elif isinstance(data, list):
        for item in data:
            candidates.extend(_collect_timestamps(item))

    return candidates


def derive_response_timestamp(data: Any) -> str:
    candidates = _collect_timestamps(data)
    if not candidates:
        return "1970-01-01T00:00:00+00:00"

    parsed: List[Tuple[datetime, str]] = [(_parse_iso_timestamp(value), value) for value in candidates]
    parsed.sort(key=lambda item: item[0])
    return parsed[-1][1]


def build_request_id(method: str, path: str, query_pairs: Sequence[Tuple[str, str]]) -> str:
    canonical = "|".join(
        [
            method.upper().strip(),
            path.strip(),
            "&".join(f"{key}={value}" for key, value in sorted(query_pairs)),
        ]
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_response_envelope(request_id: str, data: Any, access_context: Dict[str, str]) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "timestamp": derive_response_timestamp(data),
        "schema_version": API_SCHEMA_VERSION,
        "access_context": access_context,
        "data": data,
    }


def build_error_envelope(
    request_id: str,
    code: str,
    message: str,
    error_type: str,
    access_context: Dict[str, str],
) -> Dict[str, Any]:
    error_data = {
        "error": {
            "code": code,
            "message": message,
            "type": error_type,
        }
    }
    envelope = build_response_envelope(request_id=request_id, data=error_data, access_context=access_context)
    return envelope
