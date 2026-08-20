from datetime import datetime

from pydantic import BaseModel


class CardDetail(BaseModel):
    """이슈 #18 — 정형 공시 핵심 필드 배지 (예: 취득 예정 금액 / 7,174,299,854,900원)."""

    label: str
    value: str


class Card(BaseModel):
    """F-6.1.1 — 공통 카드 스키마. 다섯 탭 공통이며 없는 요소는 null로 채운다.

    프론트는 카드 컴포넌트 하나로 렌더링하고 슬롯을 켜고 끈다(F-6.1.2).
    card_id는 저장 API(F-7.1)가 받는 식별자 (확정사항 4절).
    """

    card_id: int
    label: str | None = None  # positive|neutral|negative — 공시·규제만
    label_reason: str | None = None
    title: str  # 원문 제목 그대로 (영문도 미번역, F-5.1.2)
    summary_short: str | None = None
    summary_full: str | None = None
    # #77 — 요약 속 어려운 단어(지식베이스 표제어). 프론트가 하이라이트, 탭 → /terms/explain
    hard_terms: list[str] | None = None
    source_name: str
    published_at: datetime | None = None
    origin_url: str | None = None  # 항상 담되 노출은 시트에서만 (F-6.6 확정)
    is_saved: bool = False
    thumbnail_url: str | None = None  # 유튜브만
    channel_name: str | None = None  # 유튜브만
    view_count: int | None = None  # 유튜브만
    indicator_value: str | None = None  # 금리 탭만
    details: list[CardDetail] | None = None  # 정형 공시만 (이슈 #18)
    link_sentence: str | None = None  # 금리 탭만 — 카드별 "내 종목엔" 문장
    # 공시만 (이슈 #58) — 서식 코드와 한글 서식명. SEC는 제목이 "8-K" 같은 서식 코드뿐이라
    # 초보자용 칩("8-K · 수시 보고서")에 쓴다. 제목 원문은 그대로 둔다(F-5.1.2).
    doc_type: str | None = None
    doc_type_name: str | None = None


class CardListResponse(BaseModel):
    tab: str
    market: str
    stock_code: str
    link_sentence: str | None = None  # 금리 탭만, 목록 위 1회 (F-6.2). 최신 카드 기준, 하위호환용
    disclaimer: bool = False  # 유튜브만 true — "참고용 · 개인 의견입니다" 고정 노출 근거 (F-6.3)
    reason: str | None = None  # 빈 목록 사유: no_data | fetch_failed (F-6.4)
    items: list[Card]


class MarketInfo(BaseModel):
    """F-2.1~2.3 — 구분별 화면 구성 정보."""

    market: str
    tabs: list[str]
    stock_count: int
    last_stock_code: str | None = None  # F-2.2 — 구분 전환 시 복귀 지점
    reason: str | None = None  # no_overseas_stock (F-2.3)


class MarketsResponse(BaseModel):
    markets: list[MarketInfo]
