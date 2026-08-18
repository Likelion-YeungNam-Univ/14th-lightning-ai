"""공통 의존성 — DB 세션, 사용자 세션(쿠키), 인증 게이트(F-1.3)."""

from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import UserSession

COOKIE_NAME = "assit_session"
_KST = ZoneInfo("Asia/Seoul")

DbDep = Annotated[Session, Depends(get_db)]


def utcnow() -> datetime:
    """DB DateTime 컬럼이 naive이므로 naive UTC로 통일한다."""
    return datetime.now(UTC).replace(tzinfo=None)


def now_kst() -> datetime:
    """서버 컨테이너 타임존과 무관하게 KST 벽시계 시각(naive)을 돌려준다.

    베팅 마감(C-6.1.4)·정산 배치 기준일처럼 "한국 시간 몇 시"가 의미인 값은
    컨테이너가 UTC로 떠도 어긋나면 안 된다(승래 리뷰 B-5) — naive로 맞춰서
    같은 naive 값끼리만 비교하는 이 프로젝트의 다른 datetime과 섞어도 안전하다.
    """
    return datetime.now(_KST).replace(tzinfo=None)


def today_kst() -> date:
    return now_kst().date()


def get_current_session(request: Request, db: DbDep) -> UserSession:
    """쿠키의 세션을 로드한다. 없거나 만료면 401 no_session — 프론트는 POST /session으로 재발급."""
    session_id = request.cookies.get(COOKIE_NAME)
    session = db.get(UserSession, session_id) if session_id else None
    if session is None or session.expires_at < utcnow():
        raise AppError("no_session", "세션이 없거나 만료되었습니다", status_code=401)
    session.last_seen_at = utcnow()  # 재방문 지표(F-8.2)의 기준값
    db.commit()
    return session


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


def get_optional_session(request: Request, db: DbDep) -> UserSession | None:
    """무인증 엔드포인트용 — 세션이 있으면 활용(already_added 등), 없어도 동작."""
    session_id = request.cookies.get(COOKIE_NAME)
    session = db.get(UserSession, session_id) if session_id else None
    if session is not None and session.expires_at < utcnow():
        return None
    return session


OptionalSession = Annotated[UserSession | None, Depends(get_optional_session)]


def require_auth(session: CurrentSession) -> UserSession:
    """F-1.3 — 종목 추가·카드 저장 두 지점에서만 쓰는 인증 게이트."""
    if not session.authenticated:
        raise AppError("login_required", "로그인이 필요합니다", status_code=401)
    return session


AuthSession = Annotated[UserSession, Depends(require_auth)]
