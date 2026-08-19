"""F-7 — 저장 카드 요청/응답 스키마."""

from datetime import datetime

from pydantic import BaseModel


class SavedCardAddRequest(BaseModel):
    card_id: int
    stock_code: str  # 저장 당시 보던 종목 — 배지용 (F-7.5)


class SavedCardItem(BaseModel):
    """표시는 snapshot만 쓴다 (F-7.3). card_id는 원자료 정리 후 null일 수 있다."""

    id: int  # saved_card 행 자체의 PK — 베팅방 댓글 자료 카드 첨부(C-5.1)가 이 값을 쓴다
    card_id: int | None
    tab: str  # 저장 당시 탭 배지
    stock_code: str
    stock_name: str | None
    saved_at: datetime
    snapshot: dict


class SavedCardAddResponse(BaseModel):
    item: SavedCardItem
    already_saved: bool  # 멱등 저장 여부


class SavedCardDeleteResponse(BaseModel):
    removed: bool  # 멱등 — 이미 없었으면 false


class SavedCardListResponse(BaseModel):
    items: list[SavedCardItem]
