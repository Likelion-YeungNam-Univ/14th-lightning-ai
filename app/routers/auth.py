from fastapi import APIRouter, Response

from app.config import settings
from app.deps import COOKIE_NAME, CurrentSession, DbDep
from app.errors import AppError
from app.schemas.sessions import LoginRequest, LoginResponse, LogoutResponse

router = APIRouter(tags=["auth"])


@router.post("/auth/mock-login", response_model=LoginResponse)
def mock_login(body: LoginRequest, session: CurrentSession, db: DbDep) -> LoginResponse:
    """F-1.2 — 사전 설정 계정(환경 변수)과 일치하면 세션에 authenticated를 세운다.

    가입·중복 확인·비밀번호 재설정·로그아웃은 만들지 않는다(명세).
    """
    if not settings.mock_login_id or not settings.mock_login_pw:
        raise AppError("login_not_configured", "모의 로그인 계정이 설정되지 않았습니다", 500)
    if body.id != settings.mock_login_id or body.password != settings.mock_login_pw:
        raise AppError("invalid_credentials", "아이디 또는 비밀번호가 일치하지 않습니다", 401)

    session.authenticated = True
    db.commit()
    return LoginResponse(authenticated=True)


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(session: CurrentSession, response: Response) -> LogoutResponse:
    """확정사항 16절 — F-1.2가 배제한 로그아웃을 시연 편의를 위해 추가.

    세션 행은 지우지 않고 쿠키만 만료시킨다 — 다음 POST /session이 새 세션을 발급한다.
    """
    response.delete_cookie(COOKIE_NAME, samesite="lax")
    return LogoutResponse(logged_out=True)
