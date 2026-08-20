from fastapi import APIRouter, Request, Response

from app.config import settings
from app.deps import COOKIE_NAME, DbDep, RawSession
from app.errors import AppError
from app.schemas.sessions import (
    AccountLoginRequest,
    AccountResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    SignupRequest,
)
from app.services import accounts, ratelimit

router = APIRouter(tags=["auth"])

LOGIN_ATTEMPTS_PER_MINUTE = 10  # IP당 — 무차별 대입 방어 (용어 풀이 레이트리밋 모듈 재사용)


def _check_login_rate(request: Request) -> None:
    key = f"auth:{request.client.host if request.client else 'unknown'}"
    if not ratelimit.allow(key, LOGIN_ATTEMPTS_PER_MINUTE):
        raise AppError(
            "rate_limited", "시도가 너무 잦습니다. 잠시 후 다시 시도해주세요", status_code=429
        )


@router.post("/auth/signup", response_model=AccountResponse)
def signup(
    body: SignupRequest, request: Request, session: RawSession, db: DbDep
) -> AccountResponse:
    """#74 — 실계정 가입 + 즉시 로그인. 지금까지의 익명 활동(종목·포인트)이 계정으로 승계된다."""
    _check_login_rate(request)
    user = accounts.signup(
        db, session, login_id=body.login_id, password=body.password, nickname=body.nickname
    )
    return AccountResponse(login_id=user.login_id, nickname=user.nickname)


@router.post("/auth/login", response_model=AccountResponse)
def account_login(
    body: AccountLoginRequest, request: Request, session: RawSession, db: DbDep
) -> AccountResponse:
    """#74 — 실계정 로그인. 쿠키가 바뀌었어도(다른 기기·삭제 후) 계정 데이터가 그대로 돌아온다."""
    _check_login_rate(request)
    user = accounts.login(db, session, login_id=body.login_id, password=body.password)
    return AccountResponse(login_id=user.login_id, nickname=user.nickname)


@router.post("/auth/mock-login", response_model=LoginResponse)
def mock_login(body: LoginRequest, session: RawSession, db: DbDep) -> LoginResponse:
    """F-1.2 — 사전 설정 계정(환경 변수)과 일치하면 세션에 authenticated를 세운다.

    실계정(#74)과 공존하는 시연·테스트용 경로. 가입·비밀번호 재설정은 여기 없다(명세).
    """
    if not settings.mock_login_id or not settings.mock_login_pw:
        raise AppError("login_not_configured", "모의 로그인 계정이 설정되지 않았습니다", 500)
    if body.id != settings.mock_login_id or body.password != settings.mock_login_pw:
        raise AppError("invalid_credentials", "아이디 또는 비밀번호가 일치하지 않습니다", 401)

    session.authenticated = True
    db.commit()
    return LoginResponse(authenticated=True)


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(session: RawSession, response: Response, db: DbDep) -> LogoutResponse:
    """확정사항 16절 — 쿠키 만료 + 실계정 연결 해제(#74).

    RawSession인 이유: CurrentSession은 주인 세션으로 치환된 뒤라 user_id를 지워도
    이 브라우저의 쿠키 세션에는 연결이 남는다. 세션 행·계정 데이터는 지우지 않는다.
    """
    accounts.logout(db, session)
    response.delete_cookie(COOKIE_NAME, samesite="lax")
    return LogoutResponse(logged_out=True)
