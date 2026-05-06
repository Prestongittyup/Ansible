"""Governance middleware enforcing pre-query rate and request-shape policy."""

from __future__ import annotations

from typing import Mapping, Sequence, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from legacy.api_layer.api_contracts import build_error_envelope, build_request_id
from legacy.api_layer.api_validator import validate_response_envelope
from legacy.governance_layer.request_policy_engine import GovernancePolicyError, RequestPolicyEngine


def _request_id(request: Request) -> str:
    query_pairs: Sequence[Tuple[str, str]] = [(key, value) for key, value in request.query_params.multi_items()]
    return build_request_id(request.method, request.url.path, query_pairs)


def install_governance_middleware(app: FastAPI, policy_engine: RequestPolicyEngine) -> None:
    """Install governance middleware that blocks requests before route execution."""

    @app.middleware("http")
    async def governance_guard(request: Request, call_next):
        request_id = _request_id(request)
        access_context = getattr(request.state, "access_context", None)

        try:
            decision = policy_engine.evaluate(request=request, access_context=access_context)
            request.state.governance_context = {
                "endpoint": decision.endpoint,
                "requested_limit": decision.requested_limit,
                "requested_offset": decision.requested_offset,
            }
        except GovernancePolicyError as exc:
            safe_context: Mapping[str, str]
            if isinstance(access_context, Mapping):
                safe_context = {
                    "user_id": str(access_context.get("user_id", "anonymous")) or "anonymous",
                    "role": str(access_context.get("role", "anonymous")) or "anonymous",
                    "policy_applied": str(access_context.get("policy_applied", "governance_failed"))
                    or "governance_failed",
                }
            else:
                safe_context = {
                    "user_id": "anonymous",
                    "role": "anonymous",
                    "policy_applied": "governance_failed",
                }

            status_code = 429 if exc.code in {"GOV_RATE_LIMIT_EXCEEDED", "GOV_QUOTA_EXCEEDED", "GOV_BOUNDS_EXCEEDED"} else 403
            envelope = build_error_envelope(
                request_id=request_id,
                code=exc.code,
                message=exc.message,
                error_type=exc.error_type,
                access_context=dict(safe_context),
            )
            validate_response_envelope(envelope)
            return JSONResponse(status_code=status_code, content=envelope)

        response = await call_next(request)
        return response
