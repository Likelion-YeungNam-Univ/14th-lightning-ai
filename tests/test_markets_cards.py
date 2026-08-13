"""F-2 구분 도메인 + F-6 카드 조회(목 응답) 테스트."""

DOMESTIC_TABS = ["youtube", "disclosure", "regulation", "bok", "fed"]
OVERSEAS_TABS = ["youtube", "disclosure", "regulation", "fed"]


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _markets(client) -> dict:
    return {m["market"]: m for m in client.get("/markets").json()["markets"]}


def test_markets_without_session(client):
    m = _markets(client)
    # 프론트는 이 목록으로 탭을 그린다 — 구분 값으로 추론 금지 (F-2.1)
    assert m["domestic"]["tabs"] == DOMESTIC_TABS
    assert m["overseas"]["tabs"] == OVERSEAS_TABS
    assert m["domestic"]["stock_count"] == 0
    # 해외 구분은 항상 활성 + 종목 없음 사유 (F-2.3)
    assert m["overseas"]["reason"] == "no_overseas_stock"
    assert m["domestic"]["reason"] is None


def test_markets_with_session(client):
    client.post("/session")
    m = _markets(client)
    assert m["domestic"]["stock_count"] == 4
    assert m["domestic"]["last_stock_code"] is None  # 아직 아무 카드도 안 봄


def test_cards_rejects_invalid_requests(client):
    # 존재하지 않는 종목 — invalid_combination과 구분되는 404
    r = client.get("/cards", params={"tab": "disclosure", "stock_code": "999999"})
    assert (r.status_code, r.json()["code"]) == (404, "unknown_stock")

    # 해외 종목 + 한국은행 탭 — 북마크·뒤로 가기로 들어오는 조합 (F-2.4)
    r = client.get("/cards", params={"tab": "bok", "stock_code": "TSLA"})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_combination")

    # 존재하지 않는 탭명
    r = client.get("/cards", params={"tab": "news", "stock_code": "111110"})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_combination")


def test_cards_tab_slot_rules(client):
    # 유튜브: 요약·라벨 null, 유튜브 필드 채움, disclaimer 고정 근거 (F-6.1.2·6.3)
    yt = client.get("/cards", params={"tab": "youtube", "stock_code": "111110"}).json()
    assert yt["disclaimer"] is True and yt["link_sentence"] is None
    item = yt["items"][0]
    assert item["label"] is None and item["summary_short"] is None
    assert item["thumbnail_url"] and item["channel_name"] and item["view_count"]
    assert item["card_id"] and item["is_saved"] is False and item["origin_url"]

    # 공시: 라벨·요약 채움, 유튜브 필드 null
    dis = client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"}).json()
    assert dis["disclaimer"] is False
    item = dis["items"][0]
    assert item["label"] and item["label_reason"] and item["summary_full"]
    assert item["thumbnail_url"] is None and item["view_count"] is None

    # 금리 탭: link_sentence 1회 + indicator_value 채움, 라벨 null (F-6.2)
    bok = client.get("/cards", params={"tab": "bok", "stock_code": "111110"}).json()
    assert bok["link_sentence"]
    assert bok["items"][0]["indicator_value"] == "2.50%"
    assert bok["items"][0]["label"] is None

    # 해외 종목은 fed·regulation 정상 동작, market이 응답에 실림
    fed = client.get("/cards", params={"tab": "fed", "stock_code": "TSLA"}).json()
    assert fed["market"] == "overseas" and fed["items"][0]["indicator_value"]
    reg = client.get("/cards", params={"tab": "regulation", "stock_code": "TSLA"}).json()
    assert reg["items"][0]["source_name"] == "Federal Register"


def test_last_stock_tracking(client, login_env):
    _login(client)
    client.post("/me/stocks", json={"stock_codes": ["TSLA"]})

    # 구분별 슬롯이 서로를 덮지 않는다 (F-2.2 — 교차 오염 방지)
    client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"})
    client.get("/cards", params={"tab": "fed", "stock_code": "TSLA"})
    m = _markets(client)
    assert m["domestic"]["last_stock_code"] == "111110"
    assert m["overseas"]["last_stock_code"] == "TSLA"
    assert m["overseas"]["reason"] is None  # 이제 해외 종목 있음

    # 미등록 종목 조회는 복귀 지점을 바꾸지 않는다
    client.get("/cards", params={"tab": "disclosure", "stock_code": "555550"})
    assert _markets(client)["domestic"]["last_stock_code"] == "111110"

    # 등록 해제된 종목은 복귀 지점에서 빠진다
    client.delete("/me/stocks/111110")
    assert _markets(client)["domestic"]["last_stock_code"] is None
