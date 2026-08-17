"""C-8 포인트 원장 테스트.

예상 문제 지점:
1. 비로그인 충전 → 401 (조회는 세션만 있으면 됨)
2. 정해진 상품 금액(5,000/10,000/30,000) 외 요청 → 400
3. 충전 후 잔액이 상한(30,000P)을 넘으면 결제 승인 API를 부르기 전에 차단
4. 토스 승인 API가 실패 응답을 주면 적립하지 않는다 — 클라이언트 콜백만 믿지 않는다(C-8.2)
5. 승인 성공 시 원장에 charge 행이 남고 잔액에 반영된다
6. 피자 진행률 계산(보유/18,000P)
"""

import httpx
import respx

from app.config import settings


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def test_charge_requires_login(client):
    client.post("/session")
    r = client.post(
        "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 5000}
    )
    assert (r.status_code, r.json()["code"]) == (401, "login_required")


def test_get_points_no_login_needed(client):
    client.post("/session")
    r = client.get("/me/points")
    assert r.status_code == 200
    assert r.json() == {
        "balance": 0,
        "pizza_progress": {"held": 0, "target": 18000, "percent": 0},
    }


def test_charge_rejects_invalid_amount(client, login_env):
    _login(client)
    r = client.post(
        "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 7777}
    )
    assert (r.status_code, r.json()["code"]) == (400, "invalid_amount")


def test_charge_blocks_cap_before_calling_toss(client, login_env, monkeypatch):
    """상한 초과면 결제 승인 API를 아예 부르지 않는다."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk")
    _login(client)

    called = {"count": 0}

    @respx.mock
    def _run():
        route = respx.post("https://api.tosspayments.com/v1/payments/confirm")
        route.mock(return_value=httpx.Response(200, json={"status": "DONE"}))

        # 30,000 상한을 넘도록 두 번 연속 충전 시도
        r1 = client.post(
            "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 30000}
        )
        assert r1.status_code == 200
        called["count"] = route.call_count

        r2 = client.post(
            "/me/points/charge", json={"order_id": "o2", "payment_key": "k2", "amount": 5000}
        )
        assert (r2.status_code, r2.json()["code"]) == (400, "point_cap_exceeded")
        return route.call_count

    final_count = _run()
    assert called["count"] == 1  # 첫 충전만 승인 API를 불렀다
    assert final_count == 1  # 상한 초과 시도는 승인 API를 부르지 않았다


def test_charge_requires_toss_approval(client, login_env, monkeypatch):
    """승인 API가 실패를 반환하면 적립되지 않는다(C-8.2 — 클라이언트 콜백만 믿지 않는다)."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk")
    _login(client)

    @respx.mock
    def _run():
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(400, json={"code": "REJECT_CARD_COMPANY"})
        )
        return client.post(
            "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 5000}
        )

    r = _run()
    assert (r.status_code, r.json()["code"]) == (400, "payment_failed")
    assert client.get("/me/points").json()["balance"] == 0


def test_charge_success_updates_balance_and_progress(client, login_env, monkeypatch):
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk")
    _login(client)

    @respx.mock
    def _run():
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(200, json={"status": "DONE"})
        )
        return client.post(
            "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 10000}
        )

    r = _run()
    assert r.status_code == 200
    assert r.json() == {"balance": 10000, "charged": 10000}

    progress = client.get("/me/points").json()
    assert progress["balance"] == 10000
    assert progress["pizza_progress"] == {"held": 10000, "target": 18000, "percent": 56}
