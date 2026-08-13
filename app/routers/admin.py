from typing import Annotated

from fastapi import APIRouter, Header

from app.config import settings
from app.deps import CurrentSession, DbDep
from app.errors import AppError
from app.schemas.sessions import ResetDemoResponse
from app.services.sessions import reset_demo

router = APIRouter(tags=["admin"])


@router.post("/admin/reset-demo", response_model=ResetDemoResponse)
def reset_demo_endpoint(
    session: CurrentSession,
    db: DbDep,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> ResetDemoResponse:
    """F-1.5 — 리허설 후 첫 진입 화면 재현. ADMIN_TOKEN 헤더로 보호(확정사항 4절)."""
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise AppError("admin_token_invalid", "관리자 토큰이 올바르지 않습니다", 401)
    return ResetDemoResponse(**reset_demo(db, session))
