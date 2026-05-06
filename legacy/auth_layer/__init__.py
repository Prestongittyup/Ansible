"""Sprint 6 access control layer for API-boundary authentication and authorization."""

from legacy.auth_layer.auth_middleware import install_auth_middleware
from legacy.auth_layer.auth_provider import AuthError, AuthIdentity, TokenAuthProvider
from legacy.auth_layer.auth_policies import AuthorizationError

__all__ = [
    "install_auth_middleware",
    "AuthError",
    "AuthIdentity",
    "TokenAuthProvider",
    "AuthorizationError",
]
