"""E-7 — 경제 상식 카드 조회. 회전(E-5)이 만든 결과를 읽기만 한다.

조회 실패가 홈 나머지에 영향을 주지 않는다(E-8) — 여기서 예외를 던지지 않고
빈 결과를 돌려주는 쪽을 기본으로 한다. 라우터가 필요하면 AppError로 감싼다.
"""

from sqlalchemy.orm import Session

from app.models import EconCard, EconRotation


def current_rotation(db: Session) -> tuple[list[EconCard], object]:
    """E-7 — 최신 회전 세트. 회전이 한 번도 안 돌았으면 빈 목록 + rotated_at None."""
    last = db.query(EconRotation).order_by(EconRotation.rotated_at.desc()).first()
    if last is None or not last.card_ids:
        return [], None
    cards = db.query(EconCard).filter(EconCard.id.in_(last.card_ids)).all()
    by_id = {c.id: c for c in cards}
    ordered = [by_id[i] for i in last.card_ids if i in by_id]  # 회전 당시 순서 유지
    return ordered, last.rotated_at


def get_approved_card(db: Session, card_id: int) -> EconCard | None:
    """E-7 — 모달 상세. 노출 자격은 승인본뿐(E-5.6)이라 승인 아닌 카드는 없는 것과 같다."""
    card = db.get(EconCard, card_id)
    if card is None or card.status != "approved":
        return None
    return card
