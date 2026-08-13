from fastapi import APIRouter

from app.deps import DbDep, OptionalSession
from app.schemas.stocks import PopularStockItem, StockSearchItem, StockSearchResponse
from app.services import stocks as stock_service

router = APIRouter(tags=["stocks"])


@router.get("/stocks/search", response_model=StockSearchResponse)
def search(q: str, market: str, db: DbDep, session: OptionalSession) -> StockSearchResponse:
    """F-3.3 — 현재 구분 한정 검색. 비로그인 호출 가능(already_added는 세션 있을 때만 의미)."""
    results, reason, registered = stock_service.search_stocks(db, q, market, session)
    return StockSearchResponse(
        items=[
            StockSearchItem(
                stock_code=s.stock_code,
                name=s.name,
                market=s.market,
                already_added=s.stock_code in registered,
            )
            for s in results
        ],
        reason=reason,
    )


@router.get("/stocks/popular", response_model=list[PopularStockItem])
def popular(market: str, db: DbDep, session: OptionalSession) -> list[PopularStockItem]:
    """확정사항 7절 — 종목 추가 창의 추천 칩 (국내: 시총 상위 8 보통주)."""
    registered = stock_service.registered_code_set(db, session)
    return [
        PopularStockItem(
            stock_code=s.stock_code,
            name=s.name,
            market=s.market,
            already_added=s.stock_code in registered,
        )
        for s in stock_service.popular_stocks(db, market)
    ]
