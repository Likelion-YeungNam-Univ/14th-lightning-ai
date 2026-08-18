"""F-7.1 — 에러 응답 규격: 모든 에러는 {code, message, details}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """서비스 로직에서 던지는 표준 예외."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict | list | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def _body(code: str, message: str, details: dict | list | None = None) -> dict:
    return {"code": code, "message": message, "details": details}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body("http_error", str(exc.detail), None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_body("validation_error", "요청 형식이 올바르지 않습니다", _safe_errors(exc)),
        )


def _safe_errors(exc: RequestValidationError) -> list[dict]:
    """커스텀 `field_validator`가 ValueError를 던지면 pydantic이 ctx.error에 그 예외
    객체를 그대로 담아 JSON 직렬화가 깨진다 — 문자열로 바꿔서 응답 가능하게 만든다."""
    errors = []
    for e in exc.errors():
        e = dict(e)
        ctx = e.get("ctx")
        if isinstance(ctx, dict) and "error" in ctx:
            e["ctx"] = {**ctx, "error": str(ctx["error"])}
        errors.append(e)
    return errors
