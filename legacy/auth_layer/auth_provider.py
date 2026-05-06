"""Token-based placeholder authentication provider for API-boundary access control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request

from legacy.auth_layer.auth_validator import (
    AuthValidationError,
    validate_permissions,
    validate_role,
    validate_scope_correctness,
    validate_scope_list,
    validate_token_format,
    validate_user_id,
)


class AuthError(RuntimeError):
    """Raised when request authentication fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_type = "AUTH"


@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    role: str
    permissions: tuple[str, ...]
    allowed_ips: tuple[str, ...]
    allowed_run_ids: tuple[str, ...]


DEFAULT_TOKEN_MAP: dict[str, dict[str, Any]] = {
    "tk_admin_admin_full": {
        "user_id": "admin-user",
        "role": "admin",
        "permissions": ["devices:read", "evidence:read", "runs:read"],
        "allowed_ips": [],
        "allowed_run_ids": [],
    },
    "tk_reader_reader_full": {
        "user_id": "reader-user",
        "role": "reader",
        "permissions": ["devices:read", "evidence:read", "runs:read"],
        "allowed_ips": [],
        "allowed_run_ids": [],
    },
    "tk_restricted_restricted_scope": {
        "user_id": "restricted-user",
        "role": "restricted",
        "permissions": ["device:read", "evidence:read", "runs:read"],
        "allowed_ips": ["10.0.0.1"],
        "allowed_run_ids": ["run-002"],
    },
    "tk_unknown_unknown_full": {
        "user_id": "unknown-role-user",
        "role": "unknown",
        "permissions": ["devices:read"],
        "allowed_ips": [],
        "allowed_run_ids": [],
    },
}


class TokenAuthProvider:
    """Deterministic token provider with fail-closed identity validation."""

    def __init__(
        self,
        token_map: Mapping[str, Mapping[str, Any]] | None = None,
        allowed_roles: tuple[str, ...] = ("admin", "reader", "restricted"),
    ) -> None:
        self._token_map: Mapping[str, Mapping[str, Any]] = token_map or DEFAULT_TOKEN_MAP
        self._allowed_roles = allowed_roles

    @staticmethod
    def _extract_bearer_token(request: Request) -> str:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.strip():
            raise AuthError(code="AUTH_MISSING_TOKEN", message="missing authorization token")

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].strip().lower() != "bearer":
            raise AuthError(code="AUTH_INVALID_TOKEN", message="authorization header must use Bearer token")

        token = parts[1].strip()
        if not token:
            raise AuthError(code="AUTH_INVALID_TOKEN", message="bearer token is empty")
        return token

    def authenticate(self, request: Request) -> AuthIdentity:
        try:
            token = validate_token_format(self._extract_bearer_token(request))
        except AuthValidationError as exc:
            raise AuthError(code="AUTH_INVALID_TOKEN", message=str(exc)) from exc

        payload = self._token_map.get(token)
        if payload is None:
            raise AuthError(code="AUTH_INVALID_TOKEN", message="token is not recognized")

        try:
            user_id = validate_user_id(payload.get("user_id"))
            role = validate_role(payload.get("role"), self._allowed_roles)
            permissions = validate_permissions(payload.get("permissions"))
            allowed_ips = validate_scope_list(payload.get("allowed_ips"), "allowed_ips")
            allowed_run_ids = validate_scope_list(payload.get("allowed_run_ids"), "allowed_run_ids")
            validate_scope_correctness(role=role, allowed_ips=allowed_ips, allowed_run_ids=allowed_run_ids)
        except AuthValidationError as exc:
            raise AuthError(code="AUTH_INVALID_TOKEN", message=str(exc)) from exc

        return AuthIdentity(
            user_id=user_id,
            role=role,
            permissions=permissions,
            allowed_ips=allowed_ips,
            allowed_run_ids=allowed_run_ids,
        )
