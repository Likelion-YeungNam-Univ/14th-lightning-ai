"""F-7 — 저장 카드 API. POST/DELETE는 로그인 필수(F-1.3), GET은 세션 필요."""

from fastapi import APIRouter, Query

from app.deps import AuthSession, CurrentSession, DbDep
from app.models import SavedCard
from app.schemas.saved import (
    SavedCardAddRequest,
    SavedCardAddResponse,
    SavedCardDeleteResponse,
    SavedCardItem,
    SavedCardListResponse,
)
from app.services import saved as saved_service

router = APIRouter(tags=["saved-cards"])


def _to_item(row: SavedCard, stock_name: str | None) -> SavedCardItem:
    return SavedCardItem(
        card_id=row.source_item_id,
        tab=row.tab,
        stock_code=row.stock_code,
        stock_name=stock_name,
        saved_at=row.saved_at,
        snapshot=row.snapshot_json,
    )


@router.get("/me/saved-cards", response_model=SavedCardListResponse)
def list_saved(
    session: CurrentSession,
    db: DbDep,
    stock_code: str | None = None,
    for_: str | None = Query(default=None, alias="for"),
):
    """F-7.4·F-7.5 — 국내·해외 통합, 최근 저장 순, ?stock_code= 필터.

    ?for=attachment(C-5.1)는 베팅방 자료 카드 첨부 선택창이 쓰는 것과 같은
    엔드포인트임을 프론트에 알리는 표식일 뿐, 조회 범위는 동일하다(C-5.1.1).
    """
    rows = saved_service.list_saved(db, session, stock_code)
    return SavedCardListResponse(items=[_to_item(row, name) for row, name in rows])


@router.post("/me/saved-cards", response_model=SavedCardAddResponse)
def save_card(body: SavedCardAddRequest, session: AuthSession, db: DbDep):
    """F-7.1 — 저장(멱등). 미로그인 401 (F-1.3)."""
    row, already = saved_service.save_card(db, session, body.card_id, body.stock_code)
    stock_name = None
    from app.models import StockMaster

    stock = db.get(StockMaster, row.stock_code)
    if stock is not None:
        stock_name = stock.name
    return SavedCardAddResponse(item=_to_item(row, stock_name), already_saved=already)


@router.delete("/me/saved-cards/{card_id}", response_model=SavedCardDeleteResponse)
def unsave_card(card_id: int, session: AuthSession, db: DbDep):
    """F-7.2 — 해제(멱등). 스냅샷도 함께 삭제."""
    return SavedCardDeleteResponse(removed=saved_service.unsave_card(db, session, card_id))
