"""종목 도메인 로직 (F-3.3~3.8)."""

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    MARKET_DOMESTIC,
    MARKET_OVERSEAS,
    SessionStock,
    StockMaster,
    UserSession,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

DEFAULT_STOCK_COUNT = 4  # F-3.8
# 해외는 시가총액 미수집이라 F-3.8의 "시총 상위 4"를 그대로 못 쓴다 — 인지도 기준
# 고정 4종으로 확정(명세확정사항 §12, QA 리포트 반영). 화이트리스트 안에서만 고른다
OVERSEAS_DEFAULT_CODES = ["NVDA", "AAPL", "GOOGL", "MSFT"]
POPULAR_STOCK_COUNT = 8  # 확정사항 7절
STOCK_LIMIT_PER_MARKET = 10  # F-3.5 — v3 갱신(2026-08-16): 30 → 10, 구분별
MIN_QUERY_LEN_NAME = 2  # 확정: 이름 2자, 코드(숫자 시작) 1자
SEARCH_RESULT_LIMIT = 20


def validate_market(market: str) -> str:
    if market not in (MARKET_DOMESTIC, MARKET_OVERSEAS):
        raise AppError("invalid_market", "market은 domestic 또는 overseas여야 합니다", 400)
    return market


@lru_cache(maxsize=1)
def _unsupported_overseas_aliases() -> list[dict]:
    with open(DATA_DIR / "unsupported_overseas.json", encoding="utf-8") as f:
        return json.load(f)["entries"]


def _matches_unsupported_overseas(query: str) -> bool:
    q = query.lower().strip()
    for entry in _unsupported_overseas_aliases():
        for alias in entry["aliases"]:
            if q == alias or (len(q) >= 2 and alias.startswith(q)):
                return True
    return False


def registered_code_set(db: Session, session: UserSession | None) -> set[str]:
    if session is None:
        return set()
    rows = db.query(SessionStock.stock_code).filter(SessionStock.session_id == session.id).all()
    return {code for (code,) in rows}


def search_stocks(
    db: Session, query: str, market: str, session: UserSession | None
) -> tuple[list[StockMaster], str | None, set[str]]:
    """F-3.3 — 현재 구분 한정 검색. 반환: (결과, 빈 결과 사유, 등록된 코드 집합).

    초성 검색 미지원(확정). 최소 입력: 코드(숫자 시작) 1자 / 이름 2자 — 미달은 빈 결과.
    """
    validate_market(market)
    q = query.strip()
    min_len = 1 if q[:1].isdigit() else MIN_QUERY_LEN_NAME
    if len(q) < min_len:
        return [], None, registered_code_set(db, session)

    if market == MARKET_DOMESTIC:
        results = (
            db.query(StockMaster)
            .filter(
                StockMaster.market == MARKET_DOMESTIC,
                StockMaster.name.ilike(f"%{q}%") | StockMaster.stock_code.like(f"{q}%"),
            )
            .order_by(StockMaster.market_cap.desc().nullslast())
            .limit(SEARCH_RESULT_LIMIT)
            .all()
        )
    else:
        # 해외는 화이트리스트(≤30건)라 전량 로드 후 별칭까지 파이썬에서 매칭
        q_lower = q.lower()
        rows = db.query(StockMaster).filter(StockMaster.market == MARKET_OVERSEAS).all()
        results = [
            s
            for s in rows
            if q_lower in s.name.lower()
            or s.stock_code.lower().startswith(q_lower)
            or any(q_lower in alias.lower() for alias in (s.aliases or []))
        ][:SEARCH_RESULT_LIMIT]

    reason = None
    if not results and _matches_unsupported_overseas(q):
        reason = "unsupported_overseas"  # F-3.3.1 — 오타가 아니라 미지원 종목
    return results, reason, registered_code_set(db, session)


def popular_stocks(db: Session, market: str) -> list[StockMaster]:
    """확정사항 7절 — 종목 추가 창의 추천 칩. 국내: 시총 상위 8(보통주), 해외: 화이트리스트 앞 8."""
    validate_market(market)
    query = db.query(StockMaster).filter(StockMaster.market == market)
    if market == MARKET_DOMESTIC:
        query = query.filter(
            StockMaster.stock_code.like("%0"), StockMaster.market_cap.isnot(None)
        ).order_by(StockMaster.market_cap.desc())
    else:
        query = query.order_by(StockMaster.stock_code)
    return query.limit(POPULAR_STOCK_COUNT).all()


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


def pick_default_overseas_stocks(db: Session) -> list[StockMaster]:
    """비로그인 사용자도 해외 탭 진입 시 바로 볼 수 있게 고정 4종을 준다(F-2.3 보완).

    시총 데이터가 없어 F-3.8과 같은 방식은 못 쓴다 — OVERSEAS_DEFAULT_CODES 순서 유지.
    """
    rows = {
        s.stock_code: s
        for s in db.query(StockMaster).filter(
            StockMaster.market == MARKET_OVERSEAS,
            StockMaster.stock_code.in_(OVERSEAS_DEFAULT_CODES),
        )
    }
    return [rows[code] for code in OVERSEAS_DEFAULT_CODES if code in rows]


def list_my_stocks(db: Session, session: UserSession, market: str) -> list[tuple]:
    """F-3.4 — 구분별 종목 목록 (display_order 포함 전량). 반환: (SessionStock, StockMaster) 쌍."""
    validate_market(market)
    return (
        db.query(SessionStock, StockMaster)
        .join(StockMaster, SessionStock.stock_code == StockMaster.stock_code)
        .filter(SessionStock.session_id == session.id, StockMaster.market == market)
        .order_by(SessionStock.display_order)
        .all()
    )


def add_stocks(
    db: Session, session: UserSession, stock_codes: list[str]
) -> tuple[list[StockMaster], list[str]]:
    """F-3.5 — 벌크 등록(멱등). 반환: (신규 등록 종목, 이미 등록돼 무시된 코드)."""
    codes = list(dict.fromkeys(stock_codes))  # 요청 내 중복 제거(순서 유지)
    masters = {
        s.stock_code: s
        for s in db.query(StockMaster).filter(StockMaster.stock_code.in_(codes)).all()
    }
    unknown = [c for c in codes if c not in masters]
    if unknown:
        raise AppError(
            "unknown_stock", "존재하지 않는 종목코드가 있습니다", 400, {"codes": unknown}
        )

    registered = registered_code_set(db, session)
    to_add = [masters[c] for c in codes if c not in registered]
    skipped = [c for c in codes if c in registered]

    # 구분별 상한 10 (F-3.5, v3 갱신) — 추가 후 기준으로 검사, 초과 시 남은 자리 수 반환
    for market in (MARKET_DOMESTIC, MARKET_OVERSEAS):
        current = (
            db.query(func.count())
            .select_from(SessionStock)
            .join(StockMaster, SessionStock.stock_code == StockMaster.stock_code)
            .filter(SessionStock.session_id == session.id, StockMaster.market == market)
            .scalar()
        )
        incoming = sum(1 for s in to_add if s.market == market)
        if current + incoming > STOCK_LIMIT_PER_MARKET:
            raise AppError(
                "stock_limit_exceeded",
                f"구분별 등록 상한({STOCK_LIMIT_PER_MARKET}개)을 초과합니다",
                400,
                {
                    "market": market,
                    "limit": STOCK_LIMIT_PER_MARKET,
                    "current": current,
                    "incoming": incoming,
                    "remaining": max(STOCK_LIMIT_PER_MARKET - current, 0),  # 남은 자리 수 (F-3.5)
                },
            )

    next_order = (
        db.query(func.coalesce(func.max(SessionStock.display_order), -1))
        .filter(SessionStock.session_id == session.id)
        .scalar()
        + 1
    )
    for offset, stock in enumerate(to_add):
        db.add(
            SessionStock(
                session_id=session.id,
                stock_code=stock.stock_code,
                display_order=next_order + offset,
                is_default=False,
            )
        )
    db.commit()
    return to_add, skipped


def remove_stock(db: Session, session: UserSession, stock_code: str) -> int:
    """F-3.6 — 종목 삭제. 저장 카드는 유지(스냅샷으로 표시). 반환: 해당 구분의 남은 개수."""
    row = db.get(SessionStock, (session.id, stock_code))
    if row is None:
        raise AppError("not_registered", "등록되지 않은 종목입니다", 404)
    master = db.get(StockMaster, stock_code)
    db.delete(row)
    db.commit()
    remaining = (
        db.query(func.count())
        .select_from(SessionStock)
        .join(StockMaster, SessionStock.stock_code == StockMaster.stock_code)
        .filter(SessionStock.session_id == session.id, StockMaster.market == master.market)
        .scalar()
    )
    return remaining


def reorder_stocks(
    db: Session, session: UserSession, market: str, stock_codes: list[str]
) -> list[str]:
    """F-3.7 — 노출 순서 변경. 구분 단위 적용, 해당 구분 전체 목록과 정확히 일치해야 한다."""
    validate_market(market)
    pairs = list_my_stocks(db, session, market)
    current_codes = {ss.stock_code for ss, _ in pairs}
    if set(stock_codes) != current_codes or len(stock_codes) != len(current_codes):
        raise AppError(
            "invalid_order_payload",
            "해당 구분의 등록 종목 전체를 순서대로 보내야 합니다",
            400,
            {"expected": sorted(current_codes)},
        )
    by_code = {ss.stock_code: ss for ss, _ in pairs}
    for order, code in enumerate(stock_codes):
        by_code[code].display_order = order
    db.commit()
    return stock_codes
