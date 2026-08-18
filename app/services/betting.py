"""C-6.1 — 베팅 참여. 정산(C-6.2)은 app/services/settlement.py가 맡는다."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import now_kst
from app.errors import AppError
from app.models import BettingEntry, BettingRoom, UserSession
from app.services.points import add_ledger_entry, balance, lock_session

MAX_ENTRANTS = 4  # C-6.1.1 — 진영 무관 방당 최대 4명
MIN_AMOUNT = 100  # C-6.1.3
MAX_AMOUNT = 1_000  # C-6.1.3
MARKET_CLOSE = time(15, 30)  # 참여 마감 시각 — 판가름 날짜 전 영업일 장 마감(C-6.1.4)


def _previous_business_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def entry_deadline(judge_date: date) -> datetime:
    """C-6.1.4 — 판가름 날짜 전 영업일 장 마감까지."""
    return datetime.combine(_previous_business_day(judge_date), MARKET_CLOSE)


def place_entry(
    db: Session,
    session: UserSession,
    room_id: int,
    *,
    side: str,
    amount: int,
    now: datetime | None = None,
) -> dict:
    """C-6.1 — 진영·금액 검증 후 즉시 포인트 차감(잔액 확인+차감을 한 트랜잭션으로, C-8.3.1).

    마감 시각 비교는 KST 기준(승래 리뷰 B-5) — 컨테이너가 UTC로 떠도 어긋나지 않는다.
    """
    now = now or now_kst()
    if side not in ("up", "down"):
        raise AppError("invalid_side", "진영은 up 또는 down이어야 합니다", 400)
    if not (MIN_AMOUNT <= amount <= MAX_AMOUNT):
        raise AppError("invalid_amount", f"베팅 금액은 {MIN_AMOUNT}~{MAX_AMOUNT}P여야 합니다", 400)

    room = db.get(BettingRoom, room_id)
    if room is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)
    if room.status != "open":
        raise AppError("room_not_open", "참여할 수 없는 상태의 방입니다", 400)
    if now > entry_deadline(room.judge_date):
        raise AppError("entry_closed", "참여 마감 시각이 지났습니다", 400)

    entrant_count = (
        db.query(func.count())
        .select_from(BettingEntry)
        .filter(BettingEntry.room_id == room_id)
        .scalar()
    )
    if entrant_count >= MAX_ENTRANTS:  # C-6.1.1
        raise AppError("room_full", "이 방은 이미 4명이 참여했습니다", 400)

    locked = lock_session(db, session.id)  # B-4 — 확인·차감 사이 경합 차단
    current_balance = balance(db, locked.id)
    if amount > current_balance:  # C-6.1.3 — 서버가 다시 확인
        raise AppError("insufficient_points", "보유 포인트가 부족합니다", 400)

    entry = BettingEntry(room_id=room_id, session_id=session.id, side=side, amount=amount)
    db.add(entry)
    try:
        db.flush()  # room_id+session_id 유니크 제약(C-6.1.2) 위반을 여기서 잡는다
    except IntegrityError as e:
        db.rollback()
        raise AppError("already_entered", "이미 참여한 방입니다", 400) from e

    add_ledger_entry(db, session.id, "bet", -amount, ref_type="room", ref_id=room_id)
    db.commit()
    from app.services.rooms import get_room

    return get_room(db, room_id)
