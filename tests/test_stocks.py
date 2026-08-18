"""F-3.3~3.7 종목 관리 API 테스트."""

from app.services import stocks as stock_service
from tests.conftest import DEFAULT_CODES, OVERSEAS_DEFAULT_CODES


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def test_search_domestic(client):
    # 이름 부분 일치 — 시총 내림차순
    r = client.get("/stocks/search", params={"q": "가나", "market": "domestic"})
    assert r.status_code == 200
    assert [i["stock_code"] for i in r.json()["items"]] == ["111115", "111110"]
    assert all(i["already_added"] is False for i in r.json()["items"])

    # 코드 전방 일치 — 숫자 시작은 1자부터 허용
    r = client.get("/stocks/search", params={"q": "1", "market": "domestic"})
    assert {i["stock_code"] for i in r.json()["items"]} == {"111115", "111110"}

    # 이름 1자는 최소 길이 미달 → 빈 결과 (확정: 이름 2자)
    r = client.get("/stocks/search", params={"q": "가", "market": "domestic"})
    assert r.json() == {"items": [], "reason": None}

    # 잘못된 구분 → 400 (F-2.4 계열)
    r = client.get("/stocks/search", params={"q": "가나", "market": "foo"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_market"


def test_search_overseas_alias_and_unsupported(client):
    # 화이트리스트 종목은 한글 별칭으로도 검색된다
    r = client.get("/stocks/search", params={"q": "테슬라", "market": "overseas"})
    assert [i["stock_code"] for i in r.json()["items"]] == ["TSLA"]

    r = client.get("/stocks/search", params={"q": "aapl", "market": "overseas"})
    assert [i["stock_code"] for i in r.json()["items"]] == ["AAPL"]

    # 화이트리스트 밖 유명 해외 종목 → 오타가 아니라 미지원임을 알린다 (F-3.3.1)
    # (엔비디아는 이제 해외 기본 종목이라 정상 검색되므로, 여전히 미지원인 종목으로 검증)
    r = client.get("/stocks/search", params={"q": "팔란티어", "market": "overseas"})
    assert r.json() == {"items": [], "reason": "unsupported_overseas"}

    # 그냥 없는 검색어 → 일반 빈 결과
    r = client.get("/stocks/search", params={"q": "없는회사", "market": "overseas"})
    assert r.json() == {"items": [], "reason": None}


def test_search_marks_already_added(client):
    client.post("/session")  # 기본 종목 4개 등록됨
    r = client.get("/stocks/search", params={"q": "가나", "market": "domestic"})
    by_code = {i["stock_code"]: i["already_added"] for i in r.json()["items"]}
    assert by_code == {"111115": False, "111110": True}


def test_popular(client):
    r = client.get("/stocks/popular", params={"market": "domestic"})
    codes = [i["stock_code"] for i in r.json()]
    assert codes == ["111110", "222220", "333330", "444440", "555550"]  # 보통주만, 시총순
    assert "111115" not in codes  # 우선주 제외

    r = client.get("/stocks/popular", params={"market": "overseas"})
    assert [i["stock_code"] for i in r.json()] == ["AAPL", "GOOGL", "MSFT", "NVDA", "TSLA"]


def test_my_stocks_requires_session(client):
    r = client.get("/me/stocks", params={"market": "domestic"})
    assert r.status_code == 401
    assert r.json()["code"] == "no_session"


def test_add_delete_flow(client, login_env):
    client.post("/session")

    # 비로그인 추가 → 401 login_required (F-1.3 — no_session과 구분)
    r = client.post("/me/stocks", json={"stock_codes": ["555550"]})
    assert r.status_code == 401
    assert r.json()["code"] == "login_required"

    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})

    # 벌크 등록 — 신규 1 + 기존 1(멱등 무시), 응답에 market 포함 (F-3.5.1)
    r = client.post("/me/stocks", json={"stock_codes": ["555550", "111110"]})
    assert r.status_code == 200
    assert r.json()["added"] == [{"stock_code": "555550", "name": "자차식품", "market": "domestic"}]
    assert r.json()["already_registered"] == ["111110"]

    # 목록: 기본 4 + 추가 1, 추가분이 마지막 순서
    r = client.get("/me/stocks", params={"market": "domestic"})
    items = r.json()["items"]
    assert [i["stock_code"] for i in items] == DEFAULT_CODES + ["555550"]
    assert items[-1]["is_default"] is False

    # 존재하지 않는 코드 → 400
    r = client.post("/me/stocks", json={"stock_codes": ["999999"]})
    assert r.status_code == 400
    assert r.json()["code"] == "unknown_stock"

    # 해외는 비로그인 때부터 이미 고정 기본 4종이 채워져 있다(QA 리포트 반영)
    assert [
        i["stock_code"]
        for i in client.get("/me/stocks", params={"market": "overseas"}).json()["items"]
    ] == OVERSEAS_DEFAULT_CODES

    # 해외 종목 추가 — 구분이 응답으로 내려간다
    r = client.post("/me/stocks", json={"stock_codes": ["TSLA"]})
    assert r.json()["added"][0]["market"] == "overseas"
    assert [
        i["stock_code"]
        for i in client.get("/me/stocks", params={"market": "overseas"}).json()["items"]
    ] == OVERSEAS_DEFAULT_CODES + ["TSLA"]

    # 삭제 — 해당 구분의 남은 개수 반환 (F-3.6)
    assert client.delete("/me/stocks/555550").json() == {"remaining": 4}
    assert client.delete("/me/stocks/555550").status_code == 404
    assert client.delete("/me/stocks/TSLA").json() == {"remaining": 4}  # 해외 기본 4종은 남음


def test_add_limit_per_market(client, login_env, monkeypatch):
    monkeypatch.setattr(stock_service, "STOCK_LIMIT_PER_MARKET", 5)
    _login(client)  # 기본 4개 등록 상태

    ok = client.post("/me/stocks", json={"stock_codes": ["555550"]})  # 5개 — 상한 도달
    assert ok.status_code == 200

    over = client.post("/me/stocks", json={"stock_codes": ["111115"]})  # 6개 — 초과
    assert over.status_code == 400
    assert over.json()["code"] == "stock_limit_exceeded"
    details = over.json()["details"]
    assert details["market"] == "domestic"
    assert details["remaining"] == 0 and details["limit"] == 5  # 남은 자리 수 (F-3.5, v3 갱신)


def test_default_limit_is_ten(client, login_env):
    """v3 갱신 — 구분별 상한 10. 기본 4개 + 6개 추가는 성공, 7번째부터 400 + remaining."""
    assert stock_service.STOCK_LIMIT_PER_MARKET == 10
    _login(client)
    # conftest 국내 시드는 6종뿐 — 상한 로직 자체는 위 테스트가 검증, 여기선 상수만 고정


def test_reorder(client, login_env):
    _login(client)
    reordered = list(reversed(DEFAULT_CODES))

    r = client.put("/me/stocks/order", json={"market": "domestic", "stock_codes": reordered})
    assert r.status_code == 200
    assert r.json()["stocks"] == reordered
    items = client.get("/me/stocks", params={"market": "domestic"}).json()["items"]
    assert [i["stock_code"] for i in items] == reordered

    # 일부 누락 → 400 (전체 목록을 통째로 받아야 한다, F-3.7)
    r = client.put("/me/stocks/order", json={"market": "domestic", "stock_codes": reordered[:2]})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_order_payload"

    # 다른 구분 코드가 섞임 → 400
    r = client.put(
        "/me/stocks/order", json={"market": "domestic", "stock_codes": reordered[:3] + ["TSLA"]}
    )
    assert r.status_code == 400
