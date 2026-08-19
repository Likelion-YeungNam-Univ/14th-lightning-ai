"""C-9 — 기프티콘 교환 API. 로그인 필요."""

from fastapi import APIRouter

from app.deps import AuthSession, DbDep
from app.schemas.gifticons import GifticonExchangeResponse
from app.services import gifticons as gifticon_service

router = APIRouter(tags=["gifticons"])


@router.post("/me/gifticons", response_model=GifticonExchangeResponse)
def exchange_gifticon(session: AuthSession, db: DbDep) -> GifticonExchangeResponse:
    return GifticonExchangeResponse(**gifticon_service.exchange_gifticon(db, session))
