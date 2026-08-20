"""C-8 — 포인트 잔액/충전 API. 둘 다 로그인 필요."""

from fastapi import APIRouter

from app.deps import AuthSession, CurrentSession, DbDep
from app.schemas.points import PointBalanceResponse, PointChargeRequest, PointChargeResponse
from app.services import points as points_service

router = APIRouter(tags=["points"])


@router.get("/me/points", response_model=PointBalanceResponse)
def get_points(session: CurrentSession, db: DbDep) -> PointBalanceResponse:
    """C-11 — 세션만 있으면 조회 가능(로그인 불필요). 잔액은 원장 합계(C-8.3)."""
    return PointBalanceResponse(**points_service.get_summary(db, session))


@router.post("/me/points/charge", response_model=PointChargeResponse)
def charge_points(body: PointChargeRequest, session: AuthSession, db: DbDep) -> PointChargeResponse:
    """C-8.2 — 포인트 충전. **로그인 필요.** `amount`는 5,000/10,000/30,000만.

    두 가지 모드:
    - **테스트 모드**: `order_id`·`payment_key` 생략(amount만) → 결제 없이 즉시 충전
      (이슈 #83, QA·스웨거용)
    - **토스 결제**: 프론트 결제창에서 받은 `order_id`+`payment_key` 전달 → 서버가 토스에 승인
      확인 후 충전. 테스트 키(`test_sk_`)가 아니면 서버가 거부한다 — 실결제 원천 차단
    에러: `invalid_amount`, `payment_failed`, `payment_unavailable`(502), 같은 결제 재사용 시 멱등
    """
    result = points_service.charge_points(
        db, session, order_id=body.order_id, payment_key=body.payment_key, amount=body.amount
    )
    return PointChargeResponse(**result)
