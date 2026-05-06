"""Fail-closed validators for auth token, role, policy, and scope contracts."""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

TOKEN_PATTERN = re.compile(r"^tk_[A-Za-z0-9_-]+_[A-Za-z0-9_-]+_[A-Za-z0-9_-]+$")


class AuthValidationError(RuntimeError):
    """Raised when authentication or authorization metadata is invalid."""


def _raise(message: str) -> None:
    raise AuthValidationError(message)


def validate_token_format(token: Any) -> str:
    if not isinstance(token, str) or not token.strip():
        _raise("token must be a non-empty string")
    normalized = token.strip()
    if TOKEN_PATTERN.fullmatch(normalized) is None:
        _raise("token format is invalid")
    return normalized


def validate_user_id(user_id: Any) -> str:
    if not isinstance(user_id, str) or not user_id.strip():
        _raise("user_id must be a non-empty string")
    return user_id.strip()


def validate_role(role: Any, allowed_roles: Iterable[str]) -> str:
    if not isinstance(role, str) or not role.strip():
        _raise("role must be a non-empty string")
    normalized = role.strip()
    allowed = set(allowed_roles)
    if normalized not in allowed:
        _raise(f"unknown role: {normalized}")
    return normalized


def validate_permissions(permissions: Any) -> tuple[str, ...]:
    if permissions is None:
        return tuple()
    if not isinstance(permissions, (list, tuple)):
        _raise("permissions must be a list of strings")

    normalized: list[str] = []
    for permission in permissions:
        if not isinstance(permission, str) or not permission.strip():
            _raise("permission values must be non-empty strings")
        normalized.append(permission.strip())
    return tuple(normalized)


def validate_scope_list(values: Any, field_name: str) -> tuple[str, ...]:
    if values is None:
        return tuple()
    if not isinstance(values, (list, tuple)):
        _raise(f"{field_name} must be a list of strings")

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            _raise(f"{field_name} contains invalid value")
        normalized.append(value.strip())
    return tuple(normalized)


def validate_policy_exists(policy_name: Any) -> str:
    if not isinstance(policy_name, str) or not policy_name.strip():
        _raise("policy_applied must be a non-empty string")
    return policy_name.strip()


def validate_scope_correctness(
    role: str,
    allowed_ips: Sequence[str],
    allowed_run_ids: Sequence[str],
) -> None:
    if role == "restricted" and len(allowed_ips) == 0 and len(allowed_run_ids) == 0:
        _raise("restricted role requires at least one allowed scope entry")
