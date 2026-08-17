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


def _seed_card_data(db):
    """실데이터 카드 조회용 시드 — 다른 테스트의 누적 데이터와 겹치지 않는 source_key 사용."""
    from app.collectors.base import ensure_industry_link, ensure_stock_link, upsert_source_item
    from app.deps import utcnow
    from app.models import GeneratedContent, RateLinkSentence

    yt = upsert_source_item(
        db,
        tab="youtube",
        market="domestic",
        source_key="card-yt",
        title="카드용 영상",
        published_at=utcnow(),
        origin_url="https://youtube.com/watch?v=card-yt",
        thumbnail_url="https://i.ytimg.com/card-yt.jpg",
        channel_name="카드채널",
        view_count=999_999,
    )
    ensure_stock_link(db, yt.id, "111110")

    shown = upsert_source_item(  # display=true 유형 + 정형 슬롯
        db,
        tab="disclosure",
        market="domestic",
        source_key="card-d1",
        title="주요사항보고서(자기주식취득결정)",
        doc_type="자기주식취득결정",
        published_at=utcnow(),
        origin_url="https://dart.fss.or.kr/card-d1",
        detail_json={"slots": [{"label": "취득 예정 금액", "value": "1,000,000,000원"}]},
    )
    ensure_stock_link(db, shown.id, "111110")
    hidden = upsert_source_item(  # display=false 유형 — 노출 제외 (이슈 #18)
        db,
        tab="disclosure",
        market="domestic",
        source_key="card-d2",
        title="임원ㆍ주요주주특정증권등소유상황보고서",
        doc_type="임원ㆍ주요주주특정증권등소유상황보고서",
        published_at=utcnow(),
    )
    ensure_stock_link(db, hidden.id, "111110")
    if db.query(GeneratedContent).filter_by(source_item_id=shown.id).one_or_none() is None:
        db.add(
            GeneratedContent(
                source_item_id=shown.id,
                scope="stock",
                scope_key="111110",
                summary_short="자기주식을 사기로 했어요.",
                summary_full="회사가 자기주식 취득을 결정했어요.",
                label="positive",
                label_reason="주주 환원 신호로 읽혀요.",
            )
        )

    bok = upsert_source_item(
        db,
        tab="bok",
        market="domestic",
        source_key="card-bok",
        title="한국은행 기준금리",
        indicator_value="2.75%",
        doc_type="인상",
        published_at=utcnow(),
    )
    if db.query(GeneratedContent).filter_by(source_item_id=bok.id).one_or_none() is None:
        db.add(
            GeneratedContent(
                source_item_id=bok.id,
                scope="global",
                scope_key="global",
                summary_short="기준금리가 올랐어요.",
                summary_full="기준금리가 2.75%로 인상됐어요.",
            )
        )
    # 111110의 업종은 etc(conftest) — bok 연결 문장 캐시 (다른 테스트가 만든 행은 덮어쓴다)
    sentence_row = db.get(RateLinkSentence, ("domestic", "bok", "etc", "2.75%|인상"))
    if sentence_row is None:
        sentence_row = RateLinkSentence(
            market="domestic",
            tab="bok",
            industry_key="etc",
            indicator_version="2.75%|인상",
            sentence="",
        )
        db.add(sentence_row)
    sentence_row.sentence = "금리가 오르면 이자 부담이 커지는 경향이 있어요."

    fed = upsert_source_item(
        db,
        tab="fed",
        market="domestic",
        source_key="card-fed",
        title="미국 기준금리",
        indicator_value="3.75%",
        doc_type="인하",
        published_at=utcnow(),
    )
    reg = upsert_source_item(  # TSLA(SIC 3711) 규제
        db,
        tab="regulation",
        market="overseas",
        source_key="card-reg",
        title="Vehicle Safety Rule",
        published_at=utcnow(),
        origin_url="https://federalregister.gov/card-reg",
    )
    ensure_industry_link(db, reg.id, "overseas", "3711")
    db.commit()
    return {"hidden_id": hidden.id, "fed_id": fed.id}


def test_cards_tab_slot_rules(client):
    from app.db import SessionLocal
    from app.services.industry import seed_form_types

    with SessionLocal() as db:
        seed_form_types(db)
        ids = _seed_card_data(db)

    # 유튜브: 요약·라벨 null, 유튜브 필드 채움, disclaimer 고정 (F-6.1.2·6.3)
    yt = client.get("/cards", params={"tab": "youtube", "stock_code": "111110"}).json()
    assert yt["disclaimer"] is True and yt["link_sentence"] is None
    item = yt["items"][0]  # 조회수순 — 시드가 최상위
    assert item["title"] == "카드용 영상"
    assert item["label"] is None and item["summary_short"] is None
    assert item["thumbnail_url"] and item["channel_name"] and item["view_count"] == 999_999
    assert item["card_id"] and item["is_saved"] is False and item["origin_url"]
    assert item["link_sentence"] is None  # 금리 탭 아님 — 카드별 문장도 null

    # 공시: display 유형만 노출 + 라벨·요약·정형 슬롯 채움 (이슈 #18)
    dis = client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"}).json()
    assert dis["disclaimer"] is False
    ids_in_list = [c["card_id"] for c in dis["items"]]
    assert ids["hidden_id"] not in ids_in_list  # 임원 보고서 — 노출 제외
    item = next(c for c in dis["items"] if c["title"] == "주요사항보고서(자기주식취득결정)")
    assert item["label"] == "positive" and item["label_reason"] and item["summary_full"]
    assert item["details"] == [{"label": "취득 예정 금액", "value": "1,000,000,000원"}]
    assert item["thumbnail_url"] is None and item["view_count"] is None

    # 금리 탭: link_sentence 1회 + indicator_value, 라벨 null (F-6.2)
    bok = client.get("/cards", params={"tab": "bok", "stock_code": "111110"}).json()
    assert bok["link_sentence"] == "금리가 오르면 이자 부담이 커지는 경향이 있어요."
    assert bok["items"][0]["indicator_value"] == "2.75%"
    assert bok["items"][0]["label"] is None and bok["items"][0]["summary_short"]
    # 요구사항 변경 — 카드별 link_sentence도 같은 문장(카드가 1건뿐이라 최상단과 동일)
    assert bok["items"][0]["link_sentence"] == "금리가 오르면 이자 부담이 커지는 경향이 있어요."

    # 해외 종목: fed·regulation 정상, market 응답 확인
    fed = client.get("/cards", params={"tab": "fed", "stock_code": "TSLA"}).json()
    assert fed["market"] == "overseas" and fed["items"][0]["indicator_value"]
    reg = client.get("/cards", params={"tab": "regulation", "stock_code": "TSLA"}).json()
    assert any(c["title"] == "Vehicle Safety Rule" for c in reg["items"])
    assert reg["items"][0]["source_name"] == "Federal Register"


def test_cards_link_sentence_per_card(client):
    """이슈 #44 — 카드마다 자신의 지표 스냅샷 기준 문장을 받는다(F-6.2 확장, 최상단 값과 무관)."""
    from app.db import SessionLocal
    from app.models import RateLinkSentence
    from app.services.industry import seed_form_types

    with SessionLocal() as db:
        seed_form_types(db)
        _seed_card_data(db)

        # 111110 업종(etc)에 대해 '동결' 버전의 두 번째 bok 카드 + 캐시 문장 추가 시드
        from app.collectors.base import upsert_source_item
        from app.deps import utcnow

        frozen = upsert_source_item(
            db,
            tab="bok",
            market="domestic",
            source_key="card-bok-frozen",
            title="한국은행 기준금리(동결)",
            indicator_value="2.75%",
            doc_type=None,
            published_at=utcnow(),
        )
        db.add(
            RateLinkSentence(
                market="domestic",
                tab="bok",
                industry_key="etc",
                indicator_version="2.75%|동결",
                sentence="금리가 동결되면 큰 변화가 없을 가능성이 커요.",
            )
        )
        db.commit()
        frozen_id = frozen.id

    bok = client.get("/cards", params={"tab": "bok", "stock_code": "111110"}).json()
    by_id = {c["card_id"]: c for c in bok["items"]}
    assert by_id[frozen_id]["link_sentence"] == "금리가 동결되면 큰 변화가 없을 가능성이 커요."
    other = next(c for cid, c in by_id.items() if cid != frozen_id)
    assert other["link_sentence"] == "금리가 오르면 이자 부담이 커지는 경향이 있어요."
    assert by_id[frozen_id]["link_sentence"] != other["link_sentence"]


def test_cards_empty_reasons(client):
    from app.db import SessionLocal
    from app.models import CollectStatus

    # 자료 없음 — 수집 실패 기록이 없으면 no_data (F-6.4). 앞 테스트가 남긴 실패 기록은 정리
    with SessionLocal() as db:
        reg_status = db.get(CollectStatus, ("regulation", "domestic"))
        if reg_status is not None:
            reg_status.status = "ok"
            db.commit()
    r = client.get("/cards", params={"tab": "regulation", "stock_code": "555550"}).json()
    assert r["items"] == [] and r["reason"] == "no_data"

    # 최근 수집 실패 기록이 있으면 fetch_failed
    with SessionLocal() as db:
        from app.deps import utcnow

        row = db.get(CollectStatus, ("disclosure", "444440"))
        if row is None:
            row = CollectStatus(tab="disclosure", scope_key="444440")
            db.add(row)
        row.status, row.detail, row.updated_at = "failed", "테스트", utcnow()
        db.commit()
    r = client.get("/cards", params={"tab": "disclosure", "stock_code": "444440"}).json()
    assert r["items"] == [] and r["reason"] == "fetch_failed"


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
