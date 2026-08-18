"""C-8 — 포인트 원장. 잔액은 컬럼이 아니라 point_ledger 합계로만 계산한다(C-8.3).

충전은 토스페이먼츠 테스트 키로 결제창→인증→승인 API 흐름을 그대로 타되, 실제 결제는
일어나지 않는다(C-8.2.0). 서버가 승인 API 응답을 확인한 뒤에만 적립한다(C-8.2) — 클라이언트
콜백만으로 적립하지 않는다.

**불변식 1 예외 문서화(승래 리뷰)**: `charge_points`는 토스 승인 API를 요청 경로에서
동기 호출한다. 결제 승인은 사용자가 결제창에서 돌아온 그 순간 즉시 확인해야 의미가
있는 본질적으로 동기인 흐름이라, 배치로 옮길 수 없다 — `POST /terms/explain`과 같은
성격의 예외로 취급한다.
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


def lock_session(db: Session, session_id: str) -> UserSession:
    """승래 리뷰 B-4 — 잔액 확인·차감을 한 트랜잭션 안에서 세션 행 잠금으로 직렬화한다.

    베팅·방 생성·충전·기프티콘 교환 등 잔액을 건드리는 모든 진입점에서 가장 먼저
    부른다. sqlite(테스트)에서는 FOR UPDATE가 no-op이라 락 효과는 없지만 쿼리 자체는
    동작한다 — 운영 Postgres에서만 실제로 직렬화된다.
    """
    return db.query(UserSession).filter(UserSession.id == session_id).with_for_update().one()


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
    ref_key: str | None = None,
) -> PointLedger:
    """적립은 양수, 차감은 음수(C-8.3). 커밋은 호출자 책임 — 트랜잭션(C-8.3.1) 단위로 묶는다."""
    entry = PointLedger(
        session_id=session_id,
        kind=kind,
        amount=amount,
        ref_type=ref_type,
        ref_id=ref_id,
        ref_key=ref_key,
    )
    db.add(entry)
    return entry


def _check_test_key_configured() -> None:
    """강력 권고(승래 리뷰) — 시크릿 키가 테스트 키(`test_sk_` 접두사)가 아니면 아예
    거부한다. env 한 줄 실수로 실결제가 붙는 걸 막는 가장 싼 장치다(C-8.2.0). DB에
    아무것도 쓰기 전에 먼저 확인한다."""
    if not settings.toss_secret_key:
        raise AppError("payment_not_configured", "결제 설정이 되어있지 않습니다", 500)
    if not settings.toss_secret_key.startswith("test_sk_"):
        raise AppError(
            "payment_not_configured", "테스트 키가 아닌 결제 키는 사용할 수 없습니다", 500
        )


def _confirm_toss_payment(payment_key: str, order_id: str, amount: int) -> dict:
    """토스페이먼츠 결제 승인 API 호출 — 서버가 직접 확인한다(C-8.2)."""
    try:
        resp = httpx.post(
            TOSS_CONFIRM_URL,
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            auth=(settings.toss_secret_key, ""),
            timeout=10.0,
        )
    except httpx.HTTPError as e:  # B-6 — 네트워크 오류가 raw 500으로 새지 않게
        raise AppError("payment_unavailable", "결제 서버에 연결할 수 없습니다", 502) from e
    try:
        body = resp.json()
    except ValueError as e:  # B-6 — 비JSON 응답도 raw 500으로 새지 않게
        raise AppError("payment_unavailable", "결제 서버 응답이 올바르지 않습니다", 502) from e
    if resp.status_code != 200:
        raise AppError("payment_failed", "결제 승인에 실패했습니다", 400, body)
    return body


def charge_points(
    db: Session, session: UserSession, *, order_id: str, payment_key: str, amount: int
) -> dict:
    """C-8.2 — 충전. 승인 전 상한 확인(C-8.2.1) → 승인 API 호출 → 적립을 한 트랜잭션으로.

    **B-3 리뷰 반영**: `order_id`를 원장에 미리 심어(`ref_key` unique) 같은 결제를
    두 번 적립하지 못하게 막고, 승인 응답의 `totalAmount`·`status`·`orderId`를 직접
    검증한 뒤 그 값으로만 적립한다 — 요청 body의 amount를 그대로 믿지 않는다.
    """
    _check_test_key_configured()
    if amount not in CHARGE_PRODUCTS:
        raise AppError("invalid_amount", "충전 금액은 5,000/10,000/30,000P 중 하나여야 합니다", 400)

    locked = lock_session(db, session.id)  # B-4

    current = balance(db, locked.id)
    if current + amount > POINT_CAP:
        raise AppError(
            "point_cap_exceeded",
            f"보유 포인트 상한({POINT_CAP}P)을 넘습니다",
            400,
            {"current": current, "cap": POINT_CAP},
        )

    placeholder = add_ledger_entry(db, locked.id, "charge", 0, ref_type="payment", ref_key=order_id)
    try:
        db.flush()  # ref_key unique 위반 — 같은 order_id 재요청(B-3)
    except Exception as e:
        db.rollback()
        raise AppError("already_charged", "이미 처리된 결제입니다", 409) from e
    db.commit()  # 가드 행을 먼저 확정해 동시 재요청도 여기서 걸리게 한다

    try:
        confirmed = _confirm_toss_payment(payment_key, order_id, amount)
    except AppError:
        db.delete(placeholder)  # 승인 실패 — 같은 order_id로 재시도 가능하게 가드 해제
        db.commit()
        raise

    if confirmed.get("status") != "DONE" or confirmed.get("orderId") != order_id:
        db.delete(placeholder)
        db.commit()
        raise AppError("payment_failed", "결제 승인 응답이 올바르지 않습니다", 400, confirmed)

    confirmed_amount = confirmed.get("totalAmount")
    if confirmed_amount not in CHARGE_PRODUCTS:
        db.delete(placeholder)
        db.commit()
        raise AppError("payment_failed", "결제 승인 금액이 올바르지 않습니다", 400, confirmed)

    placeholder.amount = confirmed_amount  # 응답 금액으로만 적립 — 요청 body를 신뢰하지 않는다
    db.commit()
    return {"balance": balance(db, locked.id), "charged": confirmed_amount}


def pizza_progress(held: int) -> dict:
    return {
        "held": held,
        "target": GIFTICON_PRICE,
        "percent": min(100, round(held / GIFTICON_PRICE * 100)),
    }


def get_summary(db: Session, session: UserSession) -> dict:
    held = balance(db, session.id)
    return {"balance": held, "pizza_progress": pizza_progress(held)}
