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
    result = points_service.charge_points(
        db, session, order_id=body.order_id, payment_key=body.payment_key, amount=body.amount
    )
    return PointChargeResponse(**result)
