"""Read-only API routes for query layer access."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from legacy.api_layer.api_contracts import build_error_envelope, build_request_id, build_response_envelope
from legacy.api_layer.api_validator import (
    ApiValidationError,
    validate_device_request,
    validate_device_response,
    validate_devices_request,
    validate_devices_response,
    validate_evidence_request,
    validate_evidence_response,
    validate_response_envelope,
    validate_run_request,
)
from legacy.query_layer.query_service import QueryService


def _request_id(request: Request) -> str:
    query_pairs: Sequence[Tuple[str, str]] = [(key, value) for key, value in request.query_params.multi_items()]
    return build_request_id(request.method, request.url.path, query_pairs)


def _access_context(request: Request) -> Dict[str, str]:
    value = getattr(request.state, "access_context", None)
    if isinstance(value, dict):
        return value
    return {
        "user_id": "anonymous",
        "role": "anonymous",
        "policy_applied": "missing",
    }


def _error_response(
    request: Request,
    request_id: str,
    message: str,
    status_code: int,
    code: str = "AUTHZ_VALIDATION_ERROR",
    error_type: str = "AUTHZ",
) -> JSONResponse:
    envelope = build_error_envelope(
        request_id=request_id,
        code=code,
        message=message,
        error_type=error_type,
        access_context=_access_context(request),
    )
    validate_response_envelope(envelope)
    return JSONResponse(status_code=status_code, content=envelope)


def _ok_response(request: Request, request_id: str, data: Any) -> JSONResponse:
    envelope = build_response_envelope(request_id=request_id, data=data, access_context=_access_context(request))
    validate_response_envelope(envelope)
    return JSONResponse(status_code=200, content=envelope)


def create_router(query_service: QueryService) -> APIRouter:
    router = APIRouter()

    @router.get("/devices")
    def get_devices(
        request: Request,
        vendor: str | None = None,
        execution_state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        request_id = _request_id(request)
        try:
            params = validate_devices_request(vendor=vendor, execution_state=execution_state, limit=limit, offset=offset)
            if params["mode"] == "vendor":
                records = query_service.list_devices_by_vendor(params["vendor"])
            else:
                records = query_service.list_devices_by_execution_state(params["execution_state"])

            page = records[params["offset"] : params["offset"] + params["limit"]]
            validated = validate_devices_response(page, mode=params["mode"])
            return _ok_response(request=request, request_id=request_id, data=validated)
        except ApiValidationError as exc:
            return _error_response(request=request, request_id=request_id, message=str(exc), status_code=400)

    @router.get("/device/{ip_address}")
    def get_device(request: Request, ip_address: str) -> JSONResponse:
        request_id = _request_id(request)
        try:
            ip = validate_device_request(ip_address)
            record = query_service.get_device(ip)
            validated = validate_device_response(record)
            return _ok_response(request=request, request_id=request_id, data=validated)
        except ApiValidationError as exc:
            return _error_response(request=request, request_id=request_id, message=str(exc), status_code=400)

    @router.get("/evidence")
    def get_evidence(
        request: Request,
        ip_address: str | None = None,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        request_id = _request_id(request)
        try:
            params = validate_evidence_request(
                ip_address=ip_address,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                limit=limit,
                offset=offset,
            )
            if params["mode"] == "ip":
                records = query_service.get_validation_evidence(
                    params["ip_address"],
                    limit=params["limit"],
                    offset=params["offset"],
                )
            else:
                records = query_service.get_evidence_by_time_range(
                    params["start_timestamp"],
                    params["end_timestamp"],
                    limit=params["limit"],
                    offset=params["offset"],
                )

            validated = validate_evidence_response(records)
            return _ok_response(request=request, request_id=request_id, data=validated)
        except ApiValidationError as exc:
            return _error_response(request=request, request_id=request_id, message=str(exc), status_code=400)

    @router.get("/runs/{run_id}")
    def get_run_evidence(
        request: Request,
        run_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        request_id = _request_id(request)
        try:
            params = validate_run_request(run_id=run_id, limit=limit, offset=offset)
            records = query_service.get_evidence_by_run(
                params["run_id"],
                limit=params["limit"],
                offset=params["offset"],
            )
            validated = validate_evidence_response(records)
            return _ok_response(request=request, request_id=request_id, data=validated)
        except ApiValidationError as exc:
            return _error_response(request=request, request_id=request_id, message=str(exc), status_code=400)

    return router
