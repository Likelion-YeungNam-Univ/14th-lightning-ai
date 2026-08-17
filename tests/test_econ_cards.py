"""E-2·E-3·E-5 경제 상식 카드 테스트. LLM은 페이크 클라이언트, 링크 생존 확인은 respx 목킹.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 출처 없음 → 반려 (E-3.1.1)
2. 허용 도메인 밖 출처 → 반려 (E-3.1.2)
3. 본문에 숫자 포함(출처에 있어도) → 반려 (E-3.1.3)
4. 단정적 인과("반드시") → 반려 (E-3.1.4)
5. 매수 권유 등 가드레일 위반 → 반려 (E-3.1.5, F-5.5 재사용)
6. 출처 링크 전부 죽음 → 반려 (E-3.1.6)
7. 본문 각주 번호와 sources 번호 불일치 → 반려 (E-3.1.7)
8. 정상 카드 → filtered 상태로 대기 (승인 아님)
9. 검색 5회 초과 → 카드 자체를 버림(discarded), DB에 안 남음 (E-2.1a-2)
10. 표본 검수 대상 산정 — 10%·최소 2건
11. 배치 승인 → filtered 전부 approved+locked / 배치 반려 → 전부 rejected (E-3.2.1)
12. 회전 — 승인 풀에서 10장, 직전 세트 회피 (E-5.2·5.2.1)
13. 회전 — 승인 카드 10개 미만이면 있는 만큼만 (E-5.2.2)
"""

import respx
from httpx import Response

from app.ai.econ_cards import (
    apply_batch_review,
    auto_filter,
    generate_batch,
    generate_one,
    review_sample,
    rotate,
)
from app.db import SessionLocal
from app.models import EconCard, EconRotation

GOOD_SOURCES = [
    {"number": 1, "org": "한국은행", "doc_title": "기준금리란", "url": "https://www.bok.or.kr/x"}
]
GOOD_BODY = "기준금리는 중앙은행이 정하는 정책금리예요.(1) 시장금리에 영향을 주는 경향이 있어요."


class FakeSearchLLM:
    """OpenAIClient.generate_with_search와 같은 인터페이스."""

    def __init__(self, outputs):
        # outputs: list[(dict, search_count)]
        self.outputs = list(outputs)
        self.calls = 0

    def generate_with_search(self, *, system, user, schema, name="output", max_tool_calls=5):
        self.calls += 1
        assert self.outputs, "예상보다 많은 LLM 호출"
        return self.outputs.pop(0)


def _good_card(**overrides):
    card = {"title": "기준금리가 뭔가요?", "body": GOOD_BODY, "sources": GOOD_SOURCES}
    card.update(overrides)
    return card


@respx.mock
def test_auto_filter_passes_good_card():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    assert auto_filter(_good_card()) == []


def test_auto_filter_no_sources():
    reasons = auto_filter(_good_card(sources=[]))
    assert "출처 없음" in reasons


@respx.mock
def test_auto_filter_domain_not_allowed():
    respx.head("https://blog.naver.com/x").mock(return_value=Response(200))
    bad_sources = [{"number": 1, "org": "블로그", "doc_title": "글", "url": "https://blog.naver.com/x"}]
    reasons = auto_filter(_good_card(sources=bad_sources, body="설명이에요.(1)"))
    assert any("허용 도메인 밖" in r for r in reasons)


@respx.mock
def test_auto_filter_numbers_in_body():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    reasons = auto_filter(_good_card(body="현재 기준금리는 2.75%예요.(1)"))
    assert "본문에 숫자 포함" in reasons


@respx.mock
def test_auto_filter_absolute_claim():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    reasons = auto_filter(_good_card(body="기준금리가 오르면 반드시 주가가 내려요.(1)"))
    assert "단정적 인과 표현" in reasons


@respx.mock
def test_auto_filter_guardrail_violation():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    reasons = auto_filter(_good_card(body="지금이 매수 타이밍이에요.(1)"))
    assert any("권유" in r for r in reasons)


@respx.mock
def test_auto_filter_dead_link():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(404))
    reasons = auto_filter(_good_card())
    assert "출처 링크 전부 확인 불가" in reasons


@respx.mock
def test_auto_filter_footnote_mismatch():
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    reasons = auto_filter(_good_card(body="설명이에요.(1) 추가 설명이에요.(2)"))
    assert any("각주 불일치" in r for r in reasons)


@respx.mock
def test_generate_one_good_card_ends_up_filtered(client, login_env):
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    llm = FakeSearchLLM([(_good_card(), 2)])
    with SessionLocal() as db:
        card = generate_one(db, llm, batch_id="b1")
        assert card is not None
        assert card.status == "filtered"
        assert card.reject_reason is None


@respx.mock
def test_generate_one_bad_card_rejected_with_reason(client):
    llm = FakeSearchLLM([(_good_card(sources=[]), 1)])
    with SessionLocal() as db:
        card = generate_one(db, llm, batch_id="b2")
        assert card.status == "rejected"
        assert "출처 없음" in card.reject_reason


def test_generate_one_discards_over_search_limit(client):
    llm = FakeSearchLLM([(_good_card(), 6)])  # 5회 초과
    with SessionLocal() as db:
        before = db.query(EconCard).count()
        card = generate_one(db, llm, batch_id="b3")
        assert card is None
        assert db.query(EconCard).count() == before  # DB에 안 남음


@respx.mock
def test_generate_batch_mixed_results(client):
    respx.head("https://www.bok.or.kr/x").mock(return_value=Response(200))
    llm = FakeSearchLLM(
        [
            (_good_card(title="A"), 2),
            (_good_card(sources=[]), 1),  # 반려
            (_good_card(title="B"), 6),  # 검색초과 폐기
        ]
    )
    with SessionLocal() as db:
        stats = generate_batch(db, llm, count=3)
        assert stats["filtered"] == 1
        assert stats["rejected"] == 1
        assert stats["discarded"] == 1


def _seed_cards(db, batch_id, n, status="filtered"):
    ids = []
    for i in range(n):
        c = EconCard(
            title=f"카드{i}", body=GOOD_BODY, sources=GOOD_SOURCES, batch_id=batch_id, status=status
        )
        db.add(c)
        db.flush()
        ids.append(c.id)
    db.commit()
    return ids


def test_review_sample_min_2_and_10_percent(client):
    with SessionLocal() as db:
        _seed_cards(db, "batch-small", 5)  # 10%->0.5 -> 최소 2건 적용
        assert len(review_sample(db, "batch-small")) == 2

        _seed_cards(db, "batch-big", 30)  # 10% = 3건
        assert len(review_sample(db, "batch-big")) == 3


def test_apply_batch_review_pass_approves_and_locks(client):
    with SessionLocal() as db:
        ids = _seed_cards(db, "batch-pass", 4)
        result = apply_batch_review(db, "batch-pass", passed=True, reviewer="문경")
        assert result["affected"] == 4
        cards = db.query(EconCard).filter(EconCard.id.in_(ids)).all()
        assert all(c.status == "approved" and c.locked and c.approved_by == "문경" for c in cards)


def test_apply_batch_review_fail_rejects_whole_batch(client):
    with SessionLocal() as db:
        ids = _seed_cards(db, "batch-fail", 4)
        apply_batch_review(db, "batch-fail", passed=False, reviewer="문경")
        cards = db.query(EconCard).filter(EconCard.id.in_(ids)).all()
        assert all(c.status == "rejected" and not c.locked for c in cards)


def _clear_econ(db):
    """rotate()는 배치와 무관하게 전체 승인 풀을 본다 — 다른 테스트의 잔여 데이터와
    섞이지 않도록 회전 테스트 시작 전에 비운다."""
    db.query(EconRotation).delete()
    db.query(EconCard).delete()
    db.commit()


def test_rotate_picks_ten_and_avoids_previous_set(client):
    with SessionLocal() as db:
        _clear_econ(db)
        ids = _seed_cards(db, "batch-rot", 25, status="approved")
        first = rotate(db)
        assert len(first) == 10
        second = rotate(db)
        assert len(second) == 10
        assert set(first).isdisjoint(second)  # 직전 세트 회피(E-5.2.1)
        assert set(first) <= set(ids) and set(second) <= set(ids)


def test_rotate_pool_smaller_than_ten_returns_all(client):
    with SessionLocal() as db:
        _clear_econ(db)
        _seed_cards(db, "batch-small-pool", 4, status="approved")
        picked = rotate(db)
        assert len(picked) == 4  # E-5.2.2 — 있는 만큼만


def test_rotate_records_rotation_row(client):
    with SessionLocal() as db:
        _clear_econ(db)
        _seed_cards(db, "batch-rec", 12, status="approved")
        picked = rotate(db)
        last = db.query(EconRotation).order_by(EconRotation.id.desc()).first()
        assert last.card_ids == picked
