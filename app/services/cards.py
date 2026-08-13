"""F-6 — 카드 목록 조회.

이슈 #3 시점에는 수집기(#4)·AI 가공(#5)이 없으므로 카드 아이템은 **탭별 규칙을 지키는
고정 목 데이터**다 — 프론트가 실제 스키마로 개발을 시작하는 것이 목적.
#5에서 이 파일의 목 생성부를 DB(source_item + generated_content) 조회로 교체한다.
조합 검증·last_stock 갱신·is_saved 규칙은 실제 구현이며 교체 대상이 아니다.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import SessionStock, StockMaster, UserSession
from app.services.markets import RATE_TABS, set_last_stock, validate_combination


def list_cards(db: Session, session: UserSession | None, tab: str, stock_code: str) -> dict:
    """F-6.1 — 탭 × 종목 조합의 카드 반환. 구분은 파라미터로 받지 않는다(종목이 결정)."""
    stock = db.get(StockMaster, stock_code)
    if stock is None:
        raise AppError("unknown_stock", "존재하지 않는 종목코드입니다", 404)
    validate_combination(stock.market, tab)  # F-2.4

    # F-2.2 — 등록된 종목을 조회했을 때만 구분별 복귀 지점 갱신
    if session is not None:
        registered = db.get(SessionStock, (session.id, stock_code))
        if registered is not None:
            set_last_stock(session, stock.market, stock_code)
            db.commit()

    items = _mock_items(tab, stock)
    return {
        "tab": tab,
        "market": stock.market,
        "stock_code": stock_code,
        "link_sentence": _mock_link_sentence(tab) if tab in RATE_TABS else None,
        "disclaimer": tab == "youtube",
        "reason": None if items else "no_data",
        "items": items,
    }


# ---------- 아래는 #5에서 DB 조회로 교체되는 목 데이터 ----------

_MOCK_DATE = datetime(2026, 8, 10, 9, 0, 0)


def _mock_link_sentence(tab: str) -> str:
    base = "기준금리" if tab == "bok" else "미국 기준금리"
    return f"[목데이터] {base} 동결이 이어지면 이 업종의 자금 조달 부담이 완만해지는 쪽으로 봐요"


def _mock_items(tab: str, stock: StockMaster) -> list[dict]:
    name = stock.name
    common = {"published_at": _MOCK_DATE, "is_saved": False}
    if tab == "youtube":
        return [
            {
                "card_id": 900001,
                "title": f"[목데이터] {name} 지금 사도 될까? 전문가 분석",
                "source_name": "YouTube",
                "origin_url": "https://www.youtube.com/watch?v=mock1",
                "thumbnail_url": "https://i.ytimg.com/vi/mock1/hqdefault.jpg",
                "channel_name": "주식읽어주는남자",
                "view_count": 152_000,
                **common,
            },
            {
                "card_id": 900002,
                "title": f"[목데이터] {name} 실적 발표 총정리",
                "source_name": "YouTube",
                "origin_url": "https://www.youtube.com/watch?v=mock2",
                "thumbnail_url": "https://i.ytimg.com/vi/mock2/hqdefault.jpg",
                "channel_name": "경제한입",
                "view_count": 98_000,
                **common,
            },
        ]
    if tab == "disclosure":
        return [
            {
                "card_id": 900011,
                "label": "positive",
                "label_reason": "[목데이터] 생산능력 확대는 중장기 공급 요인이라 긍정으로 봤어요.",
                "title": "유형자산 취득 결정" if stock.market == "domestic" else "Form 8-K",
                "summary_short": f"[목데이터] {name}이(가) 생산 설비에 대규모 투자를 결정했어요.",
                "summary_full": (
                    f"[목데이터] {name}이(가) 생산 설비에 대규모 투자를 결정했어요. "
                    "생산능력이 늘어나는 방향입니다. 상세 조건은 원문에서 확인 가능해요."
                ),
                "source_name": "DART" if stock.market == "domestic" else "SEC EDGAR",
                "origin_url": "https://example.com/mock-disclosure",
                **common,
            }
        ]
    if tab == "regulation":
        return [
            {
                "card_id": 900021,
                "label": "neutral",
                "label_reason": "[목데이터] 지원과 규제가 함께 담겨 있어 중립으로 봤어요.",
                "title": "[목데이터] 첨단산업 경쟁력 강화 방안 발표",
                "summary_short": "[목데이터] 정부가 이 업종에 대한 지원 방안을 발표했어요.",
                "summary_full": (
                    "[목데이터] 정부가 이 업종에 대한 지원 방안을 발표했어요. "
                    "세부 시행 시기는 원문에서 확인할 수 있어요."
                ),
                "source_name": "정책브리핑" if stock.market == "domestic" else "Federal Register",
                "origin_url": "https://example.com/mock-regulation",
                **common,
            }
        ]
    if tab in RATE_TABS:
        is_bok = tab == "bok"
        return [
            {
                "card_id": 900031 if is_bok else 900041,
                "title": "[목데이터] 통화정책방향 결정" if is_bok else "[목데이터] FOMC Statement",
                "summary_short": "[목데이터] 물가 안정세를 근거로 기준금리를 동결했어요.",
                "summary_full": (
                    "[목데이터] 금통위는 물가 안정세를 근거로 기준금리 동결을 결정했어요."
                    if is_bok
                    else "[목데이터] 연준은 고용과 물가를 근거로 기준금리 동결을 결정했어요."
                ),
                "source_name": "한국은행" if is_bok else "Fed",
                "origin_url": "https://example.com/mock-rate",
                "indicator_value": "2.50%" if is_bok else "4.25%",
                **common,
            }
        ]
    return []
