"""Authorization policies and request-scope enforcement for API routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fastapi import Request

from legacy.auth_layer.auth_provider import AuthIdentity
from legacy.auth_layer.auth_validator import AuthValidationError, validate_policy_exists


class AuthorizationError(RuntimeError):
    """Raised when authorization or request scope validation fails."""

    def __init__(self, code: str, message: str, access_context: dict[str, str]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_type = "AUTHZ"
        self.access_context = access_context


@dataclass(frozen=True)
class RolePolicy:
    name: str
    role: str
    allowed_endpoints: frozenset[str]
    allow_time_range: bool
    require_scope: bool


POLICIES: Mapping[str, RolePolicy] = {
    "admin": RolePolicy(
        name="admin_full_read",
        role="admin",
        allowed_endpoints=frozenset({"/devices", "/device/{ip_address}", "/evidence", "/runs/{run_id}"}),
        allow_time_range=True,
        require_scope=False,
    ),
    "reader": RolePolicy(
        name="reader_read_all",
        role="reader",
        allowed_endpoints=frozenset({"/devices", "/device/{ip_address}", "/evidence", "/runs/{run_id}"}),
        allow_time_range=True,
        require_scope=False,
    ),
    "restricted": RolePolicy(
        name="restricted_scoped_read",
        role="restricted",
        allowed_endpoints=frozenset({"/device/{ip_address}", "/evidence", "/runs/{run_id}"}),
        allow_time_range=False,
        require_scope=True,
    ),
}


def _access_context(identity: AuthIdentity, policy_name: str) -> dict[str, str]:
    return {
        "user_id": identity.user_id,
        "role": identity.role,
        "policy_applied": policy_name,
    }


def _route_key_and_path_values(path: str) -> tuple[str, dict[str, str]]:
    if path == "/devices":
        return "/devices", {}
    if path == "/evidence":
        return "/evidence", {}
    if path.startswith("/device/"):
        ip_value = path.removeprefix("/device/").strip()
        return "/device/{ip_address}", {"ip_address": ip_value}
    if path.startswith("/runs/"):
        run_id = path.removeprefix("/runs/").strip()
        return "/runs/{run_id}", {"run_id": run_id}
    return "<unknown>", {}


def _require(condition: bool, code: str, message: str, context: dict[str, str]) -> None:
    if not condition:
        raise AuthorizationError(code=code, message=message, access_context=context)


def resolve_policy(identity: AuthIdentity) -> RolePolicy:
    policy = POLICIES.get(identity.role)
    if policy is None:
        raise AuthorizationError(
            code="AUTHZ_POLICY_MISSING",
            message=f"no policy configured for role: {identity.role}",
            access_context={
                "user_id": identity.user_id,
                "role": identity.role,
                "policy_applied": "missing",
            },
        )
    try:
        validate_policy_exists(policy.name)
    except AuthValidationError as exc:
        raise AuthorizationError(
            code="AUTHZ_POLICY_INVALID",
            message=str(exc),
            access_context={
                "user_id": identity.user_id,
                "role": identity.role,
                "policy_applied": "invalid",
            },
        ) from exc
    return policy


def authorize_request(identity: AuthIdentity, request: Request) -> dict[str, str]:
    policy = resolve_policy(identity)
    context = _access_context(identity=identity, policy_name=policy.name)

    route_key, path_values = _route_key_and_path_values(request.url.path)
    _require(route_key in policy.allowed_endpoints, "AUTHZ_ENDPOINT_DENIED", "endpoint access denied", context)

    if not policy.require_scope:
        return context

    if route_key == "/device/{ip_address}":
        requested_ip = path_values.get("ip_address", "")
        _require(requested_ip in identity.allowed_ips, "AUTHZ_SCOPE_DENIED", "ip scope denied", context)
        return context

    if route_key == "/runs/{run_id}":
        requested_run_id = path_values.get("run_id", "")
        _require(requested_run_id in identity.allowed_run_ids, "AUTHZ_SCOPE_DENIED", "run scope denied", context)
        return context

    if route_key == "/evidence":
        ip_address = request.query_params.get("ip_address", "").strip()
        if ip_address:
            _require(ip_address in identity.allowed_ips, "AUTHZ_SCOPE_DENIED", "ip scope denied", context)
            return context

        if not policy.allow_time_range:
            raise AuthorizationError(
                code="AUTHZ_SCOPE_DENIED",
                message="time-range evidence access denied for restricted role",
                access_context=context,
            )

    return context
