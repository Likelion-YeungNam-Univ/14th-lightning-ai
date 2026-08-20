from fastapi import APIRouter, Request, Response

from app.config import settings
from app.deps import COOKIE_NAME, DbDep, resolve_primary
from app.models import AppUser
from app.schemas.sessions import SessionResponse
from app.services.sessions import ensure_session, registered_stock_codes

router = APIRouter(tags=["session"])


@router.post("/session", response_model=SessionResponse)
def create_session(request: Request, response: Response, db: DbDep) -> SessionResponse:
    """F-1.1 — 첫 진입 시 세션 발급 + 국내 기본 종목 4개. 유효 세션이 있으면 재사용(멱등).

    실계정 로그인 세션(#74)이면 응답 데이터는 주인 세션 기준 — 다른 API와 일치해야
    프론트 초기 화면이 어긋나지 않는다. 쿠키 자체는 원본 세션 id 그대로."""
    session, created = ensure_session(db, request.cookies.get(COOKIE_NAME))
    response.set_cookie(
        COOKIE_NAME,
        session.id,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",  # 프론트는 rewrite 프록시로 동일 출처(확정사항 4절)
    )
    effective = resolve_primary(db, session)
    user = db.get(AppUser, session.user_id) if session.user_id else None
    db.commit()  # _resolve_primary의 만료 연장 반영
    return SessionResponse(
        created=created,
        authenticated=effective.authenticated or session.user_id is not None,
        stocks=registered_stock_codes(db, effective.id),
        nickname=user.nickname if user else None,
    )
