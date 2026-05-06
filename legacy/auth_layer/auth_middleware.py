"""Authentication and authorization middleware for API-boundary enforcement."""

from __future__ import annotations

from typing import Sequence, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from legacy.api_layer.api_contracts import build_error_envelope, build_request_id, build_response_envelope
from legacy.api_layer.api_validator import validate_response_envelope
from legacy.auth_layer.auth_policies import AuthorizationError, authorize_request
from legacy.auth_layer.auth_provider import AuthError, TokenAuthProvider


def _request_id(request: Request) -> str:
    query_pairs: Sequence[Tuple[str, str]] = [(key, value) for key, value in request.query_params.multi_items()]
    return build_request_id(request.method, request.url.path, query_pairs)


def _anonymous_context(policy_applied: str) -> dict[str, str]:
    return {
        "user_id": "anonymous",
        "role": "anonymous",
        "policy_applied": policy_applied,
    }


def install_auth_middleware(app: FastAPI, provider: TokenAuthProvider) -> None:
    """Install fail-closed auth middleware on the API app."""

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        request_id = _request_id(request)

        try:
            identity = provider.authenticate(request)
            access_context = authorize_request(identity, request)
            request.state.access_context = access_context
        except AuthError as exc:
            envelope = build_error_envelope(
                request_id=request_id,
                code=exc.code,
                message=exc.message,
                error_type=exc.error_type,
                access_context=_anonymous_context(policy_applied="auth_failed"),
            )
            validate_response_envelope(envelope)
            return JSONResponse(status_code=401, content=envelope)
        except AuthorizationError as exc:
            envelope = build_error_envelope(
                request_id=request_id,
                code=exc.code,
                message=exc.message,
                error_type=exc.error_type,
                access_context=exc.access_context,
            )
            validate_response_envelope(envelope)
            return JSONResponse(status_code=403, content=envelope)

        response = await call_next(request)
        return response
