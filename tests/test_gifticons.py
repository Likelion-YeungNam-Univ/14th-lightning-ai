"""C-9 기프티콘 교환 테스트.

예상 문제 지점:
1. 비로그인 → 401
2. 포인트 부족(23,000P 미만) → 400
3. 정상 교환 — 포인트 차감 + 더미 코드 발급, 원장에 exchange 행 남음
4. 월 1회 제한 — 같은 달 두 번째 교환 시도 → 400
"""

from app.db import SessionLocal
from app.models import PointLedger
from app.services.points import GIFTICON_PRICE


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _grant_points_for_client(client, amount: int = 25_000) -> None:
    session_id = client.cookies.get("assit_session")
    with SessionLocal() as db:
        db.add(PointLedger(session_id=session_id, kind="charge", amount=amount))
        db.commit()


def test_gifticon_requires_login(client):
    client.post("/session")
    r = client.post("/me/gifticons")
    assert (r.status_code, r.json()["code"]) == (401, "login_required")


def test_gifticon_insufficient_points(client, login_env):
    _login(client)
    _grant_points_for_client(client, amount=10_000)  # 23,000P 미만
    r = client.post("/me/gifticons")
    assert (r.status_code, r.json()["code"]) == (400, "insufficient_points")


def test_gifticon_exchange_success(client, login_env):
    _login(client)
    _grant_points_for_client(client, amount=25_000)
    r = client.post("/me/gifticons")
    assert r.status_code == 200
    body = r.json()
    assert body["points_used"] == GIFTICON_PRICE == 23_000
    assert body["issued_code"].startswith("DUMMY-")
    assert body["balance"] == 2_000

    balance_r = client.get("/me/points").json()
    assert balance_r["balance"] == 2_000


def test_gifticon_monthly_limit(client, login_env):
    _login(client)
    _grant_points_for_client(client, amount=50_000)
    r1 = client.post("/me/gifticons")
    assert r1.status_code == 200
    r2 = client.post("/me/gifticons")
    assert (r2.status_code, r2.json()["code"]) == (400, "gifticon_monthly_limit")
