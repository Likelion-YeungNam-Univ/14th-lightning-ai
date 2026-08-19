"""F-1 세션·모의 로그인·데모 초기화 테스트. 시드·픽스처는 conftest 참조."""

from app.config import settings
from tests.conftest import DEFAULT_CODES, OVERSEAS_DEFAULT_CODES

ALL_DEFAULT_CODES = DEFAULT_CODES + OVERSEAS_DEFAULT_CODES  # 국내 4 + 해외 고정 4


def test_session_issues_defaults_and_is_idempotent(client):
    r = client.post("/session")
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["authenticated"] is False
    # 국내는 시총 상위 4(우선주 111115 제외, F-3.8) + 해외 고정 4(QA 리포트 반영)
    assert body["stocks"] == ALL_DEFAULT_CODES
    assert "assit_session" in r.cookies

    # 같은 쿠키로 재호출 — 재발급·재프로비저닝 없이 멱등 (F-1.1)
    r2 = client.post("/session")
    assert r2.json()["created"] is False
    assert r2.json()["stocks"] == body["stocks"]


def test_login_requires_session(client, login_env):
    r = client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    assert r.status_code == 401
    assert r.json()["code"] == "no_session"


def test_mock_login_success_and_failure(client, login_env):
    client.post("/session")

    wrong = client.post("/auth/mock-login", json={"id": "demo", "password": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"

    ok = client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    assert ok.status_code == 200
    assert ok.json() == {"authenticated": True}

    # 세션에 남는다 — 재발급 없이 조회 시 authenticated 유지
    assert client.post("/session").json()["authenticated"] is True


def test_logout_clears_cookie_and_next_session_is_fresh(client, login_env):
    """확정사항 16절 — F-1.2가 배제했던 로그아웃을 시연 편의로 추가."""
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    assert client.post("/session").json()["authenticated"] is True

    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"logged_out": True}
    assert "assit_session" not in client.cookies

    # 쿠키가 사라졌으니 다음 /session은 완전히 새 세션(비로그인)을 발급한다
    fresh = client.post("/session")
    assert fresh.json()["created"] is True
    assert fresh.json()["authenticated"] is False


def test_logout_requires_session(client, login_env):
    r = client.post("/auth/logout")
    assert r.status_code == 401
    assert r.json()["code"] == "no_session"


def test_login_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "mock_login_id", "")
    client.post("/session")
    r = client.post("/auth/mock-login", json={"id": "x", "password": "y"})
    assert r.status_code == 500
    assert r.json()["code"] == "login_not_configured"


def test_reset_demo(client, login_env):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})

    # 토큰 없이 → 401 (확정사항 4절)
    denied = client.post("/admin/reset-demo")
    assert denied.status_code == 401
    assert denied.json()["code"] == "admin_token_invalid"

    r = client.post("/admin/reset-demo", headers={"X-Admin-Token": "admin-secret"})
    assert r.status_code == 200
    assert r.json()["stocks"] == ALL_DEFAULT_CODES

    # 로그인 상태도 초기화되어 첫 진입 화면이 재현된다 (F-1.5)
    assert client.post("/session").json()["authenticated"] is False


def test_session_provisions_defaults_and_me_stocks_order(client):
    """프론트 요구사항 재확인 — 세션 발급 = 기본 4종목(order 0~3), 같은 쿠키로 /me/stocks."""
    r = client.post("/session")
    body = r.json()
    assert body["created"] is True and body["authenticated"] is False
    assert body["stocks"] == ALL_DEFAULT_CODES  # 국내 4 + 해외 고정 4
    assert "assit_session" in r.cookies and "httponly" in r.headers["set-cookie"].lower()

    listed = client.get("/me/stocks", params={"market": "domestic"}).json()["items"]
    assert [i["stock_code"] for i in listed] == DEFAULT_CODES
    assert [i["display_order"] for i in listed] == [0, 1, 2, 3]
    assert all(i["is_default"] for i in listed)

    # 유효 세션으로 재호출 — 새로 만들지 않고 기존 목록 반환 (멱등)
    again = client.post("/session").json()
    assert again["created"] is False and again["stocks"] == ALL_DEFAULT_CODES


def test_me_stocks_without_cookie_is_no_session(client):
    """쿠키가 안 붙으면 401 no_session — 프론트 '종목이 안 뜸'의 1순위 원인 재현."""
    fresh = client.__class__(client.app)
    r = fresh.get("/me/stocks", params={"market": "domestic"})
    assert (r.status_code, r.json()["code"]) == (401, "no_session")


def test_cors_allows_dev_origin_with_credentials(client):
    """프론트 dev 서버(3000)에서 직접 호출 시 쿠키 동반 CORS 허용."""
    r = client.options(
        "/session",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert r.headers.get("access-control-allow-credentials") == "true"
