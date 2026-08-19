from fastapi import APIRouter

from app.deps import DbDep, OptionalSession
from app.schemas.cards import CardListResponse
from app.services.cards import list_cards

router = APIRouter(tags=["cards"])


@router.get("/cards", response_model=CardListResponse)
def cards(tab: str, stock_code: str, db: DbDep, session: OptionalSession) -> CardListResponse:
    """F-6.1 — 탭 × 종목 카드 목록 + 시트용 필드 동봉(카드 상세 엔드포인트 없음).

    market 파라미터는 받지 않는다 — 종목코드가 구분을 결정한다(불변식 11).
    비로그인 호출 가능, is_saved는 저장 기능(#8) 전까지 false 고정.
    """
    return CardListResponse(**list_cards(db, session, tab, stock_code))
