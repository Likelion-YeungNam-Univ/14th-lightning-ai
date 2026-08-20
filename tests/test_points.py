"""C-8 포인트 원장 테스트.

예상 문제 지점:
1. 비로그인 충전 → 401 (조회는 세션만 있으면 됨)
2. 정해진 상품 금액(5,000/10,000/30,000) 외 요청 → 400
3. 충전 후 잔액이 상한(30,000P)을 넘으면 결제 승인 API를 부르기 전에 차단
4. 토스 승인 API가 실패 응답을 주면 적립하지 않는다 — 클라이언트 콜백만 믿지 않는다(C-8.2)
5. 승인 성공 시 원장에 charge 행이 남고 잔액에 반영된다
6. 피자 진행률 계산(보유/23,000P)
7. 같은 order_id로 두 번 충전 요청 → 두 번째는 already_charged, 이중 적립 안 됨
8. 요청 body의 amount가 아니라 승인 응답의 totalAmount로만 적립한다
9. 토스 시크릿 키가 test_sk_ 접두사가 아니면 충전 자체를 거부한다
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
        "pizza_progress": {"held": 0, "target": 23000, "percent": 0},
    }


def test_charge_rejects_invalid_amount(client, login_env, monkeypatch):
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)
    r = client.post(
        "/me/points/charge", json={"order_id": "o1", "payment_key": "k1", "amount": 7777}
    )
    assert (r.status_code, r.json()["code"]) == (400, "invalid_amount")


def test_charge_blocks_cap_before_calling_toss(client, login_env, monkeypatch):
    """상한 초과면 결제 승인 API를 아예 부르지 않는다."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)

    called = {"count": 0}

    @respx.mock
    def _run():
        route = respx.post("https://api.tosspayments.com/v1/payments/confirm")
        route.mock(
            return_value=httpx.Response(
                200, json={"status": "DONE", "orderId": "cap-1", "totalAmount": 30000}
            )
        )

        # 30,000 상한을 넘도록 두 번 연속 충전 시도
        r1 = client.post(
            "/me/points/charge", json={"order_id": "cap-1", "payment_key": "k1", "amount": 30000}
        )
        assert r1.status_code == 200
        called["count"] = route.call_count

        r2 = client.post(
            "/me/points/charge", json={"order_id": "cap-2", "payment_key": "k2", "amount": 5000}
        )
        assert (r2.status_code, r2.json()["code"]) == (400, "point_cap_exceeded")
        return route.call_count

    final_count = _run()
    assert called["count"] == 1  # 첫 충전만 승인 API를 불렀다
    assert final_count == 1  # 상한 초과 시도는 승인 API를 부르지 않았다


def test_charge_requires_toss_approval(client, login_env, monkeypatch):
    """승인 API가 실패를 반환하면 적립되지 않는다(C-8.2 — 클라이언트 콜백만 믿지 않는다)."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)

    @respx.mock
    def _run():
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(400, json={"code": "REJECT_CARD_COMPANY"})
        )
        return client.post(
            "/me/points/charge",
            json={"order_id": "approval-1", "payment_key": "k1", "amount": 5000},
        )

    r = _run()
    assert (r.status_code, r.json()["code"]) == (400, "payment_failed")
    assert client.get("/me/points").json()["balance"] == 0


def test_charge_success_updates_balance_and_progress(client, login_env, monkeypatch):
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)

    @respx.mock
    def _run():
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(
                200, json={"status": "DONE", "orderId": "success-1", "totalAmount": 10000}
            )
        )
        return client.post(
            "/me/points/charge",
            json={"order_id": "success-1", "payment_key": "k1", "amount": 10000},
        )

    r = _run()
    assert r.status_code == 200
    assert r.json() == {"balance": 10000, "charged": 10000}

    progress = client.get("/me/points").json()
    assert progress["balance"] == 10000
    assert progress["pizza_progress"] == {"held": 10000, "target": 23000, "percent": 43}


def test_charge_duplicate_order_id_blocked(client, login_env, monkeypatch):
    """같은 order_id로 재요청하면 이중 적립되지 않는다(승래 리뷰 B-3)."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)

    @respx.mock
    def _charge_once():
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(
                200, json={"status": "DONE", "orderId": "dup-1", "totalAmount": 5000}
            )
        )
        return client.post(
            "/me/points/charge", json={"order_id": "dup-1", "payment_key": "k1", "amount": 5000}
        )

    r1 = _charge_once()
    assert r1.status_code == 200
    r2 = client.post(
        "/me/points/charge", json={"order_id": "dup-1", "payment_key": "k2", "amount": 5000}
    )
    assert (r2.status_code, r2.json()["code"]) == (409, "already_charged")
    assert client.get("/me/points").json()["balance"] == 5000  # 한 번만 적립


def test_charge_trusts_response_amount_not_request(client, login_env, monkeypatch):
    """승인 응답의 totalAmount로만 적립한다 — 요청 body의 amount를 신뢰하지 않는다(B-3)."""
    monkeypatch.setattr(settings, "toss_secret_key", "test_sk_dummy")
    _login(client)

    @respx.mock
    def _run():
        # 토스 응답은 5,000원인데 클라이언트는 30,000원을 요청 — 응답 금액만 믿어야 한다
        respx.post("https://api.tosspayments.com/v1/payments/confirm").mock(
            return_value=httpx.Response(
                200, json={"status": "DONE", "orderId": "o-mismatch", "totalAmount": 5000}
            )
        )
        return client.post(
            "/me/points/charge",
            json={"order_id": "o-mismatch", "payment_key": "k1", "amount": 30000},
        )

    r = _run()
    assert r.status_code == 200
    assert r.json() == {"balance": 5000, "charged": 5000}  # 30000이 아니라 5000


def test_charge_rejects_non_test_key(client, login_env, monkeypatch):
    """토스 시크릿 키가 test_sk_ 접두사가 아니면 실결제 사고를 막기 위해 아예 거부한다."""
    monkeypatch.setattr(settings, "toss_secret_key", "live_sk_should_not_be_used")
    _login(client)
    r = client.post(
        "/me/points/charge", json={"order_id": "guard-1", "payment_key": "k1", "amount": 5000}
    )
    assert (r.status_code, r.json()["code"]) == (500, "payment_not_configured")


# ── 이슈 #83 — 테스트 경로(order_id·payment_key 생략) ──────────────────────


def test_charge_test_path_skips_toss(client, login_env, monkeypatch):
    """order_id·payment_key를 둘 다 생략하면 토스 승인 API를 부르지 않고 바로 적립한다."""
    monkeypatch.setattr(settings, "toss_secret_key", "live_sk_should_not_matter_here")
    _login(client)

    @respx.mock
    def _run():
        route = respx.post("https://api.tosspayments.com/v1/payments/confirm")
        route.mock(return_value=httpx.Response(200, json={"status": "DONE"}))
        r = client.post("/me/points/charge", json={"amount": 10000})
        return r, route.call_count

    r, call_count = _run()
    assert r.status_code == 200
    assert r.json() == {"balance": 10000, "charged": 10000}
    assert call_count == 0  # 토스를 아예 안 불렀다
    assert client.get("/me/points").json()["balance"] == 10000


def test_charge_test_path_still_validates_amount_and_cap(client, login_env):
    _login(client)
    bad = client.post("/me/points/charge", json={"amount": 1234})
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid_amount")

    ok = client.post("/me/points/charge", json={"amount": 30000})
    assert ok.status_code == 200
    over = client.post("/me/points/charge", json={"amount": 5000})
    assert (over.status_code, over.json()["code"]) == (400, "point_cap_exceeded")


def test_charge_partial_test_fields_rejected(client, login_env):
    """order_id만 있고 payment_key가 없는 등 절반만 보내면 400."""
    _login(client)
    r = client.post("/me/points/charge", json={"order_id": "o1", "amount": 5000})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_request")

    r2 = client.post("/me/points/charge", json={"payment_key": "k1", "amount": 5000})
    assert (r2.status_code, r2.json()["code"]) == (400, "invalid_request")
