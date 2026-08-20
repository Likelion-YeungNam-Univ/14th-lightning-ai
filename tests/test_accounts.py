"""#74 실계정 인증/인가 테스트 — 세션+계정(주인 세션) 방식.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 쿠키 유실 후 재로그인해도 데이터(종목·저장카드)가 안 돌아옴 → 새 클라이언트에서 로그인 → 복구
2. 두 기기 동시 로그인이 서로 다른 데이터를 봄 → 같은 주인 세션으로 합류
3. 익명 활동이 가입 시 유실 → 가입 전 등록 종목이 계정 데이터로 승계
4. 중복 아이디 / 틀린 비밀번호 / 무차별 대입 → 409 / 401 / 429
5. 실계정 로그인이 인증 게이트(F-1.3)를 못 통과 → 종목 추가 200
6. mock-login 경로 회귀 → 기존과 동일 동작(전체 스위트가 증명, 여기선 공존만 확인)
7. 로그아웃 후 이 브라우저는 익명으로 — 계정 데이터는 보존
8. 같은 브라우저에서 가입→로그아웃→재가입 → 두 계정이 데이터(포인트)를 공유하면 안 됨
9. 비밀번호 해시 왕복 + 평문·다른 비번 거부
"""

from app.db import SessionLocal
from app.models import AppUser, UserSession
from app.services import ratelimit
from app.services.accounts import hash_password, verify_password


def _my_codes(c) -> set[str]:
    r = c.get("/me/stocks", params={"market": "domestic"})
    return {s["stock_code"] for s in r.json()["items"]}


def _creds(uid: str) -> dict:
    """테스트 간 DB가 공유되므로 테스트마다 고유 아이디를 쓴다."""
    return {"login_id": uid, "password": "password123", "nickname": "승래"}


def _signup(client, uid: str, **over):
    ratelimit.reset()
    client.post("/session")
    return client.post("/auth/signup", json={**_creds(uid), **over})


def test_password_hash_roundtrip():
    stored = hash_password("password123")
    assert stored.startswith("scrypt$") and verify_password("password123", stored)
    assert not verify_password("password124", stored)
    assert not verify_password("password123", "엉터리저장값")


def test_signup_inherits_anonymous_activity(client, login_env):
    ratelimit.reset()
    client.post("/session")
    stocks_before = client.get("/me/stocks", params={"market": "domestic"}).json()["items"]
    r = _signup(client, "acc_inherit")
    assert r.status_code == 200
    body = r.json()
    assert body["login_id"] == "acc_inherit" and body["nickname"] == "승래"
    # 가입 즉시 인증 게이트 통과 + 익명 시절 데이터 그대로 (예상 문제 3·5)
    stocks_after = client.get("/me/stocks", params={"market": "domestic"}).json()["items"]
    assert [s["stock_code"] for s in stocks_after] == [s["stock_code"] for s in stocks_before]
    add = client.post("/me/stocks", json={"stock_codes": ["555550"]})
    assert add.status_code == 200


def test_login_recovers_data_after_cookie_loss(client, login_env):
    _signup(client, "acc_recover")
    client.post("/me/stocks", json={"stock_codes": ["555550"]})
    my = _my_codes(client)

    fresh = client.__class__(client.app)  # 쿠키 삭제/다른 기기 — 완전히 새 클라이언트
    fresh.post("/session")
    anon = _my_codes(fresh)
    assert "555550" not in anon  # 익명 상태 — 기본 종목뿐

    r = fresh.post("/auth/login", json={"login_id": "acc_recover", "password": "password123"})
    assert r.status_code == 200 and r.json()["nickname"] == "승래"
    recovered = _my_codes(fresh)
    assert recovered == my  # 예상 문제 1 — 핵심 시나리오

    # 두 클라이언트(기기)가 같은 데이터를 본다 (예상 문제 2)
    fresh.post("/me/stocks", json={"stock_codes": ["111115"]})
    on_first = _my_codes(client)
    assert "111115" in on_first


def test_signup_validation_and_errors(client, login_env):
    r = _signup(client, "acc_valid1")
    assert r.status_code == 200
    # 로그인된 세션에서 재가입 → 409
    r2 = client.post("/auth/signup", json={**_creds("another01")})
    assert (r2.status_code, r2.json()["code"]) == (409, "already_logged_in")

    fresh = client.__class__(client.app)
    fresh.post("/session")
    ratelimit.reset()
    dup = fresh.post("/auth/signup", json=_creds("acc_valid1"))  # 중복 아이디
    assert (dup.status_code, dup.json()["code"]) == (409, "duplicate_login_id")
    bad = fresh.post("/auth/signup", json={**_creds("한글아이디")})
    assert bad.status_code == 422  # 패턴 위반 — 공통 검증 핸들러(validation_error)
    wrong = fresh.post("/auth/login", json={"login_id": "acc_valid1", "password": "틀린비번99"})
    assert (wrong.status_code, wrong.json()["code"]) == (401, "invalid_credentials")
    ghost = fresh.post("/auth/login", json={"login_id": "noone9999", "password": "password123"})
    assert (ghost.status_code, ghost.json()["code"]) == (401, "invalid_credentials")


def test_login_rate_limited(client, login_env, monkeypatch):
    _signup(client, "acc_rate01")
    fresh = client.__class__(client.app)
    fresh.post("/session")
    ratelimit.reset()
    import app.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "LOGIN_ATTEMPTS_PER_MINUTE", 2)
    for _ in range(2):
        fresh.post("/auth/login", json={"login_id": "acc_rate01", "password": "틀린비번99"})
    r = fresh.post("/auth/login", json={"login_id": "acc_rate01", "password": "password123"})
    assert (r.status_code, r.json()["code"]) == (429, "rate_limited")  # 예상 문제 4


def test_logout_returns_to_anonymous_but_keeps_account(client, login_env):
    _signup(client, "acc_logout")
    client.post("/me/stocks", json={"stock_codes": ["555550"]})
    r = client.post("/auth/logout")
    assert r.json()["logged_out"] is True

    # 새 세션 = 익명. 계정 데이터는 사라진 게 아니라 로그인하면 다시 보인다 (예상 문제 7)
    client.post("/session")
    anon = _my_codes(client)
    assert "555550" not in anon
    ratelimit.reset()
    client.post("/auth/login", json={"login_id": "acc_logout", "password": "password123"})
    back = _my_codes(client)
    assert "555550" in back


def test_resignup_on_same_session_gets_fresh_primary(client, login_env):
    _signup(client, "acc_first1")
    client.post("/me/stocks", json={"stock_codes": ["555550"]})  # 계정 1의 데이터
    client.post("/auth/logout")

    # 같은 브라우저(쿠키 재발급 후)에서 두 번째 계정 가입 — 쿠키 세션이 계정1의 주인이었던 상황
    client.post("/session")
    ratelimit.reset()
    r = client.post(
        "/auth/signup",
        json={"login_id": "second0001", "password": "password123", "nickname": "둘째"},
    )
    assert r.status_code == 200
    second = _my_codes(client)
    assert "555550" not in second  # 예상 문제 8 — 계정 간 데이터 공유 금지
    with SessionLocal() as db:
        users = {u.login_id: u.primary_session_id for u in db.query(AppUser).all()}
        assert users["acc_first1"] != users["second0001"]  # 주인 세션 분리
        assert db.get(UserSession, users["second0001"]) is not None


def test_mock_login_coexists(client, login_env):
    ratelimit.reset()
    client.post("/session")
    r = client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert client.post("/me/stocks", json={"stock_codes": ["555550"]}).status_code == 200


def test_post_session_reflects_account(client, login_env):
    """POST /session(앱 첫 진입)도 계정 데이터를 반영해야 초기 화면이 다른 API와 일치한다."""
    _signup(client, "acc_entry1")
    client.post("/me/stocks", json={"stock_codes": ["555550"]})

    r = client.post("/session")  # 로그인된 쿠키로 앱 재진입
    body = r.json()
    assert body["created"] is False
    assert body["authenticated"] is True and body["nickname"] == "승래"
    assert "555550" in body["stocks"]  # 계정(주인 세션) 기준

    fresh = client.__class__(client.app)  # 익명은 기존과 동일 — nickname 없음
    anon = fresh.post("/session").json()
    assert anon["authenticated"] is False and anon["nickname"] is None
