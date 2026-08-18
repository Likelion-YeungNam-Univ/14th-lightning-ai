"""C-3, C-4 — 베팅방 목록/상세/생성 스키마."""

from datetime import date

from pydantic import BaseModel, Field


class SideCount(BaseModel):
    """C-3.1 — 진영별 인원·포인트."""

    count: int
    points: int


class RoomListItem(BaseModel):
    id: int
    title: str
    target_price: int
    judge_date: date
    participant_count: int
    total_points: int
    up: SideCount
    down: SideCount
    leading_side: str  # C-3.1.3 — 포인트 비중 기준, 동률이면 up
    status: str


class RoomListResponse(BaseModel):
    items: list[RoomListItem]


class RoomDetailResponse(RoomListItem):
    stock_code: str
    body: str | None
    result_side: str | None
    settle_close_price: int | None


class RoomCreateRequest(BaseModel):
    stock_code: str
    title: str = Field(min_length=1, max_length=120)
    target_price: int = Field(gt=0)  # 1,000원 단위 (C-4.1.4)
    judge_date: date
    body: str | None = None
    amount: int = Field(ge=100, le=1000)  # 생성자 자동 참여 베팅 금액 (C-4.1.2, C-6.1.3)


class RoomCreateResponse(BaseModel):
    room: RoomDetailResponse


class ChartSymbolResponse(BaseModel):
    symbol: str  # C-2.1.1 — 예: KRX:005930 / NASDAQ:NVDA
