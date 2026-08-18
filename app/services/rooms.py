"""C-1~C-4, C-10 — 커뮤니티 탭 베팅방 도메인 로직.

목표가 ±50% 범위 검증(C-4.1.4 제안)은 "사용자 요청 경로에서 외부 API를 부르지
않는다"(불변식 1)와 충돌한다 — 실시간 현재가 조회 수단이 없어 구조적 검증(1,000원
단위)까지만 하고, 범위 검증은 보류한다(PR 설명 참고).
"""

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import today_kst
from app.errors import AppError
from app.models import MARKET_DOMESTIC, BettingEntry, BettingRoom, StockMaster, UserSession
from app.services.points import add_ledger_entry, balance, lock_session

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MIN_JUDGE_LEAD_DAYS = 3  # 생성일 +3영업일 (C-4.1.3)
MAX_JUDGE_LEAD_DAYS = 90  # (제안)
MAX_OPEN_ROOMS_PER_CREATOR = 3  # C-4.1.6
MAX_ROOMS_PER_DAY = 5  # C-4.1.6
ACTIVE_STATUSES = ("open", "pending")


@lru_cache(maxsize=1)
def _overseas_exchange_map() -> dict[str, str]:
    with open(DATA_DIR / "overseas_exchange.json", encoding="utf-8") as f:
        return json.load(f)["exchanges"]


def chart_symbol(stock: StockMaster) -> str:
    """C-2.1.1 — 국내는 항상 KRX:, 해외는 화이트리스트 거래소(기본 NASDAQ)."""
    if stock.market == MARKET_DOMESTIC:
        return f"KRX:{stock.stock_code}"
    exchange = _overseas_exchange_map().get(stock.stock_code, "NASDAQ")
    return f"{exchange}:{stock.stock_code}"


def get_chart_symbol(db: Session, stock_code: str) -> str:
    stock = db.get(StockMaster, stock_code)
    if stock is None:
        raise AppError("unknown_stock", "존재하지 않는 종목코드입니다", 404)
    return chart_symbol(stock)


def _add_business_days(start: date, days: int) -> date:
    """영업일 계산 — 월~금만 (공휴일 캘린더 없음, 단순화)."""
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _validate_judge_date(judge_date: date, today: date) -> None:
    earliest = _add_business_days(today, MIN_JUDGE_LEAD_DAYS)
    latest = today + timedelta(days=MAX_JUDGE_LEAD_DAYS)
    if judge_date.weekday() >= 5:
        raise AppError("invalid_judge_date", "판가름 날짜는 거래일(평일)이어야 합니다", 400)
    if not (earliest <= judge_date <= latest):
        raise AppError(
            "invalid_judge_date",
            f"판가름 날짜는 {earliest}~{latest} 범위여야 합니다",
            400,
            {"earliest": str(earliest), "latest": str(latest)},
        )


def _room_aggregates(db: Session, room_id: int) -> dict:
    rows = (
        db.query(BettingEntry.side, func.count(), func.coalesce(func.sum(BettingEntry.amount), 0))
        .filter(BettingEntry.room_id == room_id)
        .group_by(BettingEntry.side)
        .all()
    )
    counts = {side: {"count": c, "points": p} for side, c, p in rows}
    up = counts.get("up", {"count": 0, "points": 0})
    down = counts.get("down", {"count": 0, "points": 0})
    # C-3.1.3 — 포인트 비중 기준, 동률이면 up
    leading = "up" if up["points"] >= down["points"] else "down"
    return {
        "participant_count": up["count"] + down["count"],
        "total_points": up["points"] + down["points"],
        "up": up,
        "down": down,
        "leading_side": leading,
    }


def _to_list_item(room: BettingRoom, agg: dict) -> dict:
    return {
        "id": room.id,
        "title": room.title,
        "target_price": room.target_price,
        "judge_date": room.judge_date,
        "status": room.status,
        **agg,
    }


def list_rooms(db: Session, stock_code: str, status: str | None) -> list[dict]:
    """C-3.1 — 종목별 방 목록, 판가름 날짜 가까운 순(C-3.1.1)."""
    query = db.query(BettingRoom).filter(BettingRoom.stock_code == stock_code)
    query = (
        query.filter(BettingRoom.status == status)
        if status
        else query.filter(BettingRoom.status == "open")
    )  # C-3.1.2 — 기본은 open만
    rooms = query.order_by(BettingRoom.judge_date.asc()).all()
    return [_to_list_item(r, _room_aggregates(db, r.id)) for r in rooms]


def get_room(db: Session, room_id: int) -> dict:
    room = db.get(BettingRoom, room_id)
    if room is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)
    agg = _room_aggregates(db, room_id)
    return {
        **_to_list_item(room, agg),
        "stock_code": room.stock_code,
        "body": room.body,
        "result_side": room.result_side,
        "settle_close_price": room.settle_close_price,
    }


def create_room(
    db: Session,
    session: UserSession,
    *,
    stock_code: str,
    title: str,
    target_price: int,
    judge_date_: date,
    body: str | None,
    amount: int,
    today: date | None = None,
) -> dict:
    """C-4.1 — 방 생성. 생성자는 자기 목표가 방향(up)에 자동 참여한다(C-4.1.2).

    날짜 기준은 KST(승래 리뷰 B-5) — 컨테이너가 UTC로 떠도 하루 한도가 안 어긋난다.
    """
    today = today or today_kst()
    stock = db.get(StockMaster, stock_code)
    if stock is None:
        raise AppError("unknown_stock", "존재하지 않는 종목코드입니다", 400)
    # 1,000원 단위 검증은 원화 표시 종목(국내)에만 적용 — 해외는 달러 표시라 단위가 다르다
    if stock.market == MARKET_DOMESTIC and target_price % 1000 != 0:
        raise AppError("invalid_target_price", "목표가는 1,000원 단위여야 합니다", 400)
    _validate_judge_date(judge_date_, today)
    locked = lock_session(db, session.id)  # B-4 — 확인·차감 사이 경합 차단
    if amount > balance(db, locked.id):  # C-6.1.3 — 생성자 자동 참여도 실제 베팅이다
        raise AppError("insufficient_points", "보유 포인트가 부족합니다", 400)

    # 중복 방 차단 (C-4.1.5) — 같은 종목·목표가·판가름 날짜의 진행 중인 방
    dup = (
        db.query(BettingRoom)
        .filter(
            BettingRoom.stock_code == stock_code,
            BettingRoom.target_price == target_price,
            BettingRoom.judge_date == judge_date_,
            BettingRoom.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )
    if dup is not None:
        raise AppError(
            "duplicate_room", "동일한 조건의 베팅방이 이미 있습니다", 400, {"room_id": dup.id}
        )

    # 생성 한도 (C-4.1.6)
    open_count = (
        db.query(func.count())
        .select_from(BettingRoom)
        .filter(
            BettingRoom.creator_session_id == session.id,
            BettingRoom.status.in_(ACTIVE_STATUSES),
        )
        .scalar()
    )
    if open_count >= MAX_OPEN_ROOMS_PER_CREATOR:
        raise AppError(
            "room_limit_exceeded", f"동시 진행 방은 최대 {MAX_OPEN_ROOMS_PER_CREATOR}개입니다", 400
        )
    today_count = (
        db.query(func.count())
        .select_from(BettingRoom)
        .filter(
            BettingRoom.creator_session_id == session.id,
            func.date(BettingRoom.created_at) == today,
        )
        .scalar()
    )
    if today_count >= MAX_ROOMS_PER_DAY:
        raise AppError(
            "room_daily_limit_exceeded", f"하루 생성 한도는 {MAX_ROOMS_PER_DAY}개입니다", 400
        )

    room = BettingRoom(
        stock_code=stock_code,
        creator_session_id=session.id,
        title=title,
        target_price=target_price,
        judge_date=judge_date_,
        body=body,
        status="open",
    )
    db.add(room)
    db.flush()  # room.id 확보
    db.add(BettingEntry(room_id=room.id, session_id=session.id, side="up", amount=amount))
    add_ledger_entry(db, session.id, "bet", -amount, ref_type="room", ref_id=room.id)
    db.commit()
    return get_room(db, room.id)
