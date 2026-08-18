"""C-1~C-4 — 커뮤니티 탭 베팅방. 열람은 무인증, 생성은 로그인 필요(C-1.4, C-4.1)."""

from fastapi import APIRouter

from app.deps import AuthSession, DbDep
from app.schemas.rooms import (
    BettingEntryRequest,
    BettingEntryResponse,
    ChartSymbolResponse,
    RoomCreateRequest,
    RoomCreateResponse,
    RoomDetailResponse,
    RoomListResponse,
)
from app.services import betting as betting_service
from app.services import rooms as room_service

router = APIRouter(tags=["rooms"])


@router.get("/rooms", response_model=RoomListResponse)
def list_rooms(stock_code: str, db: DbDep, status: str | None = None) -> RoomListResponse:
    return RoomListResponse(items=room_service.list_rooms(db, stock_code, status))


@router.get("/rooms/{room_id}", response_model=RoomDetailResponse)
def get_room(room_id: int, db: DbDep) -> RoomDetailResponse:
    return RoomDetailResponse(**room_service.get_room(db, room_id))


@router.post("/rooms", response_model=RoomCreateResponse)
def create_room(body: RoomCreateRequest, session: AuthSession, db: DbDep) -> RoomCreateResponse:
    room = room_service.create_room(
        db,
        session,
        stock_code=body.stock_code,
        title=body.title,
        target_price=body.target_price,
        judge_date_=body.judge_date,
        body=body.body,
        amount=body.amount,
    )
    return RoomCreateResponse(room=RoomDetailResponse(**room))


@router.get("/stocks/{stock_code}/chart-symbol", response_model=ChartSymbolResponse)
def chart_symbol(stock_code: str, db: DbDep) -> ChartSymbolResponse:
    return ChartSymbolResponse(symbol=room_service.get_chart_symbol(db, stock_code))


@router.post("/rooms/{room_id}/entries", response_model=BettingEntryResponse)
def create_entry(
    room_id: int, body: BettingEntryRequest, session: AuthSession, db: DbDep
) -> BettingEntryResponse:
    room = betting_service.place_entry(db, session, room_id, side=body.side, amount=body.amount)
    return BettingEntryResponse(room=RoomDetailResponse(**room))
