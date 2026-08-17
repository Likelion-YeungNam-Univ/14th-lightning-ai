"""C-6.2 — 베팅방 자동 정산 배치. 사용자 요청 경로가 아니라 스케줄러가 부른다(불변식 1).

18:00에 1차 시도, 실패하면 19:00·20:00 재시도(C-6.2.6) — 같은 sync_settle_rooms를
세 번 부르는 것으로 구현한다. settle_attempts가 3에 도달하면 void 처리한다.

**명세와 다르게 한 것**: C-6.2.7(상장폐지·거래정지 즉시 void)은 FinanceDataReader/yfinance
응답만으로는 "종가가 없다"와 "상장폐지됐다"를 구분할 신호가 없어, 이번 구현에서는 별도
분기 없이 C-6.2.6의 재시도 로직으로 통합 처리한다(3회 실패 시 void라는 결과는 동일).
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.deps import utcnow
from app.models import MARKET_DOMESTIC, BettingEntry, BettingRoom, StockMaster
from app.services.points import add_ledger_entry

logger = logging.getLogger(__name__)

MAX_SETTLE_ATTEMPTS = 3  # C-6.2.6 — 18:00 + 19:00 + 20:00


def _fetch_close_price(stock: StockMaster, target_date: date) -> int | None:
    """C-6.2.0 — FinanceDataReader 우선, 실패 시 yfinance 백업.

    휴장일 처리(C-6.2.3)는 조회 구간을 10일 앞당겨 잡아, 가장 최근 거래일 종가가
    자연히 마지막 행으로 나오게 하는 방식으로 해결한다(직전 거래일 종가).
    """
    start = target_date - timedelta(days=10)
    try:
        import FinanceDataReader as fdr

        row = fdr.DataReader(stock.stock_code, start, target_date)
        if row is not None and not row.empty:
            return int(round(float(row["Close"].iloc[-1])))
    except Exception as e:  # noqa: BLE001 — 외부 데이터 소스 실패는 전부 백업으로 넘긴다
        logger.info("fdr 종가 조회 실패(%s): %s", stock.stock_code, e)

    try:
        import yfinance as yf

        ticker = f"{stock.stock_code}.KS" if stock.market == MARKET_DOMESTIC else stock.stock_code
        hist = yf.Ticker(ticker).history(start=start, end=target_date + timedelta(days=1))
        if hist is not None and not hist.empty:
            return int(round(float(hist["Close"].iloc[-1])))
    except Exception as e:  # noqa: BLE001
        logger.info("yfinance 종가 조회 실패(%s): %s", stock.stock_code, e)

    return None


def _void_room(db: Session, room: BettingRoom) -> None:
    """C-6.2.2·C-6.2.6 — 무효 처리, 전액 반환."""
    entries = db.query(BettingEntry).filter(BettingEntry.room_id == room.id).all()
    for entry in entries:
        add_ledger_entry(
            db, entry.session_id, "refund", entry.amount, ref_type="room", ref_id=room.id
        )
    room.status = "void"
    room.settled_at = utcnow()
    db.commit()


def _settle_room(db: Session, room: BettingRoom, close_price: int) -> str:
    """C-6.2·C-6.2.1 — 승패 판정 + 균등 분배(잔여는 먼저 참여한 사람에게). 반환: settled|void."""
    entries = db.query(BettingEntry).filter(BettingEntry.room_id == room.id).all()
    up = [e for e in entries if e.side == "up"]
    down = [e for e in entries if e.side == "down"]
    if not up or not down:  # C-6.2.2 — 한쪽 진영만 있으면 무효
        _void_room(db, room)
        return "void"

    result_side = "up" if close_price >= room.target_price else "down"
    winners = up if result_side == "up" else down
    losers = down if result_side == "up" else up
    losing_pool = sum(e.amount for e in losers)
    share, remainder = divmod(losing_pool, len(winners))

    winners_sorted = sorted(winners, key=lambda e: e.created_at)
    for i, w in enumerate(winners_sorted):
        payout = w.amount + share + (remainder if i == 0 else 0)  # 자기 원금 + 균등분배 몫
        add_ledger_entry(db, w.session_id, "win", payout, ref_type="room", ref_id=room.id)

    room.status = "closed"
    room.result_side = result_side
    room.settle_close_price = close_price
    room.settled_at = utcnow()
    db.commit()
    return "settled"


def sync_settle_rooms(db: Session, today: date | None = None) -> dict:
    """C-6.2.4 — 18:00/19:00/20:00에 같은 함수를 반복 호출한다. C-6.3 — 상태 전이로 멱등성 보장."""
    today = today or date.today()
    rooms = (
        db.query(BettingRoom)
        .filter(BettingRoom.status.in_(("open", "pending")), BettingRoom.judge_date <= today)
        .all()
    )
    stats = {"settled": 0, "void": 0, "pending": 0}
    for room in rooms:
        stock = db.get(StockMaster, room.stock_code)
        price = _fetch_close_price(stock, room.judge_date) if stock is not None else None
        if price is None:
            room.settle_attempts += 1
            if room.settle_attempts >= MAX_SETTLE_ATTEMPTS:
                _void_room(db, room)
                stats["void"] += 1
            else:
                room.status = "pending"
                db.commit()
                stats["pending"] += 1
            continue
        outcome = _settle_room(db, room, price)
        stats[outcome] += 1
    return stats
