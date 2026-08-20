"""E-7 경제 상식 카드 조회/관리 API 테스트.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 회전 전(EconRotation 없음) → 빈 목록 + rotated_at null (500 아님)
2. 회전 후 → 회전 당시 순서대로 title만 반환, rotated_at 포함
3. 상세 조회 — 승인 안 된 카드는 404(없는 것과 동일 취급, E-5.6)
4. 존재하지 않는 카드 상세 → 404
5. 관리자 토큰 없거나 틀림 → generate/patch 둘 다 401
6. 배치 생성 트리거(HTTP) — 실제 LLM 호출은 respx로 목킹
7. PATCH approve/reject/lock/unlock 각각 상태 반영
8. 없는 카드 PATCH → 404
"""

import respx
from httpx import Response

from app.db import SessionLocal
from app.models import EconCard, EconRotation

ADMIN_HEADERS = {"X-Admin-Token": "admin-secret"}


def _make_card(db, title="카드", status="approved"):
    c = EconCard(
        title=title,
        body="본문이에요.(1)",
        hard_terms=None,
        sources=[{"number": 1, "org": "한국은행", "doc_title": "문서", "url": "https://bok.or.kr/x"}],
        batch_id="b",
        status=status,
        locked=(status == "approved"),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _clear_econ(db):
    """current_rotation()은 배치와 무관하게 '가장 최근 회전'을 본다 — 다른 테스트가 만든
    회전 기록과 섞이지 않도록 비운다."""
    db.query(EconRotation).delete()
    db.query(EconCard).delete()
    db.commit()


def test_list_before_any_rotation_returns_empty(client):
    with SessionLocal() as db:
        _clear_econ(db)
    r = client.get("/econ-cards")
    assert r.status_code == 200
    assert r.json() == {"items": [], "rotated_at": None}


def test_list_after_rotation_returns_titles_in_order(client):
    with SessionLocal() as db:
        _clear_econ(db)
        c1 = _make_card(db, "첫번째")
        c2 = _make_card(db, "두번째")
        db.add(EconRotation(card_ids=[c2.id, c1.id]))  # 순서 뒤집어서 기록
        db.commit()

    r = client.get("/econ-cards")
    body = r.json()
    assert [i["title"] for i in body["items"]] == ["두번째", "첫번째"]
    assert body["rotated_at"] is not None


def test_detail_of_approved_card(client):
    with SessionLocal() as db:
        c = _make_card(db, "상세용")
    r = client.get(f"/econ-card/{c.id}")
    assert r.status_code == 200
    assert r.json()["title"] == "상세용"
    assert r.json()["hard_terms"] is None
    assert r.json()["sources"][0]["org"] == "한국은행"


def test_detail_exposes_hard_terms(client):
    with SessionLocal() as db:
        c = _make_card(db, "어려운 용어")
        c.hard_terms = ["기준금리"]
        db.commit()
        card_id = c.id
    r = client.get(f"/econ-card/{card_id}")
    assert r.status_code == 200
    assert r.json()["hard_terms"] == ["기준금리"]


def test_detail_of_non_approved_card_is_404(client):
    with SessionLocal() as db:
        c = _make_card(db, "미승인", status="filtered")
    r = client.get(f"/econ-card/{c.id}")
    assert (r.status_code, r.json()["code"]) == (404, "unknown_econ_card")


def test_detail_of_missing_card_is_404(client):
    r = client.get("/econ-card/999999")
    assert (r.status_code, r.json()["code"]) == (404, "unknown_econ_card")


def test_admin_endpoints_require_token(client):
    r = client.post("/admin/econ-cards/generate", json={"count": 1})
    assert (r.status_code, r.json()["code"]) == (401, "admin_token_invalid")

    with SessionLocal() as db:
        c = _make_card(db)
    r = client.patch(f"/admin/econ-cards/{c.id}", json={"action": "lock"})
    assert (r.status_code, r.json()["code"]) == (401, "admin_token_invalid")

    r = client.patch(
        f"/admin/econ-cards/{c.id}", json={"action": "lock"}, headers={"X-Admin-Token": "wrong"}
    )
    assert r.status_code == 401


@respx.mock
def test_generate_endpoint_triggers_batch(client, login_env):
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            '{"title":"시가총액이 뭔가요?",'
                            '"body":"시가총액은 주가와 발행주식수를 곱한 값이에요.(1)",'
                            '"sources":[{"number":1,"org":"한국거래소",'
                            '"doc_title":"용어해설","url":"https://krx.co.kr/x"}]}'
                        ),
                    }
                ],
            }
        ]
    }
    respx.post("https://api.openai.com/v1/responses").mock(return_value=Response(200, json=payload))
    respx.head("https://krx.co.kr/x").mock(return_value=Response(200))

    with SessionLocal() as db:
        _clear_econ(db)

    # BackgroundTasks — 요청 자체는 즉시 202(승래 리뷰), TestClient는 응답 후 동기 실행한다
    r = client.post("/admin/econ-cards/generate", json={"count": 1}, headers=ADMIN_HEADERS)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "accepted" and body["batch_id"]

    with SessionLocal() as db:
        card = db.query(EconCard).filter(EconCard.batch_id == body["batch_id"]).one()
        assert card.status == "filtered"


def test_generate_endpoint_rejects_out_of_range_count(client, login_env):
    r = client.post("/admin/econ-cards/generate", json={"count": 21}, headers=ADMIN_HEADERS)
    assert r.status_code == 422
    r = client.post("/admin/econ-cards/generate", json={"count": 0}, headers=ADMIN_HEADERS)
    assert r.status_code == 422


def test_patch_approve_reject_lock_unlock(client, login_env):
    with SessionLocal() as db:
        c = _make_card(db, status="filtered")
        card_id = c.id

    r = client.patch(
        f"/admin/econ-cards/{card_id}", json={"action": "approve"}, headers=ADMIN_HEADERS
    )
    assert r.json() == {"id": card_id, "status": "approved", "locked": True}

    r = client.patch(
        f"/admin/econ-cards/{card_id}", json={"action": "unlock"}, headers=ADMIN_HEADERS
    )
    assert r.json()["locked"] is False

    r = client.patch(
        f"/admin/econ-cards/{card_id}", json={"action": "lock"}, headers=ADMIN_HEADERS
    )
    assert r.json()["locked"] is True

    r = client.patch(
        f"/admin/econ-cards/{card_id}", json={"action": "reject"}, headers=ADMIN_HEADERS
    )
    assert r.json()["status"] == "rejected"


def test_patch_missing_card_404(client, login_env):
    r = client.patch("/admin/econ-cards/999999", json={"action": "lock"}, headers=ADMIN_HEADERS)
    assert (r.status_code, r.json()["code"]) == (404, "unknown_econ_card")
