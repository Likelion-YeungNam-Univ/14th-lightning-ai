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
def test_briefing_keyword_filter_ignores_unmapped_ministry(client, monkeypatch):
    """F-4.2.2(v3 갱신) — 1차 필터는 산업 키워드. 부처 매핑에 없어도 키워드가 맞으면 통과한다.

    조직개편으로 부처명이 바뀌어도(B003의 '우주항공청'처럼 매핑표에 없는 이름) 업종
    규제 탭이 조용히 비지 않는다는 걸 보장하는 회귀 테스트(이슈 #42).
    """
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
        # B002만 제외(키워드 무관). B003은 부처 매핑에 없는 '우주항공청'이지만
        # 제목에 '반도체' 키워드가 있어 이제는 매칭된다(이전엔 부처 필터에서 먼저 버려졌음).
        assert stats["matched"] == 2

        item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "regulation", SourceItem.source_key == "B001")
            .one()
        )
        assert item.title == "반도체 특별법 시행령 개정안 입법예고"
        assert "반도체 산업 지원" in item.content  # HTML 태그 제거된 본문
        assert db.get(CollectStatus, ("regulation", "domestic")).status == "ok"

        unmapped_item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "regulation", SourceItem.source_key == "B003")
            .one()
        )
        assert unmapped_item.title == "매핑에 없는 부처의 반도체 관련 발표"


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


# ── DART 정형 API (이슈 #18) ────────────────────────────────────────────


@respx.mock
def test_dart_detail_enrichment(client, monkeypatch):
    """정형 유형은 상세 API를 접수번호로 조인해 content·detail_json을 채운다."""
    from app.services.industry import displayed_form_codes, seed_form_types

    monkeypatch.setattr("app.collectors.base.time.sleep", lambda _s: None)
    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "det-1",
                        "report_nm": "주요사항보고서(자기주식취득결정)",
                        "rcept_dt": "20260812",
                    },
                    {
                        "rcept_no": "det-2",
                        "report_nm": "자기주식취득결과보고서",  # 정형 API 없음 — 제목 기반 유지
                        "rcept_dt": "20260811",
                    },
                ],
            },
        )
    )
    respx.get(host="opendart.fss.or.kr", path="/api/tsstkAqDecsn.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "det-1",
                        "aqpln_stk_ostk": "36,671,401",
                        "aqpln_prc_ostk": "7,174,299,854,900",
                        "aqexpd_bgd": "2026년 06월 23일",
                        "aqexpd_edd": "2026년 07월 20일",
                        "aq_pp": "주주가치 제고",
                        "aq_mth": "장내매수",
                        "aqpln_stk_estk": "-",  # 값 없음 — 표시 제외
                    }
                ],
            },
        )
    )
    with SessionLocal() as db:
        seed_form_types(db)
        stock = db.query(StockMaster).filter_by(stock_code="222220").one()
        stock.corp_code = "C-DET"
        db.commit()
        from app.collectors.dart import sync_disclosures

        stats = sync_disclosures(db, [stock])
        assert stats["failed"] == 0

        enriched = db.query(SourceItem).filter_by(source_key="det-1").one()
        assert enriched.doc_type == "자기주식취득결정"
        assert "취득 예정 금액: 7,174,299,854,900원" in enriched.content
        assert "장내매수" in enriched.content
        assert "기타주식" not in enriched.content  # "-" 값 제외
        slots = enriched.detail_json["slots"]
        assert {"label": "취득 예정 금액", "value": "7,174,299,854,900원"} in slots

        plain = db.query(SourceItem).filter_by(source_key="det-2").one()
        assert plain.doc_type == "자기주식취득결과보고서"  # 긴 이름 우선 분류
        assert plain.content is None  # 정형 API 없는 유형은 그대로

        # 노출 정책: 두 유형 모두 display=true, 임원 보고서는 false
        codes = displayed_form_codes("domestic")
        assert {"자기주식취득결정", "자기주식취득결과보고서", "반기보고서"} <= codes
        assert "임원ㆍ주요주주특정증권등소유상황보고서" not in codes


@respx.mock
def test_dart_detail_enrichment_remaining_three_types(client, monkeypatch):
    """QA 확인 요청 — 실 데이터로 아직 못 본 나머지 3종(무상증자·감자·소송)도
    정형 API 필드가 정확히 슬롯으로 뽑히는지 검증한다. 나머지 3종(자기주식취득·
    자기주식처분·유상증자)은 각각 다른 테스트/실 DB로 이미 확인됨."""
    from app.services.industry import seed_form_types

    monkeypatch.setattr("app.collectors.base.time.sleep", lambda _s: None)
    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "fr-1",
                        "report_nm": "주요사항보고서(무상증자결정)",
                        "rcept_dt": "20260812",
                    },
                    {
                        "rcept_no": "cr-1",
                        "report_nm": "주요사항보고서(감자결정)",
                        "rcept_dt": "20260812",
                    },
                    {
                        "rcept_no": "lg-1",
                        "report_nm": "주요사항보고서(소송등의제기)",
                        "rcept_dt": "20260812",
                    },
                ],
            },
        )
    )
    respx.get(host="opendart.fss.or.kr", path="/api/fricDecsn.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "fr-1",
                        "nstk_ostk_cnt": "10,000,000",
                        "nstk_ascnt_ps_ostk": "0.5",
                        "nstk_asstd": "2026년 09월 01일",
                    }
                ],
            },
        )
    )
    respx.get(host="opendart.fss.or.kr", path="/api/crDecsn.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "cr-1",
                        "crstk_ostk_cnt": "5,000,000",
                        "cr_rt_ostk": "50%",
                        "cr_mth": "주식병합",
                    }
                ],
            },
        )
    )
    respx.get(host="opendart.fss.or.kr", path="/api/lwstLg.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "lg-1",
                        "icnm": "특허침해금지 청구의 소",
                        "ac_ap": "홍길동 외 2인",
                        "lgd": "2026년 08월 10일",
                    }
                ],
            },
        )
    )
    with SessionLocal() as db:
        seed_form_types(db)
        stock = db.query(StockMaster).filter_by(stock_code="222220").one()
        stock.corp_code = "C-DET2"
        db.commit()
        from app.collectors.dart import sync_disclosures

        stats = sync_disclosures(db, [stock])
        assert stats["failed"] == 0

        free = db.query(SourceItem).filter_by(source_key="fr-1").one()
        assert free.doc_type == "무상증자결정"
        assert {"label": "신주 수(보통주)", "value": "10,000,000주"} in free.detail_json["slots"]
        assert {"label": "1주당 신주 배정", "value": "0.5주"} in free.detail_json["slots"]

        cr = db.query(SourceItem).filter_by(source_key="cr-1").one()
        assert cr.doc_type == "감자결정"
        assert {"label": "감자 주식 수(보통주)", "value": "5,000,000주"} in cr.detail_json["slots"]
        assert {"label": "감자 방법", "value": "주식병합"} in cr.detail_json["slots"]

        lg = db.query(SourceItem).filter_by(source_key="lg-1").one()
        assert lg.doc_type == "소송등의제기"
        assert {"label": "사건명", "value": "특허침해금지 청구의 소"} in lg.detail_json["slots"]
        assert {"label": "원고·신청인", "value": "홍길동 외 2인"} in lg.detail_json["slots"]


def test_detail_fields_feed_summary_input(client):
    """정형 필드가 요약 입력에 들어가 숫자 가드레일(F-5.1.3)과 정합한다."""
    from tests.test_ai import GOOD, FakeLLM

    with SessionLocal() as db:
        item = db.query(SourceItem).filter_by(source_key="det-1").one()  # 위 테스트에서 적재
        fake = FakeLLM([{**GOOD, "summary_short": "약 7,174,299,854,900원 규모예요."}])
        from app.ai.summarize import generate_summaries

        stats = generate_summaries(db, fake, items=[item])
        assert stats["generated"] == 1 and len(fake.calls) == 1  # 재생성 없음
        assert "공시 상세" in fake.calls[0]  # 정형 필드가 입력에 포함


@respx.mock
def test_funding_total_slot(client, monkeypatch):
    """이슈 #26 — 유상증자 첫 슬롯 = 조달 금액 합산. 숫자 아닌 값 혼입 시 합계 생략."""
    monkeypatch.setattr("app.collectors.base.time.sleep", lambda _s: None)
    from app.collectors.dart import sync_disclosures
    from app.services.industry import seed_form_types

    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "pi-1",
                        "report_nm": "주요사항보고서(유상증자결정)",
                        "rcept_dt": "20260813",
                    },
                    {
                        "rcept_no": "pi-2",
                        "report_nm": "[기재정정]주요사항보고서(유상증자결정)",
                        "rcept_dt": "20260812",
                    },
                ],
            },
        )
    )
    respx.get(host="opendart.fss.or.kr", path="/api/piicDecsn.json").mock(
        return_value=Response(
            200,
            json={
                "status": "000",
                "list": [
                    {  # 정상 — 용도별 합산 (시설 100,000 + 운영 50,000, 나머지 "-")
                        "rcept_no": "pi-1",
                        "fdpp_fclt": "100,000",
                        "fdpp_op": "50,000",
                        "fdpp_bsninh": "-",
                        "fdpp_dtrp": "-",
                        "fdpp_ocsa": "-",
                        "fdpp_etc": "-",
                        "nstk_ostk_cnt": "1,600",
                        "ic_mthn": "주주배정증자",
                    },
                    {  # 숫자 아닌 값 혼입 — 합계 생략, 나머지 슬롯은 유지
                        "rcept_no": "pi-2",
                        "fdpp_fclt": "미정",
                        "fdpp_op": "50,000",
                        "nstk_ostk_cnt": "800",
                        "ic_mthn": "제3자배정증자",
                    },
                ],
            },
        )
    )
    with SessionLocal() as db:
        seed_form_types(db)
        stock = db.query(StockMaster).filter_by(stock_code="333330").one()
        stock.corp_code = "C-PI"
        db.commit()
        sync_disclosures(db, [stock])

        ok = db.query(SourceItem).filter_by(source_key="pi-1").one()
        assert ok.doc_type == "유상증자결정"  # 포장 유형(주요사항보고서)이 아닌 세부 유형
        assert ok.detail_json["slots"][0] == {"label": "조달 금액", "value": "150,000원"}
        assert "조달 금액(합계): 150,000원" in ok.content  # 요약 입력에도 포함 (F-5.1.3 정합)
        assert {"label": "증자 방식", "value": "주주배정증자"} in ok.detail_json["slots"]

        bad = db.query(SourceItem).filter_by(source_key="pi-2").one()
        labels = [s["label"] for s in bad.detail_json["slots"]]
        assert "조달 금액" not in labels  # 틀린 총액을 만들지 않는다
        assert "증자 방식" in labels
