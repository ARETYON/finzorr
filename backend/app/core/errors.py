"""Stable machine-readable error codes on the REST envelope.

Clients need to branch on failure kinds without parsing prose. Every error
response carries `{detail, code, request_id}`: `AppError` raises with an
explicit code; plain HTTPExceptions get a generic code derived from status.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal",
}


class AppError(HTTPException):
    """HTTPException with an explicit stable error code."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code, detail)
        self.code = code


def install_error_handlers(app: FastAPI) -> None:
    # Registered on the STARLETTE base class: the router raises it directly
    # for unknown paths (404) and wrong methods (405), and handler lookup
    # walks the exception's MRO — registering only the FastAPI subclass
    # lets those two escape the envelope.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", "") or _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": code,
                "request_id": getattr(request.state, "request_id", ""),
            },
            headers=getattr(exc, "headers", None) or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.errors(),
                "code": "validation_error",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
