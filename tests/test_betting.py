"""C-6.1 베팅 참여 테스트.

예상 문제 지점:
1. 비로그인 참여 → 401
2. 없는 방 참여 → 404
3. 잘못된 진영 문자열 → 400
4. 베팅 금액 범위(100~1000P) 벗어남 → 400 (스키마 레벨)
5. 보유 포인트보다 큰 금액 → 400 (서버가 다시 확인, C-6.1.3)
6. 같은 방에 같은 세션이 두 번 참여 → 400 (C-6.1.2)
7. 4명 초과 참여 → 400 (C-6.1.1)
8. 마감 시각(판가름 날짜 전 영업일 15:30) 이후 참여 → 400 (C-6.1.4)
9. 정상 참여 시 포인트 차감 + 방 집계 반영
"""

from datetime import date, timedelta

from app.db import SessionLocal
from app.models import PointLedger
from app.services.rooms import _add_business_days, create_room
from app.services.sessions import ensure_session


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _grant_points_for_client(client, amount: int = 10_000) -> None:
    session_id = client.cookies.get("assit_session")
    with SessionLocal() as db:
        db.add(PointLedger(session_id=session_id, kind="charge", amount=amount))
        db.commit()


def _make_room(
    db, *, stock_code="111110", amount=100, judge_date_=None, target_price=10000
) -> dict:
    """target_price를 호출마다 다르게 줘야 중복 방 차단(C-4.1.5)에 안 걸린다."""
    creator, _ = ensure_session(db, None)
    creator.authenticated = True
    db.add(PointLedger(session_id=creator.id, kind="charge", amount=10_000))
    db.commit()
    return create_room(
        db,
        creator,
        stock_code=stock_code,
        title="테스트방",
        target_price=target_price,
        judge_date_=judge_date_ or _add_business_days(date.today(), 5),
        body=None,
        amount=amount,
    )


def test_entry_requires_login(client):
    with SessionLocal() as db:
        room = _make_room(db, target_price=51000)
    client.post("/session")
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 100})
    assert (r.status_code, r.json()["code"]) == (401, "login_required")


def test_entry_unknown_room(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    r = client.post("/rooms/999999/entries", json={"side": "down", "amount": 100})
    assert (r.status_code, r.json()["code"]) == (404, "unknown_room")


def test_entry_invalid_side(client, login_env):
    with SessionLocal() as db:
        room = _make_room(db, stock_code="222220", target_price=52000)
    _login(client)
    _grant_points_for_client(client)
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "sideways", "amount": 100})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_side")


def test_entry_amount_out_of_range(client, login_env):
    with SessionLocal() as db:
        room = _make_room(db, stock_code="333330", target_price=53000)
    _login(client)
    _grant_points_for_client(client)
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 50})
    assert r.status_code == 422  # 스키마 레벨 검증(Field ge/le)

    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 1500})
    assert r.status_code == 422


def test_entry_insufficient_points(client, login_env):
    with SessionLocal() as db:
        room = _make_room(db, stock_code="TSLA", target_price=54000)
    _login(client)  # 포인트 지급 안 함
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 500})
    assert (r.status_code, r.json()["code"]) == (400, "insufficient_points")


def test_entry_duplicate_blocked(client, login_env):
    with SessionLocal() as db:
        room = _make_room(db, stock_code="555550", target_price=55000)
    _login(client)
    _grant_points_for_client(client)
    r1 = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 300})
    assert r1.status_code == 200
    r2 = client.post(f"/rooms/{room['id']}/entries", json={"side": "up", "amount": 100})
    assert (r2.status_code, r2.json()["code"]) == (400, "already_entered")


def test_entry_room_full(client, login_env):
    """방당 최대 4명(생성자 포함) — 생성자 1 + 참여자 3으로 채운 뒤 4번째가 막힌다."""
    with SessionLocal() as db:
        room = _make_room(db, stock_code="111115", target_price=56000)
        for _i in range(3):
            s, _ = ensure_session(db, None)
            s.authenticated = True
            db.add(PointLedger(session_id=s.id, kind="charge", amount=1000))
            db.commit()
            from app.services.betting import place_entry

            place_entry(db, s, room["id"], side="down", amount=100)

    _login(client)
    _grant_points_for_client(client)
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 100})
    assert (r.status_code, r.json()["code"]) == (400, "room_full")


def test_entry_deadline_passed():
    """마감(판가름 날짜 전 영업일 15:30) 이후 참여는 서비스 레벨에서 막힌다(C-6.1.4)."""
    from app.errors import AppError
    from app.services.betting import entry_deadline, place_entry

    with SessionLocal() as db:
        judge_date_ = _add_business_days(date.today(), 5)
        room = _make_room(db, stock_code="222220", judge_date_=judge_date_, target_price=57000)
        s, _ = ensure_session(db, None)
        s.authenticated = True
        db.add(PointLedger(session_id=s.id, kind="charge", amount=1000))
        db.commit()

        after_deadline = entry_deadline(judge_date_) + timedelta(minutes=1)
        try:
            place_entry(db, s, room["id"], side="down", amount=100, now=after_deadline)
            raise AssertionError("마감 이후인데 참여가 통과했다")
        except AppError as e:
            assert e.code == "entry_closed"


def test_entry_success_updates_balance_and_room(client, login_env):
    with SessionLocal() as db:
        room = _make_room(db, stock_code="333330", target_price=58000)
    _login(client)
    _grant_points_for_client(client)
    r = client.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 300})
    assert r.status_code == 200
    detail = r.json()["room"]
    assert detail["down"] == {"count": 1, "points": 300}
    assert client.get("/me/points").json()["balance"] == 9_700
