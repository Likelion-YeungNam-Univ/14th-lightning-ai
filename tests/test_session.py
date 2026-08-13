"""F-1 세션·모의 로그인·데모 초기화 테스트. 시드·픽스처는 conftest 참조."""

from app.config import settings
from tests.conftest import DEFAULT_CODES


def test_session_issues_defaults_and_is_idempotent(client):
    r = client.post("/session")
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["authenticated"] is False
    # 시총 상위 4, 우선주(111115) 제외 (F-3.8)
    assert body["stocks"] == DEFAULT_CODES
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
    assert r.json()["stocks"] == DEFAULT_CODES

    # 로그인 상태도 초기화되어 첫 진입 화면이 재현된다 (F-1.5)
    assert client.post("/session").json()["authenticated"] is False
