"""C-1~C-4·C-7 — 커뮤니티 탭 베팅방·댓글. 열람은 무인증, 쓰기는 로그인 필요."""

from fastapi import APIRouter

from app.deps import AuthSession, DbDep, OptionalSession
from app.schemas.comments import (
    CommentCreateRequest,
    CommentCreateResponse,
    CommentDeleteResponse,
    CommentItem,
    CommentLikeResponse,
    CommentListResponse,
)
from app.schemas.rooms import (
    BettingEntryRequest,
    BettingEntryResponse,
    ChartSymbolResponse,
    RoomCreateRequest,
    RoomCreateResponse,
    RoomDeleteResponse,
    RoomDetailResponse,
    RoomListResponse,
)
from app.services import betting as betting_service
from app.services import comments as comment_service
from app.services import rooms as room_service

router = APIRouter(tags=["rooms"])


@router.get("/rooms", response_model=RoomListResponse)
def list_rooms(stock_code: str, db: DbDep, status: str | None = None) -> RoomListResponse:
    """C-3 — 종목별 베팅방 목록. 무인증.

    - `status`: `open`(진행 중) | `settled`(정산 완료) | 생략(전체)
    - 정렬: 최신 생성순. 각 방에 참여 인원·총 포인트·up/down 분포·우세 진영 포함
    """
    return RoomListResponse(items=room_service.list_rooms(db, stock_code, status))


@router.get("/rooms/{room_id}", response_model=RoomDetailResponse)
def get_room(room_id: int, db: DbDep) -> RoomDetailResponse:
    """C-5 — 방 상세(본문·생성자 닉네임·베팅 현황). 무인증. 404 `unknown_room`."""
    return RoomDetailResponse(**room_service.get_room(db, room_id))


@router.post("/rooms", response_model=RoomCreateResponse)
def create_room(body: RoomCreateRequest, session: AuthSession, db: DbDep) -> RoomCreateResponse:
    """C-4 — 베팅방 생성 + 생성자 자동 베팅(up, `amount`P). **로그인 필요.**

    검증 규칙과 에러 코드:
    - `target_price` 1,000원 단위 → `invalid_target_price`
    - `judge_date` 평일 + 다음 거래일~90일 → `invalid_judge_date` (details에 earliest/latest)
    - `amount` 100~1000P, 잔액 내 → `invalid_amount` / `insufficient_points`
    - 같은 종목·목표가·판정일 방 존재 → `duplicate_room` (details.room_id)
    - 동시 진행 방 상한 / 하루 생성 한도(KST 기준) → `room_limit_exceeded` / `daily_limit_exceeded`
    - 미로그인 401 `login_required`
    """
    room = room_service.create_room(
        db,
        session,
        stock_code=body.stock_code,
        title=body.title,
        target_price=body.target_price,
        judge_date_=body.judge_date,
        body=body.body,
        amount=body.amount,
        max_participants=body.max_participants,
    )
    return RoomCreateResponse(room=RoomDetailResponse(**room))


@router.delete("/rooms/{room_id}", response_model=RoomDeleteResponse)
def delete_room(room_id: int, session: AuthSession, db: DbDep) -> RoomDeleteResponse:
    """#95 — 방 삭제. **로그인 필요 + 생성자 본인만.**

    - 다른 참여자가 있으면 400 `room_has_entrants` — 상대의 베팅을 임의로 무를 수 없다
    - open 상태만 400 `room_not_open` / 남의 방 403 `not_room_owner` / 없는 방 404
    - 성공 시 생성자 자동 베팅 환급, 방·참여·댓글 삭제(원장 기록은 보존)
    """
    return RoomDeleteResponse(removed=room_service.delete_room(db, session, room_id))


@router.get("/stocks/{stock_code}/chart-symbol", response_model=ChartSymbolResponse)
def chart_symbol(stock_code: str, db: DbDep) -> ChartSymbolResponse:
    """C-2.1.1 — TradingView 심볼. 국내 `KRX:005930` / 해외 `NASDAQ:NVDA`. 404 `unknown_stock`.

    주의: KRX 심볼은 거래소 정책상 TradingView 임베드 위젯에서 렌더되지 않는다(링크 안내용).
    """
    return ChartSymbolResponse(symbol=room_service.get_chart_symbol(db, stock_code))


@router.post("/rooms/{room_id}/entries", response_model=BettingEntryResponse)
def create_entry(
    room_id: int, body: BettingEntryRequest, session: AuthSession, db: DbDep
) -> BettingEntryResponse:
    """C-6 — 베팅 참여(1인 1회). **로그인 필요.**

    - `side`: `up` | `down` → 아니면 `invalid_side`
    - `amount`: 100~1000P → `invalid_amount`, 잔액 부족 `insufficient_points`
    - 방 상태: `room_not_open`(정산됨 등) / `entry_closed`(마감) / `room_full`(4명)
    - 이미 참여한 방이면 `already_entered`
    """
    room = betting_service.place_entry(db, session, room_id, side=body.side, amount=body.amount)
    return BettingEntryResponse(room=RoomDetailResponse(**room))


@router.get("/rooms/{room_id}/comments", response_model=CommentListResponse)
def list_comments(
    room_id: int, session: OptionalSession, db: DbDep, sort: str = "likes"
) -> CommentListResponse:
    """이슈 #80(L-3) — sort=likes(기본, 동점 최신순) | recent."""
    viewer_id = session.id if session is not None else None
    items = comment_service.list_comments(db, room_id, viewer_id, sort=sort)
    return CommentListResponse(items=[CommentItem(**c) for c in items])


@router.post("/rooms/{room_id}/comments", response_model=CommentCreateResponse)
def create_comment(
    room_id: int, body: CommentCreateRequest, session: AuthSession, db: DbDep
) -> CommentCreateResponse:
    """C-7 — 댓글 작성(300자). **로그인 필요.**

    `saved_card_id`(선택)로 내 저장 카드를 근거로 첨부 — 첨부 시점 스냅샷이 복사돼
    원본을 저장 해제해도 댓글 첨부는 유지된다. 에러: `unknown_room`, `unknown_saved_card`
    """
    item = comment_service.create_comment(
        db, session, room_id, body=body.body, saved_card_id=body.saved_card_id
    )
    return CommentCreateResponse(item=CommentItem(**item))


@router.delete("/comments/{comment_id}", response_model=CommentDeleteResponse)
def delete_comment(comment_id: int, session: AuthSession, db: DbDep) -> CommentDeleteResponse:
    """C-7.3 — 내 댓글 삭제(soft delete — 본문·첨부 비움). 남의 댓글이면 403 `not_comment_owner`."""
    return CommentDeleteResponse(removed=comment_service.delete_comment(db, session, comment_id))


@router.post("/comments/{comment_id}/like", response_model=CommentLikeResponse)
def like_comment(comment_id: int, session: AuthSession, db: DbDep) -> CommentLikeResponse:
    """C-7.4 — 좋아요(멱등). 내 댓글 400 `self_like_blocked` / 지워진 댓글 404 `comment_deleted`."""
    return CommentLikeResponse(**comment_service.like_comment(db, session, comment_id))


@router.delete("/comments/{comment_id}/like", response_model=CommentLikeResponse)
def unlike_comment(comment_id: int, session: AuthSession, db: DbDep) -> CommentLikeResponse:
    """C-7.4 — 좋아요 취소(멱등, 안 눌렀어도 200)."""
    return CommentLikeResponse(**comment_service.unlike_comment(db, session, comment_id))
