"""C-8 — 포인트 원장. 잔액은 컬럼이 아니라 point_ledger 합계로만 계산한다(C-8.3).

충전은 토스페이먼츠 테스트 키로 결제창→인증→승인 API 흐름을 그대로 타되, 실제 결제는
일어나지 않는다(C-8.2.0). 서버가 승인 API 응답을 확인한 뒤에만 적립한다(C-8.2) — 클라이언트
콜백만으로 적립하지 않는다.
"""

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError
from app.models import PointLedger, UserSession

POINT_CAP = 30_000  # C-8.2.1
CHARGE_PRODUCTS = (5_000, 10_000, 30_000)  # C-8.2
GIFTICON_PRICE = 18_000  # 피자 한 판 (C-9.1) — 원가보다 낮추지 말 것(부록: 담합 방지 근거)
TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


def balance(db: Session, session_id: str) -> int:
    """C-8.3 — 원장 합계가 유일한 진실. 별도 컬럼을 신뢰하지 않는다."""
    total = (
        db.query(PointLedger)
        .filter(PointLedger.session_id == session_id)
        .with_entities(PointLedger.amount)
        .all()
    )
    return sum(a for (a,) in total)


def add_ledger_entry(
    db: Session,
    session_id: str,
    kind: str,
    amount: int,
    *,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> PointLedger:
    """적립은 양수, 차감은 음수(C-8.3). 커밋은 호출자 책임 — 트랜잭션(C-8.3.1) 단위로 묶는다."""
    entry = PointLedger(
        session_id=session_id, kind=kind, amount=amount, ref_type=ref_type, ref_id=ref_id
    )
    db.add(entry)
    return entry


def _confirm_toss_payment(payment_key: str, order_id: str, amount: int) -> dict:
    """토스페이먼츠 결제 승인 API 호출 — 서버가 직접 확인한다(C-8.2)."""
    if not settings.toss_secret_key:
        raise AppError("payment_not_configured", "결제 설정이 되어있지 않습니다", 500)
    resp = httpx.post(
        TOSS_CONFIRM_URL,
        json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
        auth=(settings.toss_secret_key, ""),
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise AppError("payment_failed", "결제 승인에 실패했습니다", 400, resp.json())
    return resp.json()


def charge_points(
    db: Session, session: UserSession, *, order_id: str, payment_key: str, amount: int
) -> dict:
    """C-8.2 — 충전. 승인 전 상한 확인(C-8.2.1) → 승인 API 호출 → 적립을 한 트랜잭션으로."""
    if amount not in CHARGE_PRODUCTS:
        raise AppError("invalid_amount", "충전 금액은 5,000/10,000/30,000P 중 하나여야 합니다", 400)

    current = balance(db, session.id)
    if current + amount > POINT_CAP:
        raise AppError(
            "point_cap_exceeded",
            f"보유 포인트 상한({POINT_CAP}P)을 넘습니다",
            400,
            {"current": current, "cap": POINT_CAP},
        )

    _confirm_toss_payment(payment_key, order_id, amount)  # 승인 확인 후에만 적립

    add_ledger_entry(db, session.id, "charge", amount, ref_type="payment", ref_id=None)
    db.commit()
    return {"balance": balance(db, session.id), "charged": amount}


def pizza_progress(held: int) -> dict:
    return {
        "held": held,
        "target": GIFTICON_PRICE,
        "percent": min(100, round(held / GIFTICON_PRICE * 100)),
    }


def get_summary(db: Session, session: UserSession) -> dict:
    held = balance(db, session.id)
    return {"balance": held, "pizza_progress": pizza_progress(held)}
