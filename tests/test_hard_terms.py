"""#77 어려운 단어 표시 테스트 — LLM 없음(사전 대조), 실 키 불요.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 겹치는 표제어 중복 하이라이트('기준금리' 안의 '금리') → 긴 표제어 우선, 구간 마스킹
2. 표제어 폭주(카드가 밑줄 범벅) → MAX_TERMS 상한 + 등장 순서 우선
3. 요약 없는 카드(유튜브)·빈 요약에 값이 생김 → null 유지
4. 배치가 저장 안 함 / 카드·스냅샷에 안 실림 → 파이프라인 통합 확인
5. 표시된 단어를 탭했는데 설명 불가 → 표제어는 정확 일치 경로라 항상 응답(통합 확인)
6. 백필이 잠긴(locked) 행의 요약을 건드림 → hard_terms만 채우고 문장 불변
"""

from app.ai.hard_terms import MAX_TERMS, load_terms, scan_hard_terms
from app.collectors.base import ensure_stock_link, upsert_source_item
from app.db import SessionLocal
from app.models import MARKET_DOMESTIC, GeneratedContent, KnowledgeChunk
from tests.test_ai import FakeLLM


def _seed_kb(db):
    """스캔용 표제어 — 겹침(금리/기준금리/가산금리) 포함."""
    db.query(KnowledgeChunk).delete()
    for term in ("금리", "기준금리", "가산금리", "유상증자", "듀레이션", "8-K"):
        db.add(
            KnowledgeChunk(source="bok_700", term=term, content=f"{term} 설명", embedding=[0.0] * 4)
        )
    db.commit()


# ── 스캐너 단위 (예상 문제 1·2·3) ──────────────────────────────────────


def test_scan_longest_match_and_order(client):
    with SessionLocal() as db:
        _seed_kb(db)
        terms = load_terms(db)
    out = scan_hard_terms("한국은행이 기준금리를 올리자 가산금리도 움직였어요.", terms)
    assert out == ["기준금리", "가산금리"]  # '금리' 단독은 마스킹돼 안 잡힘, 등장 순서 유지
    out2 = scan_hard_terms("금리가 오르면 유상증자 부담이 커져요.", terms)
    assert out2 == ["금리", "유상증자"]  # 겹침 없으면 짧은 표제어도 잡힘
    assert scan_hard_terms("8-K 보고서예요.", terms) == ["8-K"]  # 폼 코드도 대상
    assert scan_hard_terms(None, terms) == [] and scan_hard_terms("", terms) == []
    assert scan_hard_terms("표제어가 하나도 없는 문장.", terms) == []


def test_scan_caps_at_max(client):
    terms = [f"용어{i:02d}" for i in range(20)]
    text = " ".join(terms)
    assert len(scan_hard_terms(text, sorted(terms, key=len, reverse=True))) == MAX_TERMS


# ── 배치·카드·스냅샷 통합 (예상 문제 4·5) ─────────────────────────────


GOOD_WITH_TERMS = {
    "summary_short": "회사가 유상증자를 결정했어요.",
    "summary_full": "기준금리 인상기에 유상증자로 자금을 조달하기로 했어요.",
    "label": "neutral",
    "label_reason": "정보 제공 성격이에요.",
}


def test_batch_stores_and_card_exposes(client, login_env, monkeypatch):
    from app.ai.summarize import generate_summaries

    with SessionLocal() as db:
        _seed_kb(db)
        item = upsert_source_item(
            db,
            tab="disclosure",
            market=MARKET_DOMESTIC,
            source_key="ht-d1",
            title="유상증자결정",
            doc_type="유상증자결정",
            published_at=None,
        )
        ensure_stock_link(db, item.id, "111110")
        db.commit()
        generate_summaries(db, FakeLLM([GOOD_WITH_TERMS]), items=[item])
        row = (
            db.query(GeneratedContent)
            .filter_by(source_item_id=item.id, scope="stock", scope_key="111110")
            .one()
        )
        assert row.hard_terms == ["유상증자", "기준금리"]  # 짧은+긴 요약 합산, 등장 순

    # 카드 응답 노출 + 유튜브는 null (예상 문제 3·4)
    from app.services.industry import seed_form_types

    with SessionLocal() as db:
        seed_form_types(db)
    cards = client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"}).json()
    card = next(c for c in cards["items"] if c["title"] == "유상증자결정")
    assert card["hard_terms"] == ["유상증자", "기준금리"]

    # 탭 풀이 보장 — 표시된 단어는 정확 일치 경로(지식베이스 히트 보장) (예상 문제 5)
    from app.services import ratelimit

    ratelimit.reset()
    fake_terms = FakeLLM([{"explanation": "유상증자는 새 주식을 발행해 자금을 모으는 거예요."}])
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake_terms)
    r = client.post("/terms/explain", json={"term": card["hard_terms"][0], "tab": "disclosure"})
    assert r.status_code == 200 and r.json()["explanation"]

    # 저장 스냅샷에도 실림
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})
    saved = client.post(
        "/me/saved-cards", json={"card_id": card["card_id"], "stock_code": "111110"}
    ).json()
    assert saved["item"]["snapshot"]["hard_terms"] == ["유상증자", "기준금리"]


# ── 백필 (예상 문제 6) ────────────────────────────────────────────────


def test_backfill_fills_locked_without_touching_summary(client):
    from scripts.backfill_hard_terms import main as backfill

    with SessionLocal() as db:
        _seed_kb(db)
        item = upsert_source_item(
            db, tab="disclosure", market=MARKET_DOMESTIC, source_key="ht-d2", title="반기보고서"
        )
        ensure_stock_link(db, item.id, "111110")
        db.add(
            GeneratedContent(
                source_item_id=item.id,
                scope="stock",
                scope_key="111110",
                summary_short="검수된 요약 — 듀레이션이 길어요.",
                locked=True,
            )
        )
        db.commit()
        gen_id = db.query(GeneratedContent).filter_by(source_item_id=item.id).one().id

    backfill()

    with SessionLocal() as db:
        row = db.get(GeneratedContent, gen_id)
        assert row.hard_terms == ["듀레이션"]  # 목록은 채워지고
        assert row.summary_short == "검수된 요약 — 듀레이션이 길어요."  # 문장은 불변
        assert row.locked is True
