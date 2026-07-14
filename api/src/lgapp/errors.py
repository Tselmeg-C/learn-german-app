"""RFC 9457 problem responses.

Every error the API returns has the same shape, so the client has exactly one error
contract to code against.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CONTENT_TYPE = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        type_: str = "about:blank",
        **extra: Any,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title
        self.detail = detail
        self.type_ = type_
        self.extra = extra


def _problem(request: Request, status: int, title: str, detail: str | None, **extra: Any):
    body: dict[str, Any] = {
        "type": extra.pop("type_", "about:blank"),
        "title": title,
        "status": status,
        "instance": request.url.path,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=CONTENT_TYPE)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError):
        return _problem(request, exc.status, exc.title, exc.detail, type_=exc.type_, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException):
        return _problem(request, exc.status_code, str(exc.detail), None)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError):
        return _problem(
            request,
            422,
            "Validation failed",
            "The request body or parameters did not match the expected schema.",
            errors=exc.errors(),
        )
