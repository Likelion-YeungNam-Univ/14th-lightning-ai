"""종목 도메인 로직."""

from sqlalchemy.orm import Session

from app.models import MARKET_DOMESTIC, StockMaster

DEFAULT_STOCK_COUNT = 4  # F-3.8


def pick_default_stocks(db: Session) -> list[StockMaster]:
    """F-3.8 — 국내 시가총액 상위 4개.

    우선주 제외: 국내 종목코드 끝자리가 '0'이 아니면 우선주(예: 삼성전자우 005935).
    시총 상위에 삼성전자우가 끼는 것을 막는다 (확정사항 4절).
    """
    return (
        db.query(StockMaster)
        .filter(
            StockMaster.market == MARKET_DOMESTIC,
            StockMaster.stock_code.like("%0"),
            StockMaster.market_cap.isnot(None),
        )
        .order_by(StockMaster.market_cap.desc())
        .limit(DEFAULT_STOCK_COUNT)
        .all()
    )
