"""Role and endpoint quota model for governance enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class QuotaViolation(RuntimeError):
    """Raised when quota policy validation fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RoleQuota:
    name: str
    role: str
    per_minute_limit: int
    per_hour_limit: int
    endpoint_per_minute: Mapping[str, int]
    endpoint_per_hour: Mapping[str, int]
    max_result_limit: int
    max_offset: int
    max_time_range_minutes: int


ROLE_QUOTAS: Mapping[str, RoleQuota] = {
    "admin": RoleQuota(
        name="admin_high_quota",
        role="admin",
        per_minute_limit=240,
        per_hour_limit=4000,
        endpoint_per_minute={"/devices": 180, "/device/{ip_address}": 220, "/evidence": 120, "/runs/{run_id}": 140},
        endpoint_per_hour={"/devices": 2500, "/device/{ip_address}": 3500, "/evidence": 1600, "/runs/{run_id}": 2000},
        max_result_limit=500,
        max_offset=10000,
        max_time_range_minutes=24 * 60,
    ),
    "reader": RoleQuota(
        name="reader_medium_quota",
        role="reader",
        per_minute_limit=120,
        per_hour_limit=1800,
        endpoint_per_minute={"/devices": 80, "/device/{ip_address}": 100, "/evidence": 60, "/runs/{run_id}": 70},
        endpoint_per_hour={"/devices": 1100, "/device/{ip_address}": 1400, "/evidence": 800, "/runs/{run_id}": 900},
        max_result_limit=200,
        max_offset=2000,
        max_time_range_minutes=12 * 60,
    ),
    "restricted": RoleQuota(
        name="restricted_low_quota",
        role="restricted",
        per_minute_limit=30,
        per_hour_limit=300,
        endpoint_per_minute={"/device/{ip_address}": 25, "/evidence": 20, "/runs/{run_id}": 20},
        endpoint_per_hour={"/device/{ip_address}": 250, "/evidence": 160, "/runs/{run_id}": 160},
        max_result_limit=50,
        max_offset=200,
        max_time_range_minutes=60,
    ),
}


class QuotaManager:
    """Resolves role quotas and validates request-size constraints."""

    def __init__(self, quotas: Mapping[str, RoleQuota] | None = None) -> None:
        self._quotas = quotas or ROLE_QUOTAS

    def resolve_quota(self, role: str) -> RoleQuota:
        quota = self._quotas.get(role)
        if quota is None:
            raise QuotaViolation(code="GOV_UNKNOWN_ROLE", message=f"no quota configured for role: {role}")
        return quota

    def endpoint_limits(self, role: str, endpoint: str) -> tuple[int, int]:
        quota = self.resolve_quota(role)
        per_minute = quota.endpoint_per_minute.get(endpoint, quota.per_minute_limit)
        per_hour = quota.endpoint_per_hour.get(endpoint, quota.per_hour_limit)
        return per_minute, per_hour

    def validate_request_quota(self, *, role: str, endpoint: str, limit_value: int, offset_value: int) -> RoleQuota:
        quota = self.resolve_quota(role)
        if limit_value > quota.max_result_limit:
            raise QuotaViolation(
                code="GOV_QUOTA_EXCEEDED",
                message=f"requested limit exceeds quota for role {role}: {limit_value} > {quota.max_result_limit}",
            )
        if offset_value > quota.max_offset:
            raise QuotaViolation(
                code="GOV_QUOTA_EXCEEDED",
                message=f"requested offset exceeds quota for role {role}: {offset_value} > {quota.max_offset}",
            )
        return quota
