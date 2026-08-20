"""#74 — 실계정 가입·로그인. 비밀번호는 표준 라이브러리 scrypt(솔트 포함)로만 저장한다.

설계(확정사항 19절): 계정은 '주인 세션(primary_session_id)'을 기억하는 포인터다.
- 가입: 현재 쿠키 세션이 그대로 주인이 된다 — 익명으로 쌓은 종목·포인트 승계
- 로그인: 현재 쿠키 세션에 user_id만 세운다 — deps._resolve_primary가 주인 세션으로 치환
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import utcnow
from app.errors import AppError
from app.models import AppUser, UserSession
from app.services.sessions import provision_default_stocks

# scrypt 파라미터 — 파이썬 문서 권장 상호작용 로그인 값 (~32MB 메모리, 수십 ms)
_N, _R, _P = 2**14, 8, 5


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=_N, r=_R, p=_P)
    return f"scrypt${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=_N, r=_R, p=_P)
    return hmac.compare_digest(digest.hex(), expected)  # 타이밍 공격 방어


def _fresh_primary(db: Session) -> UserSession:
    """이 쿠키 세션이 이미 다른 계정의 주인일 때 — 새 계정용 빈 주인 세션을 만든다.

    같은 세션을 두 계정의 주인으로 두면 포인트·종목이 계정 간에 공유돼 버린다(같은 브라우저에서
    가입→로그아웃→재가입 시나리오). 새 주인 세션에도 기본 종목을 채워 첫 화면이 비지 않게 한다."""
    fresh = UserSession(
        id=uuid.uuid4().hex,
        expires_at=utcnow() + timedelta(days=settings.session_ttl_days),
    )
    db.add(fresh)
    db.flush()
    provision_default_stocks(db, fresh)
    return fresh


def signup(
    db: Session, session: UserSession, *, login_id: str, password: str, nickname: str
) -> AppUser:
    """가입 + 즉시 로그인. 현재 세션이 주인 세션이 된다(익명 활동 승계).

    session은 반드시 raw(치환 전) 쿠키 세션 — deps.RawSession으로 주입할 것."""
    if session.user_id is not None:
        raise AppError("already_logged_in", "이미 로그인된 상태입니다", 409)
    already_primary = (
        db.query(AppUser).filter(AppUser.primary_session_id == session.id).first() is not None
    )
    primary = _fresh_primary(db) if already_primary else session
    user = AppUser(
        login_id=login_id,
        nickname=nickname.strip(),
        pw_hash=hash_password(password),
        primary_session_id=primary.id,
    )
    db.add(user)
    try:
        db.flush()  # UNIQUE(login_id) 위반을 커밋 전에 잡는다
    except IntegrityError:
        db.rollback()
        raise AppError("duplicate_login_id", "이미 사용 중인 아이디입니다", 409) from None
    session.user_id = user.id
    db.commit()
    return user


def login(db: Session, session: UserSession, *, login_id: str, password: str) -> AppUser:
    """비밀번호 검증 후 현재 쿠키 세션을 계정에 연결한다."""
    user = db.query(AppUser).filter(AppUser.login_id == login_id).one_or_none()
    # 계정이 없어도 해시 1회를 돌려 응답 시간으로 아이디 존재를 유추하지 못하게 한다
    stored = user.pw_hash if user else hash_password("dummy-timing-equalizer")
    if not verify_password(password, stored) or user is None:
        raise AppError("invalid_credentials", "아이디 또는 비밀번호가 일치하지 않습니다", 401)
    session.user_id = user.id
    db.commit()
    return user


def logout(db: Session, session: UserSession) -> None:
    """쿠키 세션과 계정의 연결만 끊는다 — 계정·주인 세션 데이터는 그대로."""
    session.user_id = None
    db.commit()
