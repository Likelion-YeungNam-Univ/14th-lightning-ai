"""F-4 — 수집 오케스트레이션. 배치(일 06:00)와 온디맨드(종목 추가 직후)가 공유한다.

수집 대상 = 세션에 등록된 **고유 종목**(중복 제거, F-4.5). 실패는 수집 단위별로
격리되어 있으므로 러너는 순서대로 부르기만 한다. 사용자 요청 경로에서 직접 부르지
말 것 — 온디맨드는 BackgroundTasks로만 태운다(불변식 1).
"""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.collectors.briefing import sync_regulations
from app.collectors.dart import sync_disclosures
from app.collectors.ecos import sync_bok_rate
from app.collectors.fred import sync_fed_rate
from app.collectors.youtube import sync_youtube
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, SessionStock, SourceItem, StockMaster

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90  # 보관 기간 (F-4.8) — 초과분 삭제, 저장 카드는 SET NULL로 생존


def registered_stocks(db: Session) -> list[StockMaster]:
    """세션들에 등록된 고유 종목. 시연 리셋 후에는 기본 제공 종목만 남는다."""
    codes = {code for (code,) in db.query(SessionStock.stock_code).distinct().all()}
    if not codes:
        return []
    return db.query(StockMaster).filter(StockMaster.stock_code.in_(codes)).all()


def collect_for_stocks(db: Session, stocks: list[StockMaster]) -> dict:
    """종목 종속 수집(공시·유튜브). 온디맨드 훅이 새 종목 1개로 부른다."""
    domestic = [s for s in stocks if s.market == MARKET_DOMESTIC]
    return {
        "disclosure": sync_disclosures(db, domestic),  # 해외 공시(SEC)는 이슈 #6
        "youtube": sync_youtube(db, stocks),
    }


def collect_all(db: Session) -> dict:
    """일 배치 전체 — 종목 종속(공시·유튜브) + 전역(규제·금리) + 보관 기간 정리."""
    stocks = registered_stocks(db)
    result = collect_for_stocks(db, stocks)
    result["regulation"] = sync_regulations(db)
    result["bok"] = sync_bok_rate(db)
    result["fed"] = sync_fed_rate(db)
    result["purged"] = purge_old_items(db)
    logger.info("collect_all: %s", result)
    return result


def purge_old_items(db: Session) -> int:
    """90일 초과 원자료 삭제. saved_card.source_item_id는 SET NULL — 저장 카드는 산다(F-7.3).

    금리 탭은 제외 — 결정문은 소수·수동 시드이며 최신 지표를 싣고 있어서 지우면 탭이 빈다.
    """
    cutoff = utcnow() - timedelta(days=RETENTION_DAYS)
    old = (
        db.query(SourceItem)
        .filter(SourceItem.published_at < cutoff, SourceItem.tab.notin_(["bok", "fed"]))
        .all()
    )
    for item in old:  # ORM 삭제 — sqlite에서도 FK 연쇄(SET NULL/CASCADE)가 동작하도록
        db.delete(item)
    db.commit()
    return len(old)


def collect_on_stock_added(stock_code: str) -> None:
    """BackgroundTasks 진입점 — 요청 세션과 분리된 자체 DB 세션을 쓴다."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        stock = db.get(StockMaster, stock_code)
        if stock is None:
            return
        stats = collect_for_stocks(db, [stock])
        logger.info("on-demand collect %s: %s", stock_code, stats)
        try:  # 새 종목 공시 요약까지 온디맨드로 (F-5) — 실패해도 수집분은 유효
            from app.ai.generate import generate_for_stock

            logger.info("on-demand generate %s: %s", stock_code, generate_for_stock(db, stock_code))
        except Exception as e:
            db.rollback()
            logger.warning("온디맨드 생성 실패 %s: %s", stock_code, e)
