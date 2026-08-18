"""C-6.2 — 베팅방 자동 정산 배치. 사용자 요청 경로가 아니라 스케줄러가 부른다(불변식 1).

18:00에 1차 시도, 실패하면 19:00·20:00 재시도(C-6.2.6) — 같은 sync_settle_rooms를
세 번 부르는 것으로 구현한다. settle_attempts가 3에 도달하면 void 처리한다.

**명세와 다르게 한 것**: C-6.2.7(상장폐지·거래정지 즉시 void)은 FinanceDataReader/yfinance
응답만으로는 "종가가 없다"와 "상장폐지됐다"를 구분할 신호가 없어, 이번 구현에서는 별도
분기 없이 C-6.2.6의 재시도 로직으로 통합 처리한다(3회 실패 시 void라는 결과는 동일).

**승래 리뷰 반영(B-1)**: 해외 방은 판가름 날짜 당일 18:00 KST엔 미국 장이 아직 열리기도
전이라(22:30 KST 개장) 항상 전일 종가로 확정돼버렸다 — 해외 방은 `judge_date < today`
(다음날 배치)에서만 정산 대상으로 삼는다. 또한 조회된 종가의 날짜가 judge_date보다
이르면(아직 당일 데이터가 없는 것) 1·2차 시도는 재시도로 돌리고, 마지막 시도에서만
"직전 거래일 종가"(C-6.2.3 휴장일 규칙)로 인정한다.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.deps import today_kst, utcnow
from app.models import MARKET_DOMESTIC, MARKET_OVERSEAS, BettingEntry, BettingRoom, StockMaster
from app.services.points import add_ledger_entry

logger = logging.getLogger(__name__)

MAX_SETTLE_ATTEMPTS = 3  # C-6.2.6 — 18:00 + 19:00 + 20:00


def _fetch_close_price(stock: StockMaster, target_date: date) -> tuple[float, date] | None:
    """C-6.2.0 — FinanceDataReader 우선, 실패 시 yfinance 백업.

    반환값은 (원시 종가, 그 종가의 실제 날짜) — 호출부가 "아직 당일 데이터가 없는 것"과
    "휴장일이라 직전 거래일 종가를 쓴 것"을 구분할 수 있어야 한다(승래 리뷰 B-1).
    정수로 반올림하지 않는다 — 그 자체가 승패를 뒤집을 수 있다(B-2, 예: 119.6 vs 목표 120).
    """
    start = target_date - timedelta(days=10)
    try:
        import FinanceDataReader as fdr

        row = fdr.DataReader(stock.stock_code, start, target_date)
        if row is not None and not row.empty:
            return float(row["Close"].iloc[-1]), row.index[-1].date()
    except Exception as e:  # noqa: BLE001 — 외부 데이터 소스 실패는 전부 백업으로 넘긴다
        logger.info("fdr 종가 조회 실패(%s): %s", stock.stock_code, e)

    try:
        import yfinance as yf

        if stock.market == MARKET_DOMESTIC:
            # 강력 권고 — KOSDAQ은 .KQ, 그 외(KOSPI 등)는 .KS
            suffix = ".KQ" if (stock.exchange or "").upper() == "KOSDAQ" else ".KS"
            ticker = f"{stock.stock_code}{suffix}"
        else:
            ticker = stock.stock_code
        hist = yf.Ticker(ticker).history(start=start, end=target_date + timedelta(days=1))
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1]), hist.index[-1].date()
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


def _settle_room(db: Session, room: BettingRoom, close_price: float) -> str:
    """C-6.2·C-6.2.1 — 승패 판정 + 균등 분배(잔여는 먼저 참여한 사람에게). 반환: settled|void.

    승패 비교는 원시 float로 한다(B-2) — 반올림한 값으로 비교하면 119.6 vs 목표가 120이
    120으로 올라가 승패가 뒤집힌다. 저장(`settle_close_price`)만 정수 컬럼이라 반올림한다
    — 근거 기록용으로 약간의 오차가 남는 것은 감수하고, 정확한 정수(센트 등) 저장은 컬럼
    타입 변경(Numeric)이 필요해 팀 확인 후 별도로 처리한다(명세확정사항 기록).
    """
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
    room.settle_close_price = round(close_price)
    room.settled_at = utcnow()
    db.commit()
    return "settled"


def _due_for_settlement(room: BettingRoom, stock: StockMaster | None, today: date) -> bool:
    """B-1 — 해외 방은 판가름 날짜 당일엔 미국 장이 아직 안 끝나 대상에서 뺀다."""
    if stock is None:
        return True  # 종목이 사라졌어도 정산 시도는 하고, 실패 경로(가격 None)로 넘긴다
    if stock.market == MARKET_OVERSEAS:
        return room.judge_date < today  # 다음날 배치에서만
    return room.judge_date <= today


def _settle_one_room(db: Session, room: BettingRoom, stats: dict) -> None:
    stock = db.get(StockMaster, room.stock_code)
    result = _fetch_close_price(stock, room.judge_date) if stock is not None else None
    is_final_attempt = room.settle_attempts >= MAX_SETTLE_ATTEMPTS - 1

    price: float | None
    if result is None:
        price = None
    else:
        raw_price, price_date = result
        if price_date < room.judge_date and not is_final_attempt:
            # 아직 당일 종가가 반영 안 됐을 뿐(휴장일인지 단순 지연인지 불확실) — 재시도로 미룬다
            price = None
        else:
            price = raw_price  # 당일 종가이거나, 마지막 시도라 직전 거래일 종가를 인정(C-6.2.3)

    if price is None:
        room.settle_attempts += 1
        if room.settle_attempts >= MAX_SETTLE_ATTEMPTS:
            _void_room(db, room)
            stats["void"] += 1
        else:
            room.status = "pending"
            db.commit()
            stats["pending"] += 1
        return

    outcome = _settle_room(db, room, price)
    stats[outcome] += 1


def sync_settle_rooms(db: Session, today: date | None = None) -> dict:
    """C-6.2.4 — 18:00/19:00/20:00에 같은 함수를 반복 호출한다. C-6.3 — 상태 전이로 멱등성 보장."""
    today = today or today_kst()
    rooms = (
        db.query(BettingRoom)
        .filter(BettingRoom.status.in_(("open", "pending")), BettingRoom.judge_date <= today)
        .all()
    )
    stats = {"settled": 0, "void": 0, "pending": 0}
    for room in rooms:
        stock = db.get(StockMaster, room.stock_code)
        if not _due_for_settlement(room, stock, today):
            continue  # 해외 당일 방 — 다음날 배치로 미룬다(B-1), 시도 횟수도 안 늘린다
        try:  # 강력 권고 — 한 방의 예외가 나머지 정산을 막지 않게 격리
            _settle_one_room(db, room, stats)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.warning("방 %s 정산 중 예외: %s", room.id, e)
    return stats
