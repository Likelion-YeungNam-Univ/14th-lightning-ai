"""F-1 세션·모의 로그인·데모 초기화 테스트. 실 DB·키 없이 동작(sqlite + 설정 몽키패치)."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import MARKET_DOMESTIC, StockMaster

# 시총 1위가 우선주(끝자리 5) — 기본 종목에서 빠져야 한다 (확정사항 4절)
SEED = [
    ("111115", "가나전자우", 900),
    ("111110", "가나전자", 800),
    ("222220", "다라화학", 700),
    ("333330", "마바은행", 600),
    ("444440", "사아건설", 500),
    ("555550", "자차식품", 400),
]


def _seed_stocks() -> None:
    with SessionLocal() as db:
        for code, name, cap in SEED:
            if db.get(StockMaster, code) is None:
                db.add(
                    StockMaster(
                        stock_code=code,
                        market=MARKET_DOMESTIC,
                        name=name,
                        aliases=[],
                        exchange="KOSPI",
                        industry_code="etc",
                        market_cap=cap * 10**12,
                    )
                )
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:  # lifespan → init_db(create_all)
        _seed_stocks()
        yield c


@pytest.fixture()
def login_env(monkeypatch):
    monkeypatch.setattr(settings, "mock_login_id", "demo")
    monkeypatch.setattr(settings, "mock_login_pw", "pw1234")
    monkeypatch.setattr(settings, "admin_token", "admin-secret")


def test_session_issues_defaults_and_is_idempotent(client):
    r = client.post("/session")
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["authenticated"] is False
    # 시총 상위 4, 우선주(111115) 제외 (F-3.8)
    assert body["stocks"] == ["111110", "222220", "333330", "444440"]
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
    assert r.json()["stocks"] == ["111110", "222220", "333330", "444440"]

    # 로그인 상태도 초기화되어 첫 진입 화면이 재현된다 (F-1.5)
    assert client.post("/session").json()["authenticated"] is False
