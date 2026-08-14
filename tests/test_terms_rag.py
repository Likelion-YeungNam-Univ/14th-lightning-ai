"""F-5.4·F-5.8(RAG)·F-8.3 테스트 — LLM·임베딩은 페이크, 실 키 불필요.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 캐시 히트가 LLM·레이트리밋을 소모 → 캐시 우선, 호출 0
2. 50자 초과 / 빈 용어 / 잘못된 탭 → 400 (F-5.4 상한)
3. 분당 한도 초과 → 429 rate_limited (F-8.3)
4. RAG가 유사도 하한 미달 근거를 주입 → 미주입 확인
5. 검색 순위가 유사도순이 아님 → top-k 정렬 확인
6. 가드레일 위반 설명이 캐시·노출 → 재생성 후 실패 시 null + 미캐시
7. 임베딩 저장·검색 라운드트립 (sqlite 폴백 경로)
8. 국내 공시 doc_type 분류(긴 이름 우선) 누락 → 유형 해설 주입 불가
9. 요약 입력에 국내 공시유형 해설 미주입 → 주입 확인 (확정사항 5절)
10. 지식베이스 적재 구성(700선+국내유형+미국폼) 깨짐 → 청크 수·소스 확인
"""

import respx
from httpx import Response

from app.ai.rag import search_knowledge
from app.ai.summarize import generate_summaries
from app.collectors.base import ensure_stock_link, upsert_source_item
from app.db import SessionLocal
from app.models import MARKET_DOMESTIC, KnowledgeChunk, SourceItem, TermCache
from app.services import ratelimit
from app.services.industry import seed_form_types
from scripts.seed_knowledge import load_chunks
from tests.test_ai import GOOD, FakeLLM

E_BASE = [1.0, 0.0, 0.0, 0.0]
E_NEAR = [0.9, 0.1, 0.0, 0.0]
E_FAR = [0.0, 1.0, 0.0, 0.0]


class FakeRagLLM(FakeLLM):
    """FakeLLM + 임베딩 — 용어별 준비된 벡터 반환."""

    def __init__(self, outputs, embeddings=None):
        super().__init__(outputs)
        self.embeddings = embeddings or {}

    def embed(self, texts):
        return [self.embeddings.get(t, [0.0, 0.0, 0.0, 1.0]) for t in texts]


def _seed_chunks(db):
    db.query(KnowledgeChunk).delete()
    db.add_all(
        [
            KnowledgeChunk(
                source="bok_700",
                term="기준금리",
                content="중앙은행이 결정하는 정책금리다.",
                embedding=E_BASE,
            ),
            KnowledgeChunk(
                source="bok_700",
                term="콜금리",
                content="금융기관 간 초단기 금리다.",
                embedding=E_NEAR,
            ),
            KnowledgeChunk(
                source="bok_700", term="지니계수", content="소득 불평등 지표다.", embedding=E_FAR
            ),
        ]
    )
    db.commit()


# ── RAG 검색 (예상 문제 4·5·7) ─────────────────────────────────────────


def test_search_ranking_and_threshold(client):
    with SessionLocal() as db:
        _seed_chunks(db)
        hits = search_knowledge(db, E_BASE, top_k=3, min_similarity=0.35)
        assert [c.term for c, _ in hits] == ["기준금리", "콜금리"]  # 유사도순, 무관 항목 제외
        assert hits[0][1] > hits[1][1] >= 0.35
        # 전부 하한 미달인 질의 → 미주입 (F-5.8)
        assert search_knowledge(db, [0.0, 0.0, 1.0, 0.0]) == []


# ── 용어 풀이 API (예상 문제 1·2·3·6) ──────────────────────────────────


def _post_term(client, term, tab="bok", context=None):
    return client.post("/terms/explain", json={"term": term, "tab": tab, "context": context})


def test_explain_flow_cache_and_sources(client, monkeypatch):
    ratelimit.reset()
    with SessionLocal() as db:
        _seed_chunks(db)
        db.query(TermCache).delete()
        db.commit()
    fake = FakeRagLLM(
        [{"explanation": "기준금리는 중앙은행이 정하는 정책금리예요."}],
        embeddings={"기준금리": E_BASE},
    )
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    r = _post_term(client, "기준금리", context="한국은행이 기준금리를 인상했어요.")
    assert r.status_code == 200
    body = r.json()
    assert body["explanation"].startswith("기준금리는")
    assert body["cached"] is False
    assert body["sources"][0]["term"] == "기준금리"  # RAG 근거 노출 (설명 가능성)
    assert "지니계수" not in [s["term"] for s in body["sources"]]
    assert "중앙은행이 결정하는 정책금리" in fake.calls[0]  # 근거가 프롬프트에 주입됐다

    r2 = _post_term(client, "기준금리")  # 캐시 히트 — fake 출력이 비어 호출되면 실패
    assert r2.status_code == 200
    assert r2.json()["cached"] is True and len(fake.calls) == 1


def test_explain_exact_term_lookup(client, monkeypatch):
    """표제어 정확 일치 — 벡터 검색 없이 사전 항목이 근거가 된다 ('PER' → '주가수익비율(PER)')."""
    ratelimit.reset()
    with SessionLocal() as db:
        _seed_chunks(db)
        db.add(
            KnowledgeChunk(
                source="bok_700",
                term="주가수익비율(PER)",
                content="주가를 주당순이익으로 나눈 값으로 주가 수준을 평가하는 지표다.",
                embedding=E_FAR,
            )
        )
        db.query(TermCache).delete()
        db.commit()
    fake = FakeRagLLM([{"explanation": "PER은 주가를 주당순이익으로 나눈 지표예요."}])
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    r = _post_term(client, "PER", tab="disclosure")
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] == [
        {"term": "주가수익비율(PER)", "source": "bok_700", "similarity": 1.0}
    ]
    assert "주당순이익으로 나눈" in fake.calls[0]  # 표제어 정의가 근거로 주입


def test_explain_validation(client, monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: FakeRagLLM([]))
    assert _post_term(client, "가" * 51).json()["code"] == "term_too_long"
    assert _post_term(client, "   ").json()["code"] == "invalid_term"
    assert _post_term(client, "PER", tab="news").json()["code"] == "invalid_tab"


def test_explain_rate_limited(client, monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "IP_LIMIT_PER_MINUTE", 1)
    fake = FakeRagLLM([{"explanation": "설명 1이에요."}])
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)
    with SessionLocal() as db:
        db.query(TermCache).delete()
        db.commit()

    assert _post_term(client, "레이트용어1").status_code == 200
    r = _post_term(client, "레이트용어2")  # 캐시 없는 새 용어 — 한도 초과
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"


def test_explain_guardrail_failure_not_cached(client, monkeypatch):
    ratelimit.reset()
    bad = {"explanation": "이 종목은 상승할 전망이니 지금이 매수 기회입니다."}
    fake = FakeRagLLM([bad, bad])  # 재생성도 위반
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    r = _post_term(client, "위반용어")
    assert r.status_code == 200
    assert r.json()["explanation"] is None  # 필드 비움 (F-5.5)
    assert len(fake.calls) == 2
    with SessionLocal() as db:
        assert db.get(TermCache, ("위반용어", "bok")) is None  # 실패는 캐시하지 않는다


# ── 국내 공시유형 분류·해설 주입 (예상 문제 8·9) ───────────────────────


@respx.mock
def test_dart_doc_type_classified_longest_first(client, monkeypatch):
    monkeypatch.setattr("app.collectors.base.time.sleep", lambda _s: None)
    from app.collectors.dart import sync_disclosures
    from app.models import StockMaster

    dart_json = {
        "status": "000",
        "list": [
            {
                "rcept_no": "rag-r1",
                "report_nm": "[기재정정]반기보고서 (2026.06)",
                "rcept_dt": "20260810",
            },
            {
                "rcept_no": "rag-r2",
                "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
                "rcept_dt": "20260811",
            },
            {"rcept_no": "rag-r3", "report_nm": "기타경영사항(자율공시)", "rcept_dt": "20260812"},
        ],
    }
    respx.get(host="opendart.fss.or.kr", path="/api/list.json").mock(
        return_value=Response(200, json=dart_json)
    )
    with SessionLocal() as db:
        seed_form_types(db)
        stock = db.get(StockMaster, "555550")
        stock.corp_code = "C-RAG"
        db.commit()
        sync_disclosures(db, [stock])

        by_key = {
            i.source_key: i.doc_type
            for i in db.query(SourceItem).filter(SourceItem.source_key.like("rag-r%"))
        }
        assert by_key["rag-r1"] == "반기보고서"
        assert by_key["rag-r2"] == "임원ㆍ주요주주특정증권등소유상황보고서"
        assert by_key["rag-r3"] is None  # 분류표에 없는 유형 — 해설 없이 진행


def test_domestic_summary_gets_form_desc(client):
    with SessionLocal() as db:
        seed_form_types(db)
        item = upsert_source_item(
            db,
            tab="disclosure",
            market=MARKET_DOMESTIC,
            source_key="rag-s1",
            title="유상증자결정",
            doc_type="유상증자결정",
        )
        ensure_stock_link(db, item.id, "111110")
        db.commit()

        fake = FakeLLM([GOOD])
        generate_summaries(db, fake, items=[item])
        assert "유형 해설" in fake.calls[0]
        assert "자금을 조달" in fake.calls[0]  # 국내 해설이 요약 입력에 들어간다 (확정사항 5절)


# ── 지식베이스 구성 (예상 문제 10) ─────────────────────────────────────


def test_knowledge_chunk_composition():
    chunks = load_chunks()
    by_source = {}
    for c in chunks:
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
    assert by_source["bok_700"] >= 650  # 700선 파싱 성과 하한
    assert by_source["dart_doctype"] == 12
    assert by_source["sec_formtype"] == 4
    assert all(c["term"] and len(c["content"]) >= 20 for c in chunks)
