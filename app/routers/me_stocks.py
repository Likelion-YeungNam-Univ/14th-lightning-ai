from fastapi import APIRouter, BackgroundTasks

from app.collectors.runner import collect_on_stock_added
from app.deps import AuthSession, CurrentSession, DbDep
from app.schemas.stocks import (
    AddedStock,
    MyStockItem,
    MyStocksResponse,
    StockAddRequest,
    StockAddResponse,
    StockDeleteResponse,
    StockOrderRequest,
    StockOrderResponse,
)
from app.services import stocks as stock_service

router = APIRouter(tags=["my-stocks"])


@router.get("/me/stocks", response_model=MyStocksResponse)
def my_stocks(market: str, session: CurrentSession, db: DbDep) -> MyStocksResponse:
    """F-3.4 — 구분별 종목 목록 전량 반환 (칩 7개 분기는 프론트 표시 규칙)."""
    pairs = stock_service.list_my_stocks(db, session, market)
    return MyStocksResponse(
        items=[
            MyStockItem(
                stock_code=master.stock_code,
                name=master.name,
                market=master.market,
                display_order=link.display_order,
                is_default=link.is_default,
            )
            for link, master in pairs
        ]
    )


@router.post("/me/stocks", response_model=StockAddResponse)
def add_stocks(
    body: StockAddRequest, session: AuthSession, db: DbDep, background: BackgroundTasks
) -> StockAddResponse:
    """F-3.5 — 벌크 등록(멱등, 구분별 상한 30). 로그인 요구 지점(F-1.3).

    응답에 market을 포함한다(F-3.5.1) — 다른 구분에 들어간 종목의 안내 근거.
    새 종목은 응답 후 온디맨드 수집(F-4.10) — 요청 경로에서 외부 API를 부르지 않는다.
    """
    added, skipped = stock_service.add_stocks(db, session, body.stock_codes)
    for stock in added:
        background.add_task(collect_on_stock_added, stock.stock_code)
    return StockAddResponse(
        added=[AddedStock(stock_code=s.stock_code, name=s.name, market=s.market) for s in added],
        already_registered=skipped,
    )


@router.delete("/me/stocks/{stock_code}", response_model=StockDeleteResponse)
def delete_stock(stock_code: str, session: CurrentSession, db: DbDep) -> StockDeleteResponse:
    """F-3.6 — 삭제. 백엔드는 전량 삭제 허용(마지막 1개 안내는 프론트). 저장 카드 유지."""
    remaining = stock_service.remove_stock(db, session, stock_code)
    return StockDeleteResponse(remaining=remaining)


@router.put("/me/stocks/order", response_model=StockOrderResponse)
def reorder(body: StockOrderRequest, session: CurrentSession, db: DbDep) -> StockOrderResponse:
    """F-3.7 — 노출 순서 변경. 구분 단위, 전체 목록과 일치하지 않으면 400."""
    ordered = stock_service.reorder_stocks(db, session, body.market, body.stock_codes)
    return StockOrderResponse(stocks=ordered)
