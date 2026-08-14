"""F-4 수집기 테스트 — 외부 API는 respx 목킹, 실 키 불필요 (CLAUDE.md DoD 3).

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. DART 013(자료 없음)을 실패로 오분류 → ok + 0건이어야 한다
2. 한 종목의 5xx 실패가 전체 수집을 중단 → 종목 단위 격리 (F-4.9)
3. 재수집 시 같은 자료가 중복 적재 → upsert 멱등
4. 정책브리핑 부처·키워드 필터가 무관 기사를 통과 → 소관 부처 + 키워드 매칭만
5. 정책브리핑 오류 XML(returnReasonCode)을 정상 응답으로 처리 → failed 기록
6. ECOS 혼합 항목 → 요청 URL에 0101000 필터 확인, 방향(인상) 산출
7. FRED 휴장일 "." 값으로 float 변환 크래시 → 스킵
8. 유튜브 쿼터 80% 도달 후 신규 검색 발사 → 호출 전 중단 (F-4.5.1)
9. 같은 종목 24시간 내 재검색 → fresh 스킵
10. 90일 정리(purge)가 저장 카드를 죽이거나 금리 탭을 비움 → SET NULL 생존, bok/fed 제외
11. 종목 추가 요청 경로에서 외부 API 동기 호출 → BackgroundTasks로 응답 후 수집
"""

from datetime import timedelta

import respx
from httpx import Response

from app.collectors.base import upsert_source_item
from app.collectors.briefing import sync_regulations
from app.collectors.dart import sync_disclosures
from app.collectors.ecos import sync_bok_rate
from app.collectors.fred import sync_fed_rate
from app.collectors.runner import purge_old_items
from app.collectors.youtube import sync_youtube
from app.db import SessionLocal
from app.deps import utcnow
from app.models import (
    MARKET_DOMESTIC,
    CollectStatus,
    QuotaUsage,
    SavedCard,
    SourceItem,
    SourceItemStock,
    StockMaster,
    UserSession,
)
from app.services.industry import seed_domestic_industries

DART_OK = {
    "status": "000",
    "message": "정상",
    "list": [
        {"rcept_no": "20260810000001", "report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260810"},
        {"rcept_no": "20260805000002", "report_nm": "주요사항보고서", "rcept_dt": "20260805"},
    ],
}
DART_EMPTY = {"status": "013", "message": "조회된 데이터가 없습니다."}

BRIEFING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
<header><resultCode>0</resultCode><resultMsg>NORMAL_SERVICE</resultMsg></header>
<body>
  <NewsItem>
    <NewsItemId>B001</NewsItemId><MinisterCode>산업통상부</MinisterCode>
    <Title>반도체 특별법 시행령 개정안 입법예고</Title>
    <DataContents>&lt;p&gt;정부는 반도체 산업 지원을 확대한다&lt;/p&gt;</DataContents>
    <ApproveDate>08/01/2026 10:00:00</ApproveDate>
    <OriginalUrl>https://korea.kr/news/B001</OriginalUrl>
  </NewsItem>
  <NewsItem>
    <NewsItemId>B002</NewsItemId><MinisterCode>산업통상부</MinisterCode>
    <Title>지역 소상공인 판로 지원 행사 개최</Title>
    <DataContents>산업 키워드와 무관한 행사 안내</DataContents>
    <ApproveDate>08/02/2026 10:00:00</ApproveDate>
  </NewsItem>
  <NewsItem>
    <NewsItemId>B003</NewsItemId><MinisterCode>우주항공청</MinisterCode>
    <Title>매핑에 없는 부처의 반도체 관련 발표</Title>
    <DataContents>본문</DataContents>
    <ApproveDate>08/03/2026 10:00:00</ApproveDate>
  </NewsItem>
</body></response>"""

BRIEFING_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
<header><resultCode>0</resultCode><resultMsg>NORMAL_SERVICE</resultMsg></header>
<body><totalCount>0</totalCount></body></response>"""

BRIEFING_ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OpenAPI_ServiceResponse><cmmMsgHeader>
  <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
  <returnReasonCode>30</returnReasonCode>
</cmmMsgHeader></OpenAPI_ServiceResponse>"""


def _domestic(db, codes):
    return db.query(StockMaster).filter(StockMaster.stock_code.in_(codes)).all()


def _reset_quota(db):
    db.query(QuotaUsage).delete()
    db.commit()


# ── DART (예상 문제 1·2·3) ──────────────────────────────────────────────


@respx.mock
def test_dart_ok_no_data_and_dedup(client):
    def responder(request):
        corp = request.url.params["corp_code"]
        return Response(200, json=DART_OK if corp == "C-AAA" else DART_EMPTY)

    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(side_effect=responder)
    with SessionLocal() as db:
        stocks = _domestic(db, ["111110", "222220"])
        stocks[0].corp_code, stocks[1].corp_code = (
            ("C-AAA", "C-BBB") if stocks[0].stock_code == "111110" else ("C-BBB", "C-AAA")
        )
        db.commit()

        stats = sync_disclosures(db, stocks)
        assert stats == {"stocks": 2, "items": 2, "failed": 0, "no_corp_code": 0}

        items = db.query(SourceItem).filter(SourceItem.tab == "disclosure").all()
        assert {i.source_key for i in items} >= {"20260810000001", "20260805000002"}
        one = next(i for i in items if i.source_key == "20260810000001")
        assert one.title == "반기보고서 (2026.06)"  # 원문 그대로
        assert db.get(SourceItemStock, (one.id, "111110")) is not None
        # 013(자료 없음)은 실패가 아니다 → ok
        assert db.get(CollectStatus, ("disclosure", "222220")).status == "ok"

        before = db.query(SourceItem).filter(SourceItem.tab == "disclosure").count()
        sync_disclosures(db, stocks)  # 재수집 — upsert 멱등
        assert db.query(SourceItem).filter(SourceItem.tab == "disclosure").count() == before


@respx.mock
def test_dart_failure_isolated(client, monkeypatch):
    monkeypatch.setattr("app.collectors.base.time.sleep", lambda _s: None)  # 백오프 대기 생략

    def responder(request):
        if request.url.params["corp_code"] == "C-FAIL":
            return Response(500)
        return Response(200, json=DART_EMPTY)

    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(side_effect=responder)
    with SessionLocal() as db:
        stocks = _domestic(db, ["333330", "444440"])
        for s in stocks:
            s.corp_code = "C-FAIL" if s.stock_code == "333330" else "C-OK"
        db.commit()

        stats = sync_disclosures(db, stocks)
        assert stats["failed"] == 1 and stats["stocks"] == 1  # 실패가 다른 종목을 막지 않는다
        assert db.get(CollectStatus, ("disclosure", "333330")).status == "failed"
        assert db.get(CollectStatus, ("disclosure", "444440")).status == "ok"


# ── 정책브리핑 (예상 문제 4·5) ──────────────────────────────────────────


@respx.mock
def test_briefing_ministry_and_keyword_filter(client, monkeypatch):
    monkeypatch.setattr("app.collectors.briefing.SLEEP_BETWEEN", 0)
    calls = {"n": 0}

    def responder(request):  # 3일 청크 30회 — 첫 청크만 자료, 나머지는 빈 응답
        assert "pageNo" not in request.url.params  # v2는 페이지 파라미터가 없다
        calls["n"] += 1
        return Response(200, text=BRIEFING_XML if calls["n"] == 1 else BRIEFING_EMPTY_XML)

    respx.get(host="apis.data.go.kr", path="/1371000/policyNewsService2/policyNewsList2").mock(
        side_effect=responder
    )
    with SessionLocal() as db:
        seed_domestic_industries(db)
        stats = sync_regulations(db)
        assert stats["chunks"] == 31  # 양끝 포함 91일 ÷ 3일 청크 (API 날짜 범위 제한)
        assert stats["scanned"] == 3
        assert stats["matched"] == 1  # B002는 키워드 무관, B003은 부처 미매핑

        item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "regulation", SourceItem.source_key == "B001")
            .one()
        )
        assert item.title == "반도체 특별법 시행령 개정안 입법예고"
        assert "반도체 산업 지원" in item.content  # HTML 태그 제거된 본문
        assert db.get(CollectStatus, ("regulation", "domestic")).status == "ok"


@respx.mock
def test_briefing_error_xml_marked_failed(client, monkeypatch):
    monkeypatch.setattr("app.collectors.briefing.SLEEP_BETWEEN", 0)
    respx.get(host="apis.data.go.kr", path="/1371000/policyNewsService2/policyNewsList2").mock(
        return_value=Response(200, text=BRIEFING_ERROR_XML)
    )
    with SessionLocal() as db:
        seed_domestic_industries(db)
        stats = sync_regulations(db)
        assert "error" in stats and "30" in stats["error"]
        assert db.get(CollectStatus, ("regulation", "domestic")).status == "failed"


# ── 금리 (예상 문제 6·7) ────────────────────────────────────────────────


@respx.mock
def test_ecos_direction_and_card_update(client):
    seen_urls: list[str] = []

    def responder(request):
        seen_urls.append(str(request.url))
        rows = [
            {"TIME": "20260101", "DATA_VALUE": "2.5"},
            {"TIME": "20260715", "DATA_VALUE": "2.5"},
            {"TIME": "20260716", "DATA_VALUE": "2.75"},
        ]
        return Response(200, json={"StatisticSearch": {"list_total_count": 3, "row": rows}})

    respx.get(host="ecos.bok.or.kr").mock(side_effect=responder)
    with SessionLocal() as db:
        db.query(SourceItem).filter(SourceItem.tab == "bok").delete()  # 다른 테스트의 bok 카드 제거
        db.commit()
        decision = upsert_source_item(  # 수동 시드된 결정문 카드가 지표를 받는다
            db,
            tab="bok",
            market=MARKET_DOMESTIC,
            source_key="bok-test-decision",
            title="한국은행 금융통화위원회 통화정책방향 (2026.7.16)",
            published_at=utcnow() - timedelta(days=30),
        )
        db.commit()

        result = sync_bok_rate(db)
        assert result == {"rate": "2.75%", "direction": "인상", "date": "20260716"}
        assert "0101000" in seen_urls[0]  # 항목 필터 없으면 다른 시리즈 혼입 (실측)

        db.refresh(decision)
        assert decision.indicator_value == "2.75%"
        assert decision.doc_type == "인상"
        assert db.get(CollectStatus, ("bok", "global")).status == "ok"


@respx.mock
def test_fred_skips_missing_values(client):
    obs = [
        {"date": "2026-06-01", "value": "4.00"},
        {"date": "2026-06-02", "value": "."},  # 휴장일 — float() 크래시 지점
        {"date": "2026-06-03", "value": "3.75"},
    ]
    respx.get(host="api.stlouisfed.org", path="/fred/series/observations").mock(
        return_value=Response(200, json={"observations": obs})
    )
    with SessionLocal() as db:
        result = sync_fed_rate(db)
        assert result == {"rate": "3.75%", "direction": "인하", "date": "2026-06-03"}
        card = db.query(SourceItem).filter(SourceItem.tab == "fed").first()
        assert card is not None and card.indicator_value == "3.75%"


# ── 유튜브 (예상 문제 8·9) ──────────────────────────────────────────────


def _youtube_search_json(*video_ids):
    return {
        "items": [
            {
                "id": {"videoId": vid},
                "snippet": {
                    "title": f"영상 {vid}",
                    "publishedAt": "2026-08-01T09:00:00Z",
                    "channelTitle": "테스트채널",
                    "thumbnails": {"medium": {"url": f"https://i.ytimg.com/{vid}.jpg"}},
                },
            }
            for vid in video_ids
        ]
    }


@respx.mock
def test_youtube_quota_guard_blocks_before_call(client):
    with SessionLocal() as db:
        _reset_quota(db)
        db.add(QuotaUsage(api_name="youtube", usage_date=utcnow().date(), units_used=7950))
        db.commit()
        stocks = _domestic(db, ["555550"])

        stats = sync_youtube(db, stocks)  # 목킹된 라우트 없음 — 호출되면 respx가 에러
        assert stats["quota_stop"] == 1 and stats["stocks"] == 0
        assert db.get(CollectStatus, ("youtube", "555550")).detail == "quota_guard"
        _reset_quota(db)


@respx.mock
def test_youtube_collect_and_fresh_skip(client):
    respx.get(host="www.googleapis.com", path="/youtube/v3/search").mock(
        return_value=Response(200, json=_youtube_search_json("vid1", "vid2"))
    )
    respx.get(host="www.googleapis.com", path="/youtube/v3/videos").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {"id": "vid1", "statistics": {"viewCount": "12345"}},
                    {"id": "vid2", "statistics": {}},
                ]
            },
        )
    )
    with SessionLocal() as db:
        _reset_quota(db)
        db.query(CollectStatus).filter(CollectStatus.scope_key == "111110").delete()
        db.commit()
        stocks = _domestic(db, ["111110"])

        stats = sync_youtube(db, stocks)
        assert stats["stocks"] == 1 and stats["items"] == 2

        item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "youtube", SourceItem.source_key == "vid1")
            .one()
        )
        assert item.view_count == 12345
        assert item.channel_name == "테스트채널"
        assert db.get(SourceItemStock, (item.id, "111110")) is not None
        # 사용량은 호출 직후 기록 — search 100 + videos 1
        quota = db.get(QuotaUsage, ("youtube", utcnow().date()))
        assert quota.units_used == 101

        stats2 = sync_youtube(db, stocks)  # 24시간 내 재수집 → 스킵
        assert stats2["skipped_fresh"] == 1 and stats2["stocks"] == 0
        assert quota.units_used == 101  # 추가 사용 없음


# ── 보관 정리 (예상 문제 10) ────────────────────────────────────────────


def test_purge_keeps_saved_cards_and_rate_tabs(client):
    client.post("/session")
    with SessionLocal() as db:
        old_at = utcnow() - timedelta(days=120)
        stale = upsert_source_item(
            db,
            tab="disclosure",
            market=MARKET_DOMESTIC,
            source_key="purge-old",
            title="오래된 공시",
            published_at=old_at,
        )
        old_bok = upsert_source_item(
            db,
            tab="bok",
            market=MARKET_DOMESTIC,
            source_key="purge-bok",
            title="오래된 결정문",
            published_at=old_at,
        )
        session = db.query(UserSession).first()
        saved = SavedCard(
            session_id=session.id,
            source_item_id=stale.id,
            tab="disclosure",
            stock_code="111110",
            snapshot_json={"title": "오래된 공시"},
        )
        db.add(saved)
        db.commit()
        stale_id, bok_id, saved_id = stale.id, old_bok.id, saved.id

        purged = purge_old_items(db)
        db.expire_all()  # expire_on_commit=False — 삭제·SET NULL 반영을 다시 읽는다
        assert purged >= 1
        assert db.get(SourceItem, stale_id) is None  # 90일 초과 삭제
        assert db.get(SourceItem, bok_id) is not None  # 금리 탭은 정리 제외
        survivor = db.get(SavedCard, saved_id)
        assert survivor is not None  # 저장 카드는 산다 (F-7.3)
        assert survivor.source_item_id is None  # SET NULL
        assert survivor.snapshot_json["title"] == "오래된 공시"


# ── 온디맨드 수집 (예상 문제 11) ────────────────────────────────────────


@respx.mock
def test_on_demand_collect_after_add(client, login_env):
    respx.get(host="www.googleapis.com", path="/youtube/v3/search").mock(
        return_value=Response(200, json=_youtube_search_json("vid-od"))
    )
    respx.get(host="www.googleapis.com", path="/youtube/v3/videos").mock(
        return_value=Response(
            200, json={"items": [{"id": "vid-od", "statistics": {"viewCount": "7"}}]}
        )
    )
    with SessionLocal() as db:  # 이전 테스트의 캐시·쿼터 초기화
        _reset_quota(db)
        db.query(CollectStatus).filter(CollectStatus.scope_key == "555550").delete()
        db.commit()

    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    r = client.post("/me/stocks", json={"stock_codes": ["555550"]})
    assert r.status_code == 200  # TestClient는 응답 후 BackgroundTasks를 실행한다

    with SessionLocal() as db:
        status = db.get(CollectStatus, ("youtube", "555550"))
        assert status is not None and status.status == "ok"  # 온디맨드 수집 완료
        item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "youtube", SourceItem.source_key == "vid-od")
            .one()
        )
        assert db.get(SourceItemStock, (item.id, "555550")) is not None
