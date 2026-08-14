"""F-3.2·F-4.6·F-4.7 해외 축 테스트 — SEC·Federal Register respx 목킹, LLM 페이크.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. SEC_USER_AGENT 미설정인데 호출 → 호출 전 실패 처리(차단 방지)
2. 대상 외 폼(4·144 등)까지 수집 → 8-K·10-Q·10-K·6-K만
3. 90일 밖 공시 수집 / 종목당 20건 초과 → 창·상한 준수
4. CIK 매핑 안 되는 티커 → no_cik 스킵 + 로그
5. FedReg 무효 기관 슬러그로 조회 → 유효 목록 대조 후 폐기 (F-4.7.1)
6. FedReg 키워드 무관 문서 적재 → 기관 소관이어도 버림
7. 해외 규제 relevant=false → 업종 링크 삭제·카드 미생성 (F-4.7.2)
8. 해외 공시 요약 입력에 폼 해설 누락 → 해설 포함 확인 (F-4.6.1)
9. 재수집 시 중복 적재 → upsert 멱등
"""

import respx
from httpx import Response

from app.ai.summarize import generate_summaries
from app.collectors.base import ensure_industry_link, ensure_stock_link, upsert_source_item
from app.collectors.fedreg import sync_us_regulations
from app.collectors.sec import sync_overseas_master, sync_sec_disclosures
from app.config import settings
from app.db import SessionLocal
from app.deps import utcnow
from app.models import (
    MARKET_OVERSEAS,
    CollectStatus,
    GeneratedContent,
    IndustryAgency,
    SourceItem,
    SourceItemIndustry,
    SourceItemStock,
    StockMaster,
)
from app.services.industry import (
    load_overseas_industries,
    seed_form_types,
    seed_overseas_industries,
)
from tests.test_ai import GOOD, FakeLLM

COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
}


def _recent(rows):
    """(form, filingDate, accession, desc, doc) 목록 → submissions recent 병렬 배열."""
    return {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "primaryDocDescription": [r[3] for r in rows],
        "primaryDocument": [r[4] for r in rows],
    }


def _submissions(rows, sic="3711"):
    return {"name": "Test Co", "sic": sic, "filings": {"recent": _recent(rows)}}


# ── SEC (예상 문제 1~4·9) ──────────────────────────────────────────────


@respx.mock
def test_overseas_master_resolves_cik_and_sic(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.collectors.sec.time.sleep", lambda _s: None)
    # 실제 15개 대신 2개짜리 임시 화이트리스트 — 다른 테스트의 2종 전제 유지
    (tmp_path / "overseas_whitelist.json").write_text(
        '{"stocks": [{"ticker": "AAPL", "name": "애플", "aliases": ["애플", "apple"]},'
        '{"ticker": "TSLA", "name": "테슬라", "aliases": ["테슬라", "tesla"]},'
        '{"ticker": "ZZZZ", "name": "없는회사", "aliases": []}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.collectors.sec.DATA_DIR", tmp_path)

    respx.get(host="www.sec.gov", path="/files/company_tickers.json").mock(
        return_value=Response(200, json=COMPANY_TICKERS)
    )
    seen_ua = []

    def sub_responder(request):
        seen_ua.append(request.headers.get("User-Agent"))
        sic = "3571" if "0000320193" in request.url.path else "3711"
        return Response(200, json=_submissions([], sic=sic))

    respx.get(host="data.sec.gov", path__startswith="/submissions/").mock(side_effect=sub_responder)

    with SessionLocal() as db:
        stats = sync_overseas_master(db)
        assert stats["stocks"] == 2
        assert stats["no_cik"] == 1  # ZZZZ — 매핑 없음 스킵
        aapl = db.get(StockMaster, "AAPL")
        assert aapl.cik == "0000320193" and aapl.sic_code == "3571"  # 10자리 패딩 + SIC
        assert db.get(StockMaster, "TSLA").cik == "0001318605"
        assert seen_ua and all(ua == settings.sec_user_agent for ua in seen_ua)  # UA 필수
        # 업종·폼 해설 시드 동반
        assert stats["industries"] == len(load_overseas_industries())
        assert stats["form_types"] == 22  # 국내 18 + 미국 4 (이슈 #18 유형 확장)


@respx.mock
def test_sec_disclosures_form_filter_window_and_dedup(client, monkeypatch):
    monkeypatch.setattr("app.collectors.sec.time.sleep", lambda _s: None)
    today = utcnow().strftime("%Y-%m-%d")
    rows = [
        ("8-K", today, "acc-1", "8-K", "a.htm"),
        ("4", today, "acc-2", "FORM 4", "b.xml"),  # 대상 외 폼
        ("10-Q", today, "acc-3", "", "c.htm"),  # 설명 없음 → 폼 코드가 제목
        ("10-K", "2020-01-01", "acc-4", "10-K", "d.htm"),  # 90일 밖
    ]
    respx.get(host="data.sec.gov", path__startswith="/submissions/").mock(
        return_value=Response(200, json=_submissions(rows))
    )
    with SessionLocal() as db:
        tsla = db.get(StockMaster, "TSLA")
        tsla.cik = "0001318605"
        db.commit()

        stats = sync_sec_disclosures(db, [tsla])
        assert stats == {"stocks": 1, "items": 2, "failed": 0, "no_cik": 0}

        items = {
            i.source_key: i
            for i in db.query(SourceItem).filter(
                SourceItem.tab == "disclosure", SourceItem.market == MARKET_OVERSEAS
            )
        }
        assert set(items) >= {"acc-1", "acc-3"}
        assert "acc-2" not in items and "acc-4" not in items
        assert items["acc-3"].title == "10-Q"  # 영문 원문/폼 코드 그대로 — 번역 없음
        assert items["acc-1"].doc_type == "8-K"
        assert "edgar/data/1318605/acc1/a.htm" in items["acc-1"].origin_url
        assert db.get(SourceItemStock, (items["acc-1"].id, "TSLA")) is not None

        before = len(items)
        sync_sec_disclosures(db, [tsla])  # 멱등
        count_after = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "disclosure", SourceItem.market == MARKET_OVERSEAS)
            .count()
        )
        assert count_after == before


def test_sec_without_user_agent_fails_before_call(client, monkeypatch):
    monkeypatch.setattr(settings, "sec_user_agent", "")
    with SessionLocal() as db:
        tsla = db.get(StockMaster, "TSLA")
        tsla.cik = "0001318605"
        db.commit()
        stats = sync_sec_disclosures(db, [tsla])  # respx 없이 — 호출되면 실 API로 나간다
        assert stats["failed"] == 1
        assert db.get(CollectStatus, ("disclosure", "TSLA")).status == "failed"
        assert "SEC_USER_AGENT" in db.get(CollectStatus, ("disclosure", "TSLA")).detail


# ── Federal Register (예상 문제 5·6) ───────────────────────────────────


FEDREG_DOCS = {
    "results": [
        {
            "title": "New Drug Approval Pathway Rule",
            "abstract": "FDA finalizes a rule on prescription drug approval.",
            "agencies": [{"slug": "food-and-drug-administration"}],
            "publication_date": "2026-08-01",
            "html_url": "https://www.federalregister.gov/d/FR-1",
            "document_number": "FR-1",
            "type": "Rule",
        },
        {
            "title": "Paperwork Reduction Act Notice",  # 키워드 무관
            "abstract": "Administrative information collection notice.",
            "agencies": [{"slug": "food-and-drug-administration"}],
            "publication_date": "2026-08-02",
            "html_url": "https://www.federalregister.gov/d/FR-2",
            "document_number": "FR-2",
            "type": "Rule",
        },
    ],
    "next_page_url": None,
}


@respx.mock
def test_fedreg_agency_validation_and_keyword_filter(client):
    with SessionLocal() as db:
        seed_overseas_industries(db)
        db.add(  # 무효 슬러그 검증용 가짜 업종
            IndustryAgency(
                market=MARKET_OVERSEAS,
                industry_key="9999",
                name="가짜업종",
                agencies=["not-a-real-agency"],
                keywords=["nothing"],
                profile="검증용",
            )
        )
        db.commit()

        valid = sorted({s for row in load_overseas_industries() for s in row["agencies"]})
        respx.get(host="www.federalregister.gov", path="/api/v1/agencies").mock(
            return_value=Response(200, json=[{"slug": s} for s in valid])
        )
        seen_params = []

        def doc_responder(request):
            seen_params.append(str(request.url))
            return Response(200, json=FEDREG_DOCS)

        respx.get(host="www.federalregister.gov", path="/api/v1/documents.json").mock(
            side_effect=doc_responder
        )

        stats = sync_us_regulations(db)
        assert stats["dropped_slugs"] == 1  # not-a-real-agency 폐기 (F-4.7.1)
        assert stats["scanned"] == 2 and stats["matched"] == 1
        assert "not-a-real-agency" not in seen_params[0]
        assert "RULE" in seen_params[0] and "PRORULE" in seen_params[0]

        item = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "regulation", SourceItem.source_key == "FR-1")
            .one()
        )
        assert item.market == MARKET_OVERSEAS
        assert item.title == "New Drug Approval Pathway Rule"  # 영문 그대로
        assert db.get(SourceItemIndustry, (item.id, MARKET_OVERSEAS, "2834")) is not None
        # FR-2는 기관 소관이지만 키워드 무관 — 미적재
        assert db.query(SourceItem).filter(SourceItem.source_key == "FR-2").one_or_none() is None
        # 정리 — 다른 테스트에 가짜 업종이 남지 않게
        db.delete(db.get(IndustryAgency, (MARKET_OVERSEAS, "9999")))
        db.commit()


# ── 해외 AI 가공 (예상 문제 7·8) ───────────────────────────────────────


def test_overseas_regulation_relevance_drop(client):
    with SessionLocal() as db:
        seed_overseas_industries(db)
        item = upsert_source_item(
            db,
            tab="regulation",
            market=MARKET_OVERSEAS,
            source_key="rel-1",
            title="Unrelated Rule",
            content="Administrative details only.",
        )
        ensure_industry_link(db, item.id, MARKET_OVERSEAS, "3674")
        db.commit()

        irrelevant = {**GOOD, "relevant": False}
        stats = generate_summaries(db, FakeLLM([irrelevant]), items=[item])
        assert stats["dropped"] == 1 and stats["generated"] == 0
        # 업종 링크 삭제 + 카드(생성물) 미생성 (F-4.7.2)
        assert db.get(SourceItemIndustry, (item.id, MARKET_OVERSEAS, "3674")) is None
        assert db.query(GeneratedContent).filter_by(source_item_id=item.id).one_or_none() is None


def test_overseas_disclosure_prompt_includes_form_desc(client):
    with SessionLocal() as db:
        seed_form_types(db)
        item = upsert_source_item(
            db,
            tab="disclosure",
            market=MARKET_OVERSEAS,
            source_key="form-1",
            title="8-K",
            doc_type="8-K",
        )
        ensure_stock_link(db, item.id, "TSLA")
        db.commit()

        fake = FakeLLM([GOOD])
        stats = generate_summaries(db, fake, items=[item])
        assert stats["generated"] == 1
        assert "유형 해설" in fake.calls[0]  # F-4.6.1 — 폼 해설이 요약 입력에 들어간다
        assert "수시 보고서" in fake.calls[0]
        assert "미국 상장사 공시" in fake.calls[0]
