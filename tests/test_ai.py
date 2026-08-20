"""F-5 AI 가공 테스트 — LLM은 페이크 클라이언트로 대체, 실 키 불필요 (DoD 3).

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 유튜브에 요약 생성 (F-5.1 위반) → 대상 탭 제외
2. 금리 탭에 라벨 부착 (F-5.2 위반) → 요약만
3. 권유·예측 표현 통과 → 재생성 1회로 교정
4. 재생성도 위반 → 위반 필드만 비움 (정상 라벨은 유지)
5. 입력에 없는 금액 숫자 생성 (F-5.1.3) → 차단, 입력에 있는 숫자·날짜는 통과
6. locked 행을 배치가 덮어씀 (F-5.7) → 건너뜀, LLM 호출 0
7. 재실행 시 중복 생성·중복 과금 → 멱등(기존 행 스킵)
8. 연결 문장에 종목명 (F-5.3.1) → 재생성, 그래도 위반이면 행 미생성
9. 같은 지표 버전 재실행 → 캐시 히트, LLM 호출 0 (F-5.3.2)
10. 지표 갱신 시 문장 안 바뀜 → 새 버전 키로 재생성
11. LLM 한 건 실패가 배치 전체 중단 → 생성 단위 격리
12. "자사주 매수 결정" 같은 사실 서술 오탐 → 가드레일 통과 확인
"""

from app.ai.guardrail import find_unsourced_numbers, find_violations
from app.ai.link_sentence import generate_link_sentences
from app.ai.llm_client import LLMError
from app.ai.summarize import generate_summaries
from app.collectors.base import upsert_source_item
from app.db import SessionLocal
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, GeneratedContent, IndustryAgency, RateLinkSentence
from app.services.industry import seed_domestic_industries


class FakeLLM:
    """OpenAIClient와 같은 인터페이스 — 준비된 출력을 순서대로 반환."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[str] = []
        self.efforts: list[str | None] = []  # 호출별 reasoning_effort (#69)

    def generate_json(self, *, system, user, schema, name="output", reasoning_effort=None):
        self.calls.append(user)
        self.efforts.append(reasoning_effort)
        assert self.outputs, "예상보다 많은 LLM 호출"
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return dict(out)


GOOD = {
    "summary_short": "회사가 반기 실적을 공시했어요.",
    "summary_full": (
        "이 공시는 반기 실적을 담은 정기 보고예요. 자세한 수치는 원문에서 확인할 수 있어요."
    ),
    "label": "neutral",
    "label_reason": "정기 보고라 영향이 제한적이에요.",
}
BAD_ADVICE = {**GOOD, "summary_short": "실적이 좋으니 지금이 매수 기회로 보입니다."}


def _disclosure_item(db, source_key, stock_code="111110", title="반기보고서 (2026.06)"):
    item = upsert_source_item(
        db, tab="disclosure", market=MARKET_DOMESTIC, source_key=source_key, title=title
    )
    from app.collectors.base import ensure_stock_link

    ensure_stock_link(db, item.id, stock_code)
    db.commit()
    return item


def _row(db, item, scope="stock", key="111110"):
    return (
        db.query(GeneratedContent)
        .filter_by(source_item_id=item.id, scope=scope, scope_key=key)
        .one_or_none()
    )


# ── 가드레일 단위 (예상 문제 12) ────────────────────────────────────────


def test_guardrail_patterns():
    assert find_violations("지금이 매수 기회입니다") == ["매수·매도 권유"]
    assert find_violations("주가가 상승할 전망입니다") == ["방향 예측"]
    assert find_violations("목표가 10만 원을 제시") == ["수치 단정"]
    # 사실 서술은 오탐하지 않는다
    assert find_violations("자사주 매수 결정을 공시했어요.") == []
    assert find_violations("금리가 0.25%p 인상되었어요.") == []


def test_unsourced_numbers():
    src = "제목: 유상증자 500억 결정 (2026.08.10)"
    assert find_unsourced_numbers("500억 원 규모예요.", src) == []  # 입력에 있는 숫자
    assert find_unsourced_numbers("2026년 8월에 나온 소식이에요.", src) == []  # 날짜는 제외
    assert find_unsourced_numbers("약 3조 원 규모예요.", src) != []  # 입력에 없는 금액


# ── 요약·라벨 (예상 문제 1·2·3·4·5·6·7·11) ─────────────────────────────


def test_summary_and_label_for_disclosure(client):
    with SessionLocal() as db:
        item = _disclosure_item(db, "ai-d1")
        fake = FakeLLM([GOOD])
        stats = generate_summaries(db, fake, items=[item])
        assert stats["generated"] == 1 and len(fake.calls) == 1
        row = _row(db, item)
        assert row.summary_short == GOOD["summary_short"]
        assert row.label == "neutral" and row.label_reason

        # 멱등 — 재실행 시 LLM 호출 없음
        stats2 = generate_summaries(db, FakeLLM([]), items=[item])
        assert stats2["skipped"] == 1 and stats2["generated"] == 0


def test_youtube_excluded_and_rate_has_no_label(client):
    with SessionLocal() as db:
        yt = upsert_source_item(
            db, tab="youtube", market=MARKET_DOMESTIC, source_key="ai-y1", title="영상"
        )
        bok = upsert_source_item(
            db,
            tab="bok",
            market=MARKET_DOMESTIC,
            source_key="ai-b1",
            title="통화정책방향",
            indicator_value="2.75%",
            doc_type="인상",
            content="기준금리를 2.75%로 인상하기로 결정했다.",
        )
        db.commit()
        fake = FakeLLM(
            [
                {
                    "summary_short": "기준금리가 2.75%로 올랐어요.",
                    "summary_full": "한국은행이 기준금리를 인상했어요.",
                }
            ]
        )
        stats = generate_summaries(db, fake, items=[yt, bok])
        assert stats["skipped"] == 1  # 유튜브 제외 (F-5.1)
        assert stats["generated"] == 1
        row = _row(db, bok, scope="global", key="global")
        assert row.summary_short and row.label is None  # 금리 탭 라벨 미부착 (F-5.2)


def test_guardrail_retry_then_fix(client):
    with SessionLocal() as db:
        item = _disclosure_item(db, "ai-d2")
        fake = FakeLLM([BAD_ADVICE, GOOD])
        generate_summaries(db, fake, items=[item])
        assert len(fake.calls) == 2  # 재생성 1회
        assert "재생성 요청" in fake.calls[1]
        assert _row(db, item).summary_short == GOOD["summary_short"]


def test_guardrail_empties_only_bad_fields(client):
    with SessionLocal() as db:
        item = _disclosure_item(db, "ai-d3")
        fake = FakeLLM([BAD_ADVICE, BAD_ADVICE])  # 재생성도 위반
        stats = generate_summaries(db, fake, items=[item])
        assert stats["emptied"] == 1
        row = _row(db, item)
        assert row.summary_short is None and row.summary_full is None
        assert row.label == "neutral"  # 정상이었던 라벨은 유지


def test_unsourced_number_blocked(client):
    with SessionLocal() as db:
        item = _disclosure_item(db, "ai-d4", title="유상증자 결정")
        bad_number = {**GOOD, "summary_short": "약 3조 원 규모의 유상증자예요."}
        fake = FakeLLM([bad_number, GOOD])
        generate_summaries(db, fake, items=[item])
        assert len(fake.calls) == 2
        assert "입력에 없는 숫자" in fake.calls[1]


def test_locked_row_untouched(client):
    with SessionLocal() as db:
        item = _disclosure_item(db, "ai-d5")
        db.add(
            GeneratedContent(
                source_item_id=item.id,
                scope="stock",
                scope_key="111110",
                summary_short="검수 완료 요약",
                locked=True,
            )
        )
        db.commit()
        stats = generate_summaries(db, FakeLLM([]), items=[item])  # 호출되면 assert 실패
        assert stats["locked"] == 1
        assert _row(db, item).summary_short == "검수 완료 요약"  # F-5.7


def test_one_failure_does_not_stop_batch(client):
    with SessionLocal() as db:
        a = _disclosure_item(db, "ai-d6")
        b = _disclosure_item(db, "ai-d7")
        fake = FakeLLM([LLMError("일시 오류"), GOOD])
        stats = generate_summaries(db, fake, items=[a, b])
        assert stats["failed"] == 1 and stats["generated"] == 1


# ── 연결 문장 (예상 문제 8·9·10) ────────────────────────────────────────


def _seed_indicator(db, value="2.75%", direction="인상"):
    item = upsert_source_item(
        db,
        tab="bok",
        market=MARKET_DOMESTIC,
        source_key="ai-ind",
        title="한국은행 기준금리",
        indicator_value=value,
        doc_type=direction,
        published_at=utcnow(),  # published_at 없는 다른 bok 카드보다 항상 최신이 되게
    )
    db.commit()
    return item


LINK_OK = {
    "sentence": "금리가 오르면 대출 이자 부담이 커져 이 업종 수요에 영향을 주는 경향이 있어요."
}


def test_link_sentence_cache_and_version(client):
    with SessionLocal() as db:
        seed_domestic_industries(db)
        _seed_indicator(db)
        n_industries = (
            db.query(IndustryAgency)
            .filter(IndustryAgency.market == MARKET_DOMESTIC, IndustryAgency.profile.isnot(None))
            .count()
        )
        fake = FakeLLM([LINK_OK] * n_industries)
        stats = generate_link_sentences(db, fake)
        assert stats["generated"] == n_industries  # 국내×bok — fed 지표는 없음
        assert stats["no_indicator"] == 2  # 국내×fed, 해외×fed

        # 같은 지표 버전 재실행 → 전량 캐시 히트, LLM 호출 0 (F-5.3.2)
        fake2 = FakeLLM([])
        stats2 = generate_link_sentences(db, fake2)
        assert stats2["cached"] == n_industries and stats2["generated"] == 0

        # 지표 갱신(버전 변경) → 새 키로 다시 생성
        _seed_indicator(db, value="3.00%", direction="인상")
        fake3 = FakeLLM([LINK_OK] * n_industries)
        stats3 = generate_link_sentences(db, fake3)
        assert stats3["generated"] == n_industries
        versions = {v for (v,) in db.query(RateLinkSentence.indicator_version).distinct()}
        assert "3.00%|인상" in versions


def test_link_sentence_stock_name_dropped(client):
    with SessionLocal() as db:
        seed_domestic_industries(db)
        _seed_indicator(db, value="9.99%")  # 캐시와 겹치지 않는 지표 버전
        n = (
            db.query(IndustryAgency)
            .filter(IndustryAgency.market == MARKET_DOMESTIC, IndustryAgency.profile.isnot(None))
            .count()
        )
        with_name = {"sentence": "가나전자 같은 종목은 금리 영향을 받는 경향이 있어요."}
        # 첫 업종만 종목명 위반(재생성도 위반 → 드롭), 나머지는 정상
        fake = FakeLLM([with_name, with_name] + [LINK_OK] * (n - 1))
        stats = generate_link_sentences(db, fake)
        assert stats["dropped"] == 1 and stats["generated"] == n - 1
        assert len(fake.calls) == 2 + (n - 1)  # 재생성 1회 시도 후 포기 (F-5.3.1)
