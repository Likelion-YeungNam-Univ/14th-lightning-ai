"""F-1 — 세션 발급·기본 종목 프로비저닝·데모 초기화."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.deps import utcnow
from app.models import SavedCard, SessionStock, UserSession
from app.services.stocks import pick_default_stocks

logger = logging.getLogger(__name__)


def registered_stock_codes(db: Session, session_id: str) -> list[str]:
    rows = (
        db.query(SessionStock.stock_code)
        .filter(SessionStock.session_id == session_id)
        .order_by(SessionStock.display_order)
        .all()
    )
    return [code for (code,) in rows]


def provision_default_stocks(db: Session, session: UserSession) -> None:
    """F-3.8 — 국내 기본 종목 프로비저닝. is_default로 종목 추가 전환율(핵심 지표) 산출."""
    defaults = pick_default_stocks(db)
    if not defaults:  # 종목 마스터가 비어 있음 — 시드 미복원. 조용히 넘기지 않고 로그로 알린다
        logger.warning(
            "기본 종목 0건 — stock_master가 비어 있습니다. scripts/restore_seed.sh 또는 "
            "sync_stock_master를 실행하세요 (session=%s)",
            session.id,
        )
    for order, stock in enumerate(defaults):
        db.add(
            SessionStock(
                session_id=session.id,
                stock_code=stock.stock_code,
                display_order=order,
                is_default=True,
            )
        )


def ensure_session(db: Session, session_id: str | None) -> tuple[UserSession, bool]:
    """F-1.1 — 유효한 세션이 있으면 재사용(멱등), 없으면 발급 + 기본 종목 4개."""
    if session_id:
        existing = db.get(UserSession, session_id)
        if existing is not None and existing.expires_at >= utcnow():
            return existing, False

    session = UserSession(
        id=uuid.uuid4().hex,
        expires_at=utcnow() + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    db.flush()
    provision_default_stocks(db, session)
    db.commit()
    return session, True


def reset_demo(db: Session, session: UserSession) -> dict:
    """F-1.5 — 요청 세션의 종목·저장 카드·마지막 종목을 지우고 기본 종목 4개로 되돌린다."""
    db.query(SessionStock).filter(SessionStock.session_id == session.id).delete()
    deleted_cards = db.query(SavedCard).filter(SavedCard.session_id == session.id).delete()
    session.last_stock_domestic = None
    session.last_stock_overseas = None
    session.authenticated = False
    provision_default_stocks(db, session)
    db.commit()
    return {
        "stocks": registered_stock_codes(db, session.id),
        "deleted_saved_cards": deleted_cards,
    }
