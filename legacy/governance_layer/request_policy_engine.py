"""Central governance policy engine combining bounds, quota, and rate-limit checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request

from legacy.governance_layer.query_bounds_validator import QueryBoundsValidator, QueryBoundsViolation
from legacy.governance_layer.quota_manager import QuotaManager, QuotaViolation
from legacy.governance_layer.rate_limiter import RateLimiter, RateLimitViolation


class GovernancePolicyError(RuntimeError):
    """Raised when governance policy rejects a request."""

    def __init__(self, code: str, message: str, error_type: str = "AUTHZ") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_type = error_type


@dataclass(frozen=True)
class GovernanceDecision:
    user_id: str
    role: str
    endpoint: str
    policy_applied: str
    requested_limit: int
    requested_offset: int


class RequestPolicyEngine:
    """Evaluates pre-query governance controls in deterministic order."""

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        quota_manager: QuotaManager | None = None,
        bounds_validator: QueryBoundsValidator | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter()
        self._quota_manager = quota_manager or QuotaManager()
        self._bounds_validator = bounds_validator or QueryBoundsValidator()

    @staticmethod
    def _canonical_endpoint(path: str) -> str:
        if path == "/devices":
            return "/devices"
        if path == "/evidence":
            return "/evidence"
        if path.startswith("/device/"):
            return "/device/{ip_address}"
        if path.startswith("/runs/"):
            return "/runs/{run_id}"
        return "<unknown>"

    @staticmethod
    def _require_access_context(access_context: Mapping[str, Any] | None) -> tuple[str, str, str]:
        if not isinstance(access_context, Mapping):
            raise GovernancePolicyError(
                code="GOV_MISSING_ACCESS_CONTEXT",
                message="missing access_context for governance evaluation",
            )

        user_id = str(access_context.get("user_id", "")).strip()
        role = str(access_context.get("role", "")).strip()
        policy_applied = str(access_context.get("policy_applied", "")).strip()

        if not user_id or not role or not policy_applied:
            raise GovernancePolicyError(
                code="GOV_MISSING_ACCESS_CONTEXT",
                message="access_context is incomplete",
            )

        return user_id, role, policy_applied

    @staticmethod
    def _query_map(request: Request) -> dict[str, str]:
        return {key: value for key, value in request.query_params.multi_items()}

    def evaluate(self, request: Request, access_context: Mapping[str, Any] | None) -> GovernanceDecision:
        user_id, role, policy_applied = self._require_access_context(access_context)
        endpoint = self._canonical_endpoint(request.url.path)
        if endpoint == "<unknown>":
            raise GovernancePolicyError(code="GOV_ENDPOINT_UNKNOWN", message="unsupported endpoint")

        query = self._query_map(request)

        try:
            role_quota = self._quota_manager.resolve_quota(role)
            requested_limit, requested_offset = self._bounds_validator.validate(path=request.url.path, query=query, quota=role_quota)
            self._quota_manager.validate_request_quota(
                role=role,
                endpoint=endpoint,
                limit_value=requested_limit,
                offset_value=requested_offset,
            )
            per_minute, per_hour = self._quota_manager.endpoint_limits(role, endpoint)
            self._rate_limiter.check_and_record(
                user_id=user_id,
                role=role,
                endpoint=endpoint,
                per_minute_limit=per_minute,
                per_hour_limit=per_hour,
            )
        except (QueryBoundsViolation, QuotaViolation, RateLimitViolation) as exc:
            raise GovernancePolicyError(code=exc.code, message=exc.message) from exc

        return GovernanceDecision(
            user_id=user_id,
            role=role,
            endpoint=endpoint,
            policy_applied=policy_applied,
            requested_limit=requested_limit,
            requested_offset=requested_offset,
        )
