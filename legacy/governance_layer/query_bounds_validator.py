"""Pre-query request-shape and bounds validator for governance layer."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from legacy.governance_layer.quota_manager import RoleQuota


class QueryBoundsViolation(RuntimeError):
    """Raised when request bounds violate governance policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class QueryBoundsValidator:
    """Validates request limit/offset/time-window and full-scan constraints."""

    def __init__(self, default_max_limit: int = 100, default_max_offset: int = 5000) -> None:
        self._default_max_limit = default_max_limit
        self._default_max_offset = default_max_offset

    @staticmethod
    def _parse_int(value: str | None, default: int, field_name: str) -> int:
        text = str(default) if value is None else value
        try:
            parsed = int(text)
        except ValueError as exc:
            raise QueryBoundsViolation(code="GOV_INVALID_QUERY_BOUNDS", message=f"{field_name} must be an integer") from exc
        if parsed < 0:
            raise QueryBoundsViolation(code="GOV_INVALID_QUERY_BOUNDS", message=f"{field_name} must be non-negative")
        return parsed

    @staticmethod
    def _parse_timestamp(value: str, field_name: str) -> datetime:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise QueryBoundsViolation(
                code="GOV_INVALID_QUERY_BOUNDS",
                message=f"{field_name} must be ISO timestamp",
            ) from exc

    def validate(self, *, path: str, query: Mapping[str, str], quota: RoleQuota) -> tuple[int, int]:
        default_limit = min(self._default_max_limit, quota.max_result_limit)
        limit_value = self._parse_int(query.get("limit"), default=default_limit, field_name="limit")
        offset_value = self._parse_int(query.get("offset"), default=0, field_name="offset")

        hard_limit = min(self._default_max_limit, quota.max_result_limit)
        hard_offset = min(self._default_max_offset, quota.max_offset)

        if limit_value == 0:
            raise QueryBoundsViolation(code="GOV_INVALID_QUERY_BOUNDS", message="limit must be greater than zero")
        if limit_value > hard_limit:
            raise QueryBoundsViolation(
                code="GOV_BOUNDS_EXCEEDED",
                message=f"limit exceeds governance bound: {limit_value} > {hard_limit}",
            )
        if offset_value > hard_offset:
            raise QueryBoundsViolation(
                code="GOV_BOUNDS_EXCEEDED",
                message=f"offset exceeds governance bound: {offset_value} > {hard_offset}",
            )

        if path == "/devices":
            has_vendor = bool(query.get("vendor", "").strip())
            has_state = bool(query.get("execution_state", "").strip())
            if not has_vendor and not has_state:
                raise QueryBoundsViolation(
                    code="GOV_FULL_SCAN_DENIED",
                    message="unfiltered /devices query is not allowed",
                )

        if path == "/evidence":
            ip_address = query.get("ip_address", "").strip()
            start_timestamp = query.get("start_timestamp", "").strip()
            end_timestamp = query.get("end_timestamp", "").strip()

            has_ip = bool(ip_address)
            has_start = bool(start_timestamp)
            has_end = bool(end_timestamp)

            if has_ip and (has_start or has_end):
                raise QueryBoundsViolation(
                    code="GOV_INVALID_QUERY_BOUNDS",
                    message="ip_address cannot be combined with start/end timestamps",
                )

            if not has_ip and not (has_start and has_end):
                raise QueryBoundsViolation(
                    code="GOV_FULL_SCAN_DENIED",
                    message="/evidence requires ip_address or bounded time range",
                )

            if has_start != has_end:
                raise QueryBoundsViolation(
                    code="GOV_INVALID_QUERY_BOUNDS",
                    message="both start_timestamp and end_timestamp are required",
                )

            if has_start and has_end:
                start_dt = self._parse_timestamp(start_timestamp, "start_timestamp")
                end_dt = self._parse_timestamp(end_timestamp, "end_timestamp")
                if end_dt < start_dt:
                    raise QueryBoundsViolation(
                        code="GOV_INVALID_QUERY_BOUNDS",
                        message="end_timestamp must be greater than or equal to start_timestamp",
                    )
                span_minutes = (end_dt - start_dt).total_seconds() / 60.0
                if span_minutes > float(quota.max_time_range_minutes):
                    raise QueryBoundsViolation(
                        code="GOV_BOUNDS_EXCEEDED",
                        message=(
                            f"time range exceeds governance bound: "
                            f"{span_minutes:.2f}m > {quota.max_time_range_minutes}m"
                        ),
                    )

        return limit_value, offset_value
