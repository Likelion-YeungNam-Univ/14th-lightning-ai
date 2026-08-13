from fastapi import APIRouter, Request, Response

from app.config import settings
from app.deps import COOKIE_NAME, DbDep
from app.schemas.sessions import SessionResponse
from app.services.sessions import ensure_session, registered_stock_codes

router = APIRouter(tags=["session"])


@router.post("/session", response_model=SessionResponse)
def create_session(request: Request, response: Response, db: DbDep) -> SessionResponse:
    """F-1.1 — 첫 진입 시 세션 발급 + 국내 기본 종목 4개. 유효 세션이 있으면 재사용(멱등)."""
    session, created = ensure_session(db, request.cookies.get(COOKIE_NAME))
    response.set_cookie(
        COOKIE_NAME,
        session.id,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",  # 프론트는 rewrite 프록시로 동일 출처(확정사항 4절)
    )
    return SessionResponse(
        created=created,
        authenticated=session.authenticated,
        stocks=registered_stock_codes(db, session.id),
    )
