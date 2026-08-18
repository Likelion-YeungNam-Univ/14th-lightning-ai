"""C-1~C-4 커뮤니티 탭 베팅방 테스트.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 비로그인 생성 → 401
2. 없는 종목코드로 생성 → 400
3. 목표가 1,000원 단위 아님 → 400
4. 판가름 날짜가 최소 리드(3영업일) 미만/최대(90일) 초과/주말 → 400
5. 정상 생성 시 생성자가 up 진영에 자동 참여(C-4.1.2) → 목록·상세에 반영, 포인트 차감(C-8)
6. 같은 종목·목표가·판가름날짜 중복 방 → 400
7. 동시 진행 3개 초과 / 하루 생성 5개 초과 → 400
8. 목록 정렬(판가름 날짜 가까운 순)·기본 status=open 필터
9. 우세 진영 계산 — 포인트 비중, 동률이면 up
10. 존재하지 않는 방 상세 → 404
11. 차트 심볼 — 국내 KRX:, 해외 화이트리스트 매핑, 화이트리스트 밖은 NASDAQ 기본값, 없는 종목 404
12. 포인트 부족으로 생성 시 방 생성 자체가 막힌다(C-6.1.3 — 생성자 자동 참여도 실제 베팅)
"""

from datetime import date, timedelta

from app.db import SessionLocal
from app.services.rooms import _add_business_days, create_room


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _valid_judge_date() -> str:
    return str(_add_business_days(date.today(), 5))


def _grant_points(session_id: str, amount: int = 10_000) -> None:
    """테스트 전용 — 결제 없이 포인트를 바로 적립한다(원장에 charge 행 추가)."""
    from app.models import PointLedger

    with SessionLocal() as db:
        db.add(PointLedger(session_id=session_id, kind="charge", amount=amount))
        db.commit()


def _grant_points_for_client(client, amount: int = 10_000) -> None:
    session_id = client.cookies.get("assit_session")
    _grant_points(session_id, amount)


def test_auth_gate(client):
    client.post("/session")  # 비로그인
    r = client.post(
        "/rooms",
        json={
            "stock_code": "111110",
            "title": "제목",
            "target_price": 10000,
            "judge_date": _valid_judge_date(),
            "amount": 500,
        },
    )
    assert (r.status_code, r.json()["code"]) == (401, "login_required")


def test_create_without_points_blocked(client, login_env):
    """C-6.1.3 — 생성자 자동 참여도 실제 베팅이라 포인트가 없으면 방 생성 자체가 막힌다."""
    _login(client)
    r = client.post(
        "/rooms",
        json={
            "stock_code": "111110",
            "title": "포인트없음",
            "target_price": 10000,
            "judge_date": _valid_judge_date(),
            "amount": 500,
        },
    )
    assert (r.status_code, r.json()["code"]) == (400, "insufficient_points")


def test_create_validations(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    base = {
        "title": "제목",
        "target_price": 10000,
        "judge_date": _valid_judge_date(),
        "amount": 500,
    }
    r = client.post("/rooms", json={**base, "stock_code": "999999"})
    assert (r.status_code, r.json()["code"]) == (400, "unknown_stock")

    r = client.post("/rooms", json={**base, "stock_code": "111110", "target_price": 10500})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_target_price")

    too_soon = str(date.today() + timedelta(days=1))
    r = client.post("/rooms", json={**base, "stock_code": "111110", "judge_date": too_soon})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_judge_date")

    too_far = str(date.today() + timedelta(days=200))
    r = client.post("/rooms", json={**base, "stock_code": "111110", "judge_date": too_far})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_judge_date")


def test_overseas_target_price_skips_1000_unit_check(client, login_env):
    """해외 종목은 달러 표시라 1,000원 단위 검증을 적용하지 않는다(승래 리뷰 반영)."""
    _login(client)
    _grant_points_for_client(client)
    r = client.post(
        "/rooms",
        json={
            "stock_code": "TSLA",
            "title": "테슬라 250.5달러",
            "target_price": 251,  # 1,000 단위가 아니어도 통과해야 한다
            "judge_date": _valid_judge_date(),
            "amount": 500,
        },
    )
    assert r.status_code == 200
    assert r.json()["room"]["target_price"] == 251


def test_create_room_auto_joins_creator(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    r = client.post(
        "/rooms",
        json={
            "stock_code": "111110",
            "title": "가나전자 10000 갈까요",
            "target_price": 10000,
            "judge_date": _valid_judge_date(),
            "amount": 500,
        },
    )
    assert r.status_code == 200
    room = r.json()["room"]
    assert room["status"] == "open"
    assert room["up"] == {"count": 1, "points": 500}
    assert room["down"] == {"count": 0, "points": 0}
    assert room["leading_side"] == "up"
    assert room["participant_count"] == 1

    detail = client.get(f"/rooms/{room['id']}").json()
    assert detail["stock_code"] == "111110"
    assert detail["title"] == "가나전자 10000 갈까요"

    # 포인트가 실제로 차감됐다(C-8.3) — 10,000 지급 - 500 베팅 = 9,500
    assert client.get("/me/points").json()["balance"] == 9_500


def test_duplicate_room_blocked(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    payload = {
        "stock_code": "111115",
        "title": "방1",
        "target_price": 10000,
        "judge_date": _valid_judge_date(),
        "amount": 500,
    }
    r1 = client.post("/rooms", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/rooms", json={**payload, "title": "방2"})
    assert (r2.status_code, r2.json()["code"]) == (400, "duplicate_room")


def test_room_limits(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    jd = _valid_judge_date()
    for price in (10000, 20000, 30000):
        r = client.post(
            "/rooms",
            json={
                "stock_code": "222220",
                "title": f"방{price}",
                "target_price": price,
                "judge_date": jd,
                "amount": 500,
            },
        )
        assert r.status_code == 200
    r = client.post(
        "/rooms",
        json={
            "stock_code": "222220",
            "title": "네번째",
            "target_price": 40000,
            "judge_date": jd,
            "amount": 500,
        },
    )
    assert (r.status_code, r.json()["code"]) == (400, "room_limit_exceeded")


def test_daily_room_limit_via_service(login_env):
    """하루 생성 5개 한도 — 동시 진행 상한(3개)과 안 겹치게 방을 닫아가며 검증한다."""
    with SessionLocal() as db:
        from app.models import PointLedger
        from app.services.sessions import ensure_session

        session, _ = ensure_session(db, None)
        session.authenticated = True
        db.add(PointLedger(session_id=session.id, kind="charge", amount=10_000))
        db.commit()
        jd = _add_business_days(date.today(), 5)
        for i in range(5):
            room = create_room(
                db,
                session,
                stock_code="333330",
                title=f"방{i}",
                target_price=10000 + i * 1000,
                judge_date_=jd,
                body=None,
                amount=100,
            )
            from app.models import BettingRoom

            db.get(BettingRoom, room["id"]).status = "closed"  # 동시 진행 상한을 안 건드리게 닫음
            db.commit()
        try:
            create_room(
                db,
                session,
                stock_code="333330",
                title="6번째",
                target_price=20000,
                judge_date_=jd,
                body=None,
                amount=100,
            )
            raise AssertionError("하루 한도를 넘겼는데 통과했다")
        except Exception as e:
            assert getattr(e, "code", None) == "room_daily_limit_exceeded"


def test_list_sorted_and_default_status_open(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    near = _valid_judge_date()
    far = str(_add_business_days(date.today(), 10))
    client.post(
        "/rooms",
        json={
            "stock_code": "444440",
            "title": "먼방",
            "target_price": 10000,
            "judge_date": far,
            "amount": 500,
        },
    )
    client.post(
        "/rooms",
        json={
            "stock_code": "444440",
            "title": "가까운방",
            "target_price": 20000,
            "judge_date": near,
            "amount": 500,
        },
    )
    items = client.get("/rooms", params={"stock_code": "444440"}).json()["items"]
    assert [i["title"] for i in items] == ["가까운방", "먼방"]
    assert all(i["status"] == "open" for i in items)


def test_leading_side_by_points_not_headcount(client, login_env):
    """진영별 포인트 비중으로 우세를 계산한다 — 인원이 아니라(C-3.1.3)."""
    with SessionLocal() as db:
        from app.models import BettingEntry, PointLedger
        from app.services.sessions import ensure_session

        creator, _ = ensure_session(db, None)
        creator.authenticated = True
        db.add(PointLedger(session_id=creator.id, kind="charge", amount=10_000))
        db.commit()
        room = create_room(
            db,
            creator,
            stock_code="555550",
            title="포인트비중테스트",
            target_price=10000,
            judge_date_=_add_business_days(date.today(), 5),
            body=None,
            amount=100,  # up: 100P 1명
        )
        for _i in range(3):  # down: 3명이지만 소액씩만
            s, _ = ensure_session(db, None)
            db.add(BettingEntry(room_id=room["id"], session_id=s.id, side="down", amount=10))
        db.commit()
        detail = get_room_detail(db, room["id"])
        assert detail["down"]["count"] == 3 and detail["down"]["points"] == 30
        assert detail["up"]["points"] == 100 > detail["down"]["points"]
        assert detail["leading_side"] == "up"


def get_room_detail(db, room_id):
    from app.services.rooms import get_room

    return get_room(db, room_id)


def test_room_not_found(client, login_env):
    r = client.get("/rooms/999999")
    assert (r.status_code, r.json()["code"]) == (404, "unknown_room")


def test_chart_symbol(client):
    r = client.get("/stocks/111110/chart-symbol")
    assert r.json()["symbol"] == "KRX:111110"

    r = client.get("/stocks/TSLA/chart-symbol")
    assert r.json()["symbol"] == "NASDAQ:TSLA"

    r = client.get("/stocks/UNKNOWN999/chart-symbol")
    assert r.status_code == 404


def test_daily_limit_across_kst_midnight(client, login_env):
    """KST 00~09시: created_at(UTC)은 전날, today(KST)는 오늘 — 한도가 리셋되면 안 된다.

    func.date(created_at) == today 비교가 이 구간에서 0건으로 계산돼 CI가 그 시간대에 실패했음.
    """
    from datetime import datetime

    from app.models import BettingRoom, PointLedger
    from app.services.sessions import ensure_session

    with SessionLocal() as db:
        session, _ = ensure_session(db, None)
        session.authenticated = True
        db.add(PointLedger(session_id=session.id, kind="charge", amount=10_000))
        db.commit()
        kst_today = date(2026, 8, 19)  # KST 08:00 상황
        utc_now = datetime(2026, 8, 18, 23, 0)  # 같은 순간의 UTC = 전날 23시
        jd = _add_business_days(kst_today, 5)
        for i in range(5):
            room = create_room(
                db,
                session,
                stock_code="333330",
                title=f"r{i}",
                target_price=10000 + i * 1000,
                judge_date_=jd,
                body=None,
                amount=100,
                today=kst_today,
            )
            r = db.get(BettingRoom, room["id"])
            r.status = "closed"
            r.created_at = utc_now  # DB가 UTC로 찍은 것처럼
            db.commit()
        try:
            create_room(
                db,
                session,
                stock_code="333330",
                title="6th",
                target_price=20000,
                judge_date_=jd,
                body=None,
                amount=100,
                today=kst_today,
            )
        except Exception as e:
            assert getattr(e, "code", None) == "room_daily_limit_exceeded"
        else:
            raise AssertionError("KST 자정 넘긴 구간에서 한도가 리셋됨")
