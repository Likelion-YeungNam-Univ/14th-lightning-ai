"""#95 — 방 삭제 + 방별 최대 참여 인원(2~4) 테스트.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 남의 방 삭제 / 정산된 방 삭제 → 403 / 400
2. 타인이 참여한 방을 생성자가 무름(상대 베팅 강제 취소) → 400 room_has_entrants
3. 삭제 후 포인트 유실(생성자 자동 베팅이 증발) → 환급으로 원장 합계 복원
4. 삭제 후 방·참여·댓글 잔재 / 목록 노출 → CASCADE + 404
5. 정원 범위 밖(1명·5명) → 422, 미지정 → 기본 4
6. 2명 방이 4명 기준으로 동작(정원 미적용) → 2명 차면 room_full
7. 마이그레이션 전 기존 방(max_entrants NULL) → 4로 간주
8. 비로그인 삭제 → 401
"""

from app.db import SessionLocal
from app.models import BettingEntry, BettingRoom, RoomComment
from tests.test_rooms import _grant_points_for_client, _login, _valid_judge_date


def _create(client, **over):
    payload = {
        "stock_code": "111110",
        "title": over.pop("title", "정원 테스트"),
        "target_price": over.pop("target_price", 20000),
        "judge_date": _valid_judge_date(),
        "amount": 100,
        **over,
    }
    return client.post("/rooms", json=payload)


def _fresh_bettor(client, amount=5_000):
    other = client.__class__(client.app)
    other.post("/session")
    other.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    _grant_points_for_client(other, amount)
    return other


# ── 정원(2~4) ─────────────────────────────────────────────────────────


def test_capacity_default_and_bounds(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    r = _create(client, title="기본 정원", target_price=21000)
    assert r.status_code == 200
    assert r.json()["room"]["max_participants"] == 4  # 미지정 → 기본 4

    assert _create(client, title="5명", target_price=22000, max_participants=5).status_code == 422
    assert _create(client, title="1명", target_price=23000, max_participants=1).status_code == 422


def test_two_person_room_fills_at_two(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    room = _create(client, title="2인 방", target_price=24000, max_participants=2).json()["room"]
    assert room["max_participants"] == 2 and room["participant_count"] == 1  # 생성자 포함

    second = _fresh_bettor(client)
    r = second.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 100})
    assert r.status_code == 200
    assert r.json()["room"]["participant_count"] == 2

    third = _fresh_bettor(client)
    r = third.post(f"/rooms/{room['id']}/entries", json={"side": "down", "amount": 100})
    assert (r.status_code, r.json()["code"]) == (400, "room_full")  # 예상 문제 6


def test_legacy_room_null_capacity_counts_as_four(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    room_id = _create(client, title="레거시", target_price=25000).json()["room"]["id"]
    with SessionLocal() as db:  # 마이그레이션 전 기존 방 시뮬레이션
        db.get(BettingRoom, room_id).max_entrants = None
        db.commit()
    r = client.get(f"/rooms/{room_id}")
    assert r.json()["max_participants"] == 4  # 예상 문제 7


# ── 방 삭제 ───────────────────────────────────────────────────────────


def test_delete_requires_login_and_owner(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    room_id = _create(client, title="삭제 권한", target_price=26000).json()["room"]["id"]

    anon = client.__class__(client.app)
    anon.post("/session")
    r = anon.delete(f"/rooms/{room_id}")
    assert (r.status_code, r.json()["code"]) == (401, "login_required")  # 예상 문제 8

    stranger = _fresh_bettor(client)
    r = stranger.delete(f"/rooms/{room_id}")
    assert (r.status_code, r.json()["code"]) == (403, "not_room_owner")  # 예상 문제 1


def test_delete_blocked_when_others_joined(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    room_id = _create(client, title="참여자 있음", target_price=27000).json()["room"]["id"]
    second = _fresh_bettor(client)
    second.post(f"/rooms/{room_id}/entries", json={"side": "down", "amount": 100})

    r = client.delete(f"/rooms/{room_id}")
    assert (r.status_code, r.json()["code"]) == (400, "room_has_entrants")  # 예상 문제 2


def test_delete_refunds_and_removes(client, login_env):
    _login(client)
    _grant_points_for_client(client, 1_000)
    before = client.get("/me/points").json()["balance"]
    created = _create(client, title="삭제 대상", target_price=28000, amount=300).json()["room"]
    room_id = created["id"]
    assert client.get("/me/points").json()["balance"] == before - 300  # 자동 베팅 차감
    client.post(f"/rooms/{room_id}/comments", json={"body": "삭제될 댓글"})

    r = client.delete(f"/rooms/{room_id}")
    assert r.status_code == 200 and r.json()["removed"] is True

    assert client.get("/me/points").json()["balance"] == before  # 예상 문제 3 — 환급 완료
    assert client.get(f"/rooms/{room_id}").status_code == 404  # 예상 문제 4
    listed = client.get("/rooms", params={"stock_code": "111110"}).json()["items"]
    assert room_id not in [x["id"] for x in listed]
    with SessionLocal() as db:  # CASCADE — 참여·댓글 잔재 없음
        assert db.query(BettingEntry).filter_by(room_id=room_id).count() == 0
        assert db.query(RoomComment).filter_by(room_id=room_id).count() == 0

    r2 = client.delete(f"/rooms/{room_id}")  # 재삭제 — 404 (멱등 아님을 명시적으로)
    assert r2.status_code == 404


def test_delete_settled_room_blocked(client, login_env):
    _login(client)
    _grant_points_for_client(client)
    room_id = _create(client, title="정산됨", target_price=29000).json()["room"]["id"]
    with SessionLocal() as db:
        db.get(BettingRoom, room_id).status = "closed"
        db.commit()
    r = client.delete(f"/rooms/{room_id}")
    assert (r.status_code, r.json()["code"]) == (400, "room_not_open")  # 예상 문제 1
