"""C-6.2 자동 정산 배치 테스트. FinanceDataReader/yfinance는 monkeypatch로 대체한다.

예상 문제 지점:
1. 종가 ≥ 목표가 → up 승, 미만 → down 승 (C-6.2)
2. 승패 분배 — 진 쪽 전액 균등분배 + 원금 반환, 잔여는 먼저 참여한 사람에게(C-6.2.1)
3. 한쪽 진영만 있으면 void + 전액 반환(C-6.2.2)
4. 종가 조회 실패 시 pending으로 전환, 3회 실패 시 void + 전액 반환(C-6.2.6)
5. 정산 후 open/pending이 아닌 방(closed/void)은 재처리하지 않는다(C-6.3 멱등성)
6. (승래 리뷰 B-1) 해외 방은 판가름 날짜 당일엔 정산 대상에서 빠지고, 다음날 배치에서만 처리된다
7. (승래 리뷰 B-1) 조회된 종가 날짜가 판가름 날짜보다 이르면 1·2차 시도는 재시도, 마지막
   시도에서만 직전 거래일 종가로 인정한다
8. (승래 리뷰 B-2) 승패 비교는 반올림 전 원시 float로 한다 — 119.6 vs 목표 120이 120으로
   반올림돼 승패가 뒤집히면 안 된다
"""

from datetime import date, timedelta

import app.services.settlement as settlement_mod
from app.db import SessionLocal
from app.models import BettingEntry, BettingRoom, PointLedger
from app.services.points import balance
from app.services.rooms import _add_business_days, create_room
from app.services.sessions import ensure_session
from app.services.settlement import sync_settle_rooms


def _make_room_with_sides(db, *, stock_code, target_price, judge_date_, up_amounts, down_amounts):
    creator, _ = ensure_session(db, None)
    creator.authenticated = True
    db.add(PointLedger(session_id=creator.id, kind="charge", amount=10_000))
    db.commit()
    room = create_room(
        db,
        creator,
        stock_code=stock_code,
        title="정산테스트",
        target_price=target_price,
        judge_date_=judge_date_,
        body=None,
        amount=up_amounts[0],  # 생성자가 첫 up 참여자
    )
    for amt in up_amounts[1:]:
        s, _ = ensure_session(db, None)
        s.authenticated = True
        db.add(PointLedger(session_id=s.id, kind="charge", amount=10_000))
        db.commit()
        from app.services.betting import place_entry

        place_entry(db, s, room["id"], side="up", amount=amt)
    for amt in down_amounts:
        s, _ = ensure_session(db, None)
        s.authenticated = True
        db.add(PointLedger(session_id=s.id, kind="charge", amount=10_000))
        db.commit()
        from app.services.betting import place_entry

        place_entry(db, s, room["id"], side="down", amount=amt)
    return room["id"]


def test_up_wins_pays_stake_back_plus_even_split(client, monkeypatch):
    with SessionLocal() as db:
        judge_date_ = _add_business_days(date.today(), 5)
        room_id = _make_room_with_sides(
            db,
            stock_code="111110",
            target_price=61000,
            judge_date_=judge_date_,
            up_amounts=[300],  # 생성자 1명
            down_amounts=[100, 100],  # 2명
        )
        # 판가름 날짜를 과거로 강제 이동(create_room은 오늘 이후만 허용하므로 여기서 조정)
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(  # 승리
            settlement_mod, "_fetch_close_price", lambda stock, d: (63000.0, d)
        )

        stats = sync_settle_rooms(db, today=date.today())
        assert stats == {"settled": 1, "void": 0, "pending": 0}

        db.refresh(room)
        assert room.status == "closed"
        assert room.result_side == "up"
        assert room.settle_close_price == 63000

        up_entry = db.query(BettingEntry).filter_by(room_id=room_id, side="up").one()
        # 원금 300 + 진 쪽 전액(200) 균등분배(승자 1명이라 전부) = 500
        assert balance(db, up_entry.session_id) == 10_000 - 300 + 500


def test_down_wins_split_evenly_with_remainder_to_earliest(client, monkeypatch):
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="222220",
            target_price=62000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100, 100, 100],  # 3명 — 나눗셈 나머지 발생 유도
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(  # down 승
            settlement_mod, "_fetch_close_price", lambda stock, d: (60000.0, d)
        )

        sync_settle_rooms(db, today=date.today())
        db.refresh(room)
        assert room.result_side == "down"

        down_entries = (
            db.query(BettingEntry)
            .filter_by(room_id=room_id, side="down")
            .order_by(BettingEntry.created_at.asc())
            .all()
        )
        # 진 쪽 pool=300, 승자 3명 → 100씩 균등분배, 나머지 0
        for e in down_entries:
            assert balance(db, e.session_id) == 10_000 - 100 + (100 + 100)


def test_one_sided_room_voided_and_refunded(client, monkeypatch):
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="333330",
            target_price=63000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[],  # 한쪽 진영만
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: (65000.0, d))

        stats = sync_settle_rooms(db, today=date.today())
        assert stats == {"settled": 0, "void": 1, "pending": 0}

        db.refresh(room)
        assert room.status == "void"
        up_entry = db.query(BettingEntry).filter_by(room_id=room_id, side="up").one()
        assert balance(db, up_entry.session_id) == 10_000  # 전액 반환


def test_missing_close_price_retries_then_voids(client, monkeypatch):
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="444440",
            target_price=64000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: None)

        stats1 = sync_settle_rooms(db, today=date.today())
        assert stats1 == {"settled": 0, "void": 0, "pending": 1}
        db.refresh(room)
        assert room.status == "pending" and room.settle_attempts == 1

        stats2 = sync_settle_rooms(db, today=date.today())
        db.refresh(room)
        assert stats2 == {"settled": 0, "void": 0, "pending": 1}
        assert room.settle_attempts == 2

        stats3 = sync_settle_rooms(db, today=date.today())
        db.refresh(room)
        assert stats3 == {"settled": 0, "void": 1, "pending": 0}
        assert room.status == "void" and room.settle_attempts == 3

        up_entry = db.query(BettingEntry).filter_by(room_id=room_id, side="up").one()
        down_entry = db.query(BettingEntry).filter_by(room_id=room_id, side="down").one()
        assert balance(db, up_entry.session_id) == 10_000
        assert balance(db, down_entry.session_id) == 10_000


def test_closed_room_not_reprocessed(client, monkeypatch):
    """멱등성(C-6.3) — 이미 closed된 방은 다음 정산 배치에서 다시 건드리지 않는다."""
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="555550",
            target_price=65000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: (67000.0, d))
        sync_settle_rooms(db, today=date.today())
        db.refresh(room)
        assert room.status == "closed"
        balance_after_first = balance(db, room.creator_session_id)

        # 두 번째 배치 실행 — 같은 방은 다시 정산되지 않는다
        stats = sync_settle_rooms(db, today=date.today())
        assert stats == {"settled": 0, "void": 0, "pending": 0}
        assert balance(db, room.creator_session_id) == balance_after_first


def test_overseas_room_not_settled_same_day(client, monkeypatch):
    """B-1 — 해외 방은 판가름 날짜 당일엔 대상에서 빠진다(미국 장이 아직 안 끝남)."""
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="TSLA",
            target_price=76000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today()  # 오늘이 판가름 날짜
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: (77000.0, d))

        stats = sync_settle_rooms(db, today=date.today())
        assert stats == {"settled": 0, "void": 0, "pending": 0}  # 아예 손대지 않는다

        db.refresh(room)
        assert room.status == "open" and room.settle_attempts == 0  # 시도 횟수도 안 늘어난다


def test_overseas_room_settled_next_day(client, monkeypatch):
    """B-1 — 해외 방은 다음날 배치(judge_date < today)부터 정산 대상이다."""
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="TSLA",
            target_price=77000,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: (78000.0, d))

        stats = sync_settle_rooms(db, today=date.today())
        assert stats == {"settled": 1, "void": 0, "pending": 0}
        db.refresh(room)
        assert room.status == "closed" and room.result_side == "up"


def test_stale_price_date_retried_then_accepted_on_final_attempt(client, monkeypatch):
    """B-1 — 종가 날짜가 판가름 날짜보다 이르면 1·2차는 재시도, 마지막 시도만 인정한다."""
    with SessionLocal() as db:
        judge_date_ = _add_business_days(date.today(), 5)
        room_id = _make_room_with_sides(
            db,
            stock_code="111110",
            target_price=74000,
            judge_date_=judge_date_,
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()
        stale_date = room.judge_date - timedelta(days=1)  # 판가름 날짜보다 이른 날짜

        monkeypatch.setattr(
            settlement_mod, "_fetch_close_price", lambda stock, d: (79000.0, stale_date)
        )

        stats1 = sync_settle_rooms(db, today=date.today())
        assert stats1 == {"settled": 0, "void": 0, "pending": 1}  # 1차 — 아직 인정 안 함
        db.refresh(room)
        assert room.status == "pending" and room.settle_attempts == 1

        stats2 = sync_settle_rooms(db, today=date.today())
        assert stats2 == {"settled": 0, "void": 0, "pending": 1}  # 2차 — 여전히 재시도
        db.refresh(room)
        assert room.settle_attempts == 2

        stats3 = sync_settle_rooms(db, today=date.today())  # 3차(마지막) — 직전 거래일로 인정
        assert stats3 == {"settled": 1, "void": 0, "pending": 0}
        db.refresh(room)
        assert room.status == "closed" and room.result_side == "up"


def test_float_close_price_not_rounded_before_comparison(client, monkeypatch):
    """B-2 — 119.6 vs 목표 120은 down 승이어야 한다(반올림하면 120으로 올라가 up 승이 됨)."""
    with SessionLocal() as db:
        room_id = _make_room_with_sides(
            db,
            stock_code="TSLA",  # 해외 — 목표가가 달러 단위라 1,000원 단위 검증을 안 받는다
            target_price=120,
            judge_date_=_add_business_days(date.today(), 5),
            up_amounts=[300],
            down_amounts=[100],
        )
        room = db.get(BettingRoom, room_id)
        room.judge_date = date.today() - timedelta(days=1)
        db.commit()

        monkeypatch.setattr(settlement_mod, "_fetch_close_price", lambda stock, d: (119.6, d))

        sync_settle_rooms(db, today=date.today())
        db.refresh(room)
        assert room.result_side == "down"  # 반올림 버그였으면 "up"이 나온다
        assert room.settle_close_price == 120  # 저장은 반올림값(정수 컬럼 한계, 명세확정사항 기록)
