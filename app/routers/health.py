from fastapi import APIRouter
from sqlalchemy import func, text

from app.deps import DbDep
from app.models import MARKET_DOMESTIC, StockMaster

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DbDep) -> dict:
    """DB 연결 + 데이터 적재 상태. `seeded: false`면 종목 마스터가 비어 있어
    기본 종목·검색이 빈 결과를 낸다 — 시드 복원(scripts/restore_seed.sh) 필요."""
    db.execute(text("SELECT 1"))
    domestic_stocks = (
        db.query(func.count(StockMaster.stock_code))
        .filter(StockMaster.market == MARKET_DOMESTIC)
        .scalar()
        or 0
    )
    return {
        "status": "ok",
        "db": "ok",
        "seeded": domestic_stocks > 0,
        "domestic_stocks": domestic_stocks,
        "hint": None
        if domestic_stocks
        else "종목 마스터 비어 있음 — bash scripts/restore_seed.sh 실행",
    }
