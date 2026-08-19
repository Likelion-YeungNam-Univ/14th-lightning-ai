"""C-7 베팅방 댓글 테스트.

예상 문제 지점:
1. 없는 방에 댓글 목록/작성 → 404
2. 비로그인 작성/삭제 → 401
3. 참여자 댓글엔 진영 배지, 미참여자는 side=None(프론트가 [미참여] 표시, C-7.1.1)
4. 최신순 정렬 고정(C-7.1.2)
5. 자료 카드 첨부 — 본인 저장 카드만 가능, 타인 카드 첨부 시 400
6. 삭제는 본인만(C-7.3), 삭제된 댓글은 body=None + deleted=True로 남는다(플레이스홀더)
7. 이미 삭제된 댓글을 다시 삭제하면 멱등(removed=False)
"""

from datetime import date

from app.db import SessionLocal
from app.models import PointLedger
from app.services.rooms import _add_business_days, create_room
from app.services.sessions import ensure_session


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _make_room(db, *, stock_code="111110", target_price=81000) -> int:
    creator, _ = ensure_session(db, None)
    creator.authenticated = True
    db.add(PointLedger(session_id=creator.id, kind="charge", amount=10_000))
    db.commit()
    room = create_room(
        db,
        creator,
        stock_code=stock_code,
        title="댓글테스트방",
        target_price=target_price,
        judge_date_=_add_business_days(date.today(), 5),
        body=None,
        amount=100,
    )
    return room["id"]


def test_comment_unknown_room(client, login_env):
    r = client.get("/rooms/999999/comments")
    assert (r.status_code, r.json()["code"]) == (404, "unknown_room")

    _login(client)
    r = client.post("/rooms/999999/comments", json={"body": "안녕"})
    assert (r.status_code, r.json()["code"]) == (404, "unknown_room")


def test_comment_requires_login_to_write(client):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="111110", target_price=81000)
    client.post("/session")
    r = client.post(f"/rooms/{room_id}/comments", json={"body": "안녕"})
    assert (r.status_code, r.json()["code"]) == (401, "login_required")


def test_comment_read_open_without_login(client):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="222220", target_price=82000)
    r = client.get(f"/rooms/{room_id}/comments")
    assert r.status_code == 200


def test_comment_side_badge_and_unparticipated(client, login_env):
    """참여자는 side 배지, 미참여자는 side=None(C-7.1.1)."""
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="333330", target_price=83000)

    _login(client)  # 이 세션은 이 방에 참여한 적 없음
    r = client.post(f"/rooms/{room_id}/comments", json={"body": "미참여자 댓글"})
    assert r.status_code == 200
    assert r.json()["item"]["side"] is None

    with SessionLocal() as db:
        from app.models import BettingRoom, RoomComment

        room = db.get(BettingRoom, room_id)
        assert room.creator_session_id is not None
        db.add(RoomComment(room_id=room_id, session_id=room.creator_session_id, body="생성자 댓글"))
        db.commit()

    items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    creator_comment = next(c for c in items if c["body"] == "생성자 댓글")
    assert creator_comment["side"] == "up"  # 생성자는 자동 참여(up)


def test_comment_sorted_newest_first(client, login_env):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="AAPL", target_price=84000)
    _login(client)
    client.post(f"/rooms/{room_id}/comments", json={"body": "첫번째"})
    client.post(f"/rooms/{room_id}/comments", json={"body": "두번째"})
    items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    assert [c["body"] for c in items[:2]] == ["두번째", "첫번째"]


def test_comment_attach_saved_card(client, login_env):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="555550", target_price=85000)
    _login(client)

    from app.models import SourceItem
    from app.services.industry import seed_form_types
    from tests.test_markets_cards import _seed_card_data

    with SessionLocal() as db:
        seed_form_types(db)
        _seed_card_data(db)
        card_source_id = db.query(SourceItem.id).filter(SourceItem.source_key == "card-d1").scalar()

    save_r = client.post(
        "/me/saved-cards", json={"card_id": card_source_id, "stock_code": "111110"}
    )
    assert save_r.status_code == 200, save_r.text
    real_saved_card_id = save_r.json()["item"]["id"]  # saved_card.id — 첨부는 이 값을 쓴다

    # attachment 목적 조회 (C-5.1) — 기존 엔드포인트를 그대로 쓴다
    attach_list = client.get("/me/saved-cards", params={"for": "attachment"}).json()["items"]
    assert any(i["id"] == real_saved_card_id for i in attach_list)

    r = client.post(
        f"/rooms/{room_id}/comments",
        json={"body": "자료 참고", "saved_card_id": real_saved_card_id},
    )
    assert r.status_code == 200
    assert r.json()["item"]["saved_card_snapshot"] is not None


def test_comment_attach_other_users_card_blocked(client, login_env):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="111115", target_price=86000)
        from app.services.sessions import ensure_session as _es

        other, _ = _es(db, None)
        other.authenticated = True
        db.commit()

    from app.models import SourceItem
    from app.services.industry import seed_form_types
    from tests.test_markets_cards import _seed_card_data

    with SessionLocal() as db:
        seed_form_types(db)
        _seed_card_data(db)
        from app.models import SavedCard

        card_source_id = db.query(SourceItem.id).filter(SourceItem.source_key == "card-d1").scalar()
        other_card = SavedCard(
            session_id=other.id,
            source_item_id=card_source_id,
            tab="disclosure",
            stock_code="111110",
            snapshot_json={"title": "타인 카드"},
        )
        db.add(other_card)
        db.commit()
        other_card_id = other_card.id

    _login(client)
    r = client.post(
        f"/rooms/{room_id}/comments",
        json={"body": "훔친 카드", "saved_card_id": other_card_id},
    )
    assert (r.status_code, r.json()["code"]) == (400, "unknown_saved_card")


def test_comment_delete_only_by_author(client, login_env):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="222220", target_price=87000)
    _login(client)
    r = client.post(f"/rooms/{room_id}/comments", json={"body": "지울댓글"})
    comment_id = r.json()["item"]["id"]

    # 다른 세션으로 삭제 시도 → 403
    with SessionLocal() as db:
        from app.services.comments import delete_comment as svc_delete
        from app.services.sessions import ensure_session as _es

        other, _ = _es(db, None)
        other.authenticated = True
        db.commit()
        try:
            svc_delete(db, other, comment_id)
            raise AssertionError("타인이 삭제했는데 통과했다")
        except Exception as e:
            assert getattr(e, "code", None) == "not_comment_owner"

    del_r = client.delete(f"/comments/{comment_id}")
    assert del_r.status_code == 200
    assert del_r.json()["removed"] is True

    items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    target = next(c for c in items if c["id"] == comment_id)
    assert target["deleted"] is True and target["body"] is None

    # 이미 삭제된 걸 다시 삭제 → 멱등 false
    del_r2 = client.delete(f"/comments/{comment_id}")
    assert del_r2.json()["removed"] is False


def test_comment_session_id_never_leaks(client, login_env):
    """승래 리뷰 B-1 — 응답 어디에도 session_id 원문이 실리면 안 된다(세션 탈취 재현 지점)."""
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="TSLA", target_price=88000)
    _login(client)
    session_id = client.cookies.get("assit_session")
    r = client.post(f"/rooms/{room_id}/comments", json={"body": "내 댓글"})
    assert r.status_code == 200
    assert session_id not in r.text  # raw body 어디에도 없어야 한다

    list_r = client.get(f"/rooms/{room_id}/comments")
    assert session_id not in list_r.text
    item = list_r.json()["items"][0]
    assert "session_id" not in item
    assert item["is_mine"] is True
    assert len(item["author_tag"]) == 6  # HMAC 축약 태그


def test_comment_is_mine_false_for_others_and_anonymous(client, login_env):
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="555550", target_price=89000)
    _login(client)
    client.post(f"/rooms/{room_id}/comments", json={"body": "작성자 댓글"})

    # 익명(비로그인) 조회 — is_mine 항상 false
    anon_items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    # 새 클라이언트가 없으니 같은 client로 세션만 새로 발급해 비교
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as other_client:
        other_items = other_client.get(f"/rooms/{room_id}/comments").json()["items"]
        assert other_items[0]["is_mine"] is False

    assert anon_items[0]["is_mine"] is True  # 원래 client는 작성자 세션을 유지 중


def test_comment_snapshot_survives_unsave(client, login_env):
    """승래 리뷰 B-3 — 저장 해제해도 이미 첨부된 댓글의 스냅샷은 그대로 남는다."""
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="111110", target_price=90000)
    _login(client)

    from app.models import SourceItem
    from app.services.industry import seed_form_types
    from tests.test_markets_cards import _seed_card_data

    with SessionLocal() as db:
        seed_form_types(db)
        _seed_card_data(db)
        card_source_id = db.query(SourceItem.id).filter(SourceItem.source_key == "card-d1").scalar()

    client.post("/me/saved-cards", json={"card_id": card_source_id, "stock_code": "111110"})
    with SessionLocal() as db:
        from app.models import SavedCard

        session_id = client.cookies.get("assit_session")
        row = (
            db.query(SavedCard)
            .filter(SavedCard.session_id == session_id, SavedCard.source_item_id == card_source_id)
            .one()
        )
        real_saved_card_id = row.id

    r = client.post(
        f"/rooms/{room_id}/comments",
        json={"body": "근거자료", "saved_card_id": real_saved_card_id},
    )
    comment_id = r.json()["item"]["id"]

    client.delete(f"/me/saved-cards/{card_source_id}")  # 저장 해제

    items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    target = next(c for c in items if c["id"] == comment_id)
    assert target["saved_card_snapshot"] is not None  # 여전히 남아있어야 한다


def test_comment_deleted_hides_snapshot_too(client, login_env):
    """승래 리뷰 B-4 — 삭제된 댓글은 첨부 스냅샷도 함께 감춘다."""
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="222220", target_price=91000)
    _login(client)

    from app.models import SourceItem
    from app.services.industry import seed_form_types
    from tests.test_markets_cards import _seed_card_data

    with SessionLocal() as db:
        seed_form_types(db)
        _seed_card_data(db)
        card_source_id = db.query(SourceItem.id).filter(SourceItem.source_key == "card-d1").scalar()
    client.post("/me/saved-cards", json={"card_id": card_source_id, "stock_code": "111110"})
    with SessionLocal() as db:
        from app.models import SavedCard

        session_id = client.cookies.get("assit_session")
        real_saved_card_id = (
            db.query(SavedCard)
            .filter(SavedCard.session_id == session_id, SavedCard.source_item_id == card_source_id)
            .one()
            .id
        )

    r = client.post(
        f"/rooms/{room_id}/comments",
        json={"body": "지울 근거자료", "saved_card_id": real_saved_card_id},
    )
    comment_id = r.json()["item"]["id"]
    client.delete(f"/comments/{comment_id}")

    items = client.get(f"/rooms/{room_id}/comments").json()["items"]
    target = next(c for c in items if c["id"] == comment_id)
    assert target["deleted"] is True
    assert target["saved_card_snapshot"] is None


def test_comment_body_blank_rejected(client, login_env):
    """권고 반영 — 공백만으로는 댓글을 남길 수 없다."""
    with SessionLocal() as db:
        room_id = _make_room(db, stock_code="333330", target_price=92000)
    _login(client)
    r = client.post(f"/rooms/{room_id}/comments", json={"body": "   "})
    assert r.status_code == 422
