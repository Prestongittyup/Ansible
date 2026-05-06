"""FastAPI server entrypoint for Sprint 5 read-only API layer."""

from __future__ import annotations

from fastapi import FastAPI

from legacy.api_layer.api_routes import create_router
from legacy.auth_layer.auth_middleware import install_auth_middleware
from legacy.auth_layer.auth_provider import TokenAuthProvider
from legacy.governance_layer.governance_middleware import install_governance_middleware
from legacy.governance_layer.request_policy_engine import RequestPolicyEngine
from legacy.query_layer.query_service import QueryService


def create_app(
    query_service: QueryService | None = None,
    auth_provider: TokenAuthProvider | None = None,
    policy_engine: RequestPolicyEngine | None = None,
) -> FastAPI:
    service = query_service
    if service is None:
        raise RuntimeError("query_service dependency is required for API server startup")

    provider = auth_provider or TokenAuthProvider()
    governance = policy_engine or RequestPolicyEngine()

    app = FastAPI(title="Sprint 5 Read-Only API Layer")
    # Register governance first so auth middleware executes before governance checks.
    install_governance_middleware(app, governance)
    install_auth_middleware(app, provider)
    app.include_router(create_router(service))
    return app
