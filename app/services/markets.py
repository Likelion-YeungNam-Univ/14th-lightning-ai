"""F-2 — 국내/해외 구분 도메인.

탭 구성을 백엔드가 내려준다(F-2.1) — 프론트가 구분 값으로 탭을 추론하게 하지 않는다.
"""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    MARKET_DOMESTIC,
    MARKET_OVERSEAS,
    SessionStock,
    StockMaster,
    UserSession,
)

# F-2.1 확정 — regulation은 양쪽에 있고 출처만 다르다(정책브리핑/Federal Register)
TABS_BY_MARKET: dict[str, list[str]] = {
    MARKET_DOMESTIC: ["youtube", "disclosure", "regulation", "bok", "fed"],
    MARKET_OVERSEAS: ["youtube", "disclosure", "regulation", "fed"],
}

RATE_TABS = ("bok", "fed")  # 금리 탭 — link_sentence 대상 (F-6.2)


def validate_combination(market: str, tab: str) -> None:
    """F-2.4 — 화면에 없는 조합도 북마크·뒤로 가기로 요청된다. 빈 목록 대신 명시적 에러."""
    if tab not in TABS_BY_MARKET.get(market, []):
        raise AppError(
            "invalid_combination",
            "해당 구분에서 지원하지 않는 탭입니다",
            400,
            {"market": market, "tab": tab},
        )


def _registered_by_market(db: Session, session: UserSession, market: str) -> list[str]:
    rows = (
        db.query(SessionStock.stock_code)
        .join(StockMaster, SessionStock.stock_code == StockMaster.stock_code)
        .filter(SessionStock.session_id == session.id, StockMaster.market == market)
        .order_by(SessionStock.display_order)
        .all()
    )
    return [code for (code,) in rows]


def last_stock_of(session: UserSession, market: str) -> str | None:
    return session.last_stock_domestic if market == MARKET_DOMESTIC else session.last_stock_overseas


def set_last_stock(session: UserSession, market: str, stock_code: str) -> None:
    if market == MARKET_DOMESTIC:
        session.last_stock_domestic = stock_code
    else:
        session.last_stock_overseas = stock_code


def markets_overview(db: Session, session: UserSession | None) -> list[dict]:
    """F-2.1·2.2·2.3 — 구분별 탭 구성 + 마지막 본 종목 + 종목 유무. 세션 없어도 동작."""
    result = []
    for market, tabs in TABS_BY_MARKET.items():
        codes = _registered_by_market(db, session, market) if session else []
        last = last_stock_of(session, market) if session else None
        result.append(
            {
                "market": market,
                "tabs": tabs,
                "stock_count": len(codes),
                # 등록 목록에서 빠진 종목은 복귀 지점으로 쓰지 않는다
                "last_stock_code": last if last in codes else None,
                # F-2.3 — 해외 구분은 항상 활성이되, 종목 없음을 사유로 알린다
                "reason": (
                    "no_overseas_stock" if market == MARKET_OVERSEAS and not codes else None
                ),
            }
        )
    return result
