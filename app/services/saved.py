"""F-7 — 카드 저장. 저장본은 snapshot_json만으로 렌더한다(F-7.3).

- 스냅샷: 저장 시점의 제목·요약·라벨·출처·날짜·정형 배지 복사. 원자료·생성물이
  갱신돼도 저장본은 불변. **유튜브는 제목·링크만**(YouTube 30일 보관 정책, 확정사항 4절).
- 저장은 구분에 묶이지 않는다(F-7.4) — 목록은 국내·해외 통합.
- card_id = source_item.id (확정사항 4절). 원자료가 정리(purge)되면 FK만 NULL이 되고
  저장 카드는 스냅샷으로 계속 렌더된다.
"""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import SavedCard, SourceItem, StockMaster, UserSession
from app.services.cards import _build_card


def _build_snapshot(db: Session, item: SourceItem, stock: StockMaster) -> dict:
    if item.tab == "youtube":  # 최소 스냅샷 — 30일 뒤 게시 불가 데이터를 남기지 않는다
        return {"title": item.title, "origin_url": item.origin_url, "source_name": "YouTube"}
    card = _build_card(db, item.tab, stock, item)
    return {
        "title": card["title"],
        "summary_short": card["summary_short"],
        "summary_full": card["summary_full"],
        "label": card["label"],
        "label_reason": card["label_reason"],
        "source_name": card["source_name"],
        "published_at": card["published_at"].isoformat() if card["published_at"] else None,
        "origin_url": card["origin_url"],
        "indicator_value": card["indicator_value"],
        "details": card["details"],
    }


def save_card(
    db: Session, session: UserSession, card_id: int, stock_code: str
) -> tuple[SavedCard, bool]:
    """F-7.1 — 저장(멱등). 반환: (행, 이미 저장돼 있었는지)."""
    item = db.get(SourceItem, card_id)
    if item is None:
        raise AppError("unknown_card", "존재하지 않는 카드입니다", 404)
    stock = db.get(StockMaster, stock_code)
    if stock is None:
        raise AppError("unknown_stock", "존재하지 않는 종목코드입니다", 400)

    existing = (
        db.query(SavedCard)
        .filter(SavedCard.session_id == session.id, SavedCard.source_item_id == card_id)
        .one_or_none()
    )
    if existing is not None:  # 중복 저장은 멱등 (F-7.1) — 스냅샷도 처음 것을 유지
        return existing, True

    saved = SavedCard(
        session_id=session.id,
        source_item_id=card_id,
        tab=item.tab,  # 저장 당시 탭 배지 (F-7.5)
        stock_code=stock_code,  # 저장 당시 종목 배지
        snapshot_json=_build_snapshot(db, item, stock),
    )
    db.add(saved)
    db.commit()
    return saved, False


def unsave_card(db: Session, session: UserSession, card_id: int) -> bool:
    """F-7.2 — 해제(멱등). 스냅샷 행 자체를 지운다. 반환: 실제로 지웠는지."""
    removed = (
        db.query(SavedCard)
        .filter(SavedCard.session_id == session.id, SavedCard.source_item_id == card_id)
        .delete()
    )
    db.commit()
    return removed > 0


def list_saved(
    db: Session, session: UserSession, stock_code: str | None = None
) -> list[tuple[SavedCard, str | None]]:
    """F-7.4·F-7.5 — 구분 통합, 최근 저장 순, 종목 필터. 반환: (행, 종목명)."""
    query = (
        db.query(SavedCard, StockMaster.name)
        .outerjoin(StockMaster, StockMaster.stock_code == SavedCard.stock_code)
        .filter(SavedCard.session_id == session.id)
    )
    if stock_code:
        query = query.filter(SavedCard.stock_code == stock_code)
    return query.order_by(SavedCard.saved_at.desc(), SavedCard.id.desc()).all()
