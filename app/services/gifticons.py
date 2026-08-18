"""C-9 — 피자 기프티콘 교환. 시연 범위는 발급 완료 화면까지, 실물 발급은 하지 않는다(C-9.1.5)."""

import secrets

from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.deps import now_kst
from app.errors import AppError
from app.models import GifticonOrder, UserSession
from app.services.points import GIFTICON_PRICE, add_ledger_entry, balance, lock_session


def _exchanged_this_month(db: Session, session_id: str) -> int:
    now = now_kst()  # 월 경계는 KST 기준(권고 반영) — UTC면 자정~새벽 9시가 전월로 샌다
    return (
        db.query(func.count())
        .select_from(GifticonOrder)
        .filter(
            GifticonOrder.session_id == session_id,
            GifticonOrder.status == "issued",
            extract("year", GifticonOrder.created_at) == now.year,
            extract("month", GifticonOrder.created_at) == now.month,
        )
        .scalar()
    )


def exchange_gifticon(db: Session, session: UserSession) -> dict:
    """C-9.1 — 차감과 발급을 한 트랜잭션으로(C-9.1.3). 월 1회 제한(C-9.1.1).

    세션 행을 잠근 뒤(승래 리뷰 B-2) 월 1회 제한·잔액을 확인한다 — 동시 요청이
    락 없이 각자 확인만 하고 둘 다 통과해 이중 교환·음수 잔액이 나던 버그를 막는다.
    같은 락이 더블클릭 같은 단순 재요청도 순서대로 직렬화해 자연히 막는다.
    """
    locked = lock_session(db, session.id)

    if _exchanged_this_month(db, locked.id) >= 1:
        raise AppError("gifticon_monthly_limit", "기프티콘 교환은 월 1회만 가능합니다", 400)

    current_balance = balance(db, locked.id)
    if current_balance < GIFTICON_PRICE:
        raise AppError("insufficient_points", "보유 포인트가 부족합니다", 400)

    add_ledger_entry(db, locked.id, "exchange", -GIFTICON_PRICE, ref_type="gifticon", ref_id=None)
    order = GifticonOrder(
        session_id=locked.id,
        points_used=GIFTICON_PRICE,
        status="issued",
        issued_code=f"DUMMY-{secrets.token_hex(4).upper()}",  # C-9.1.5 — 실물 미발급, 더미 코드
    )
    db.add(order)
    db.commit()
    return {
        "order_id": order.id,
        "points_used": order.points_used,
        "issued_code": order.issued_code,
        "balance": balance(db, locked.id),
    }
