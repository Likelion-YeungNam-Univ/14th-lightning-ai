"""C-9 — 기프티콘 교환 API. 로그인 필요."""

from fastapi import APIRouter

from app.deps import AuthSession, DbDep
from app.schemas.gifticons import GifticonExchangeResponse
from app.services import gifticons as gifticon_service

router = APIRouter(tags=["gifticons"])


@router.post("/me/gifticons", response_model=GifticonExchangeResponse)
def exchange_gifticon(session: AuthSession, db: DbDep) -> GifticonExchangeResponse:
    """C-9 — 포인트 23,000P → 피자 기프티콘 교환(시연용 — 실물 발송 없음). **로그인 필요.**

    에러: `insufficient_points`, 월 1회 제한 `gifticon_monthly_limit`
    """
    return GifticonExchangeResponse(**gifticon_service.exchange_gifticon(db, session))
