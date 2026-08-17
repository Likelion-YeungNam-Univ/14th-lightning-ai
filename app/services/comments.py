"""C-7 — 베팅방 댓글. 자료 카드 첨부는 저장 카드의 스냅샷을 그대로 참조한다(C-5.1.3)."""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import BettingEntry, BettingRoom, RoomComment, SavedCard, UserSession

BODY_MAX_LEN = 300  # C-7.1


def _side_for(db: Session, room_id: int, session_id: str) -> str | None:
    entry = (
        db.query(BettingEntry)
        .filter(BettingEntry.room_id == room_id, BettingEntry.session_id == session_id)
        .one_or_none()
    )
    return entry.side if entry else None  # None → 프론트가 [미참여] 배지(C-7.1.1)


def _to_item(db: Session, comment: RoomComment) -> dict:
    snapshot = None
    if comment.saved_card_id is not None:
        card = db.get(SavedCard, comment.saved_card_id)
        snapshot = card.snapshot_json if card is not None else None
    return {
        "id": comment.id,
        "session_id": comment.session_id,
        "side": _side_for(db, comment.room_id, comment.session_id),
        "body": None if comment.deleted_at else comment.body,
        "deleted": comment.deleted_at is not None,
        "saved_card_snapshot": snapshot,
        "created_at": comment.created_at,
    }


def list_comments(db: Session, room_id: int) -> list[dict]:
    """C-7.1.2 — 최신순 고정."""
    if db.get(BettingRoom, room_id) is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)
    comments = (
        db.query(RoomComment)
        .filter(RoomComment.room_id == room_id)
        .order_by(RoomComment.created_at.desc(), RoomComment.id.desc())
        .all()
    )
    return [_to_item(db, c) for c in comments]


def create_comment(
    db: Session, session: UserSession, room_id: int, *, body: str, saved_card_id: int | None
) -> dict:
    if db.get(BettingRoom, room_id) is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)

    if saved_card_id is not None:  # C-5.1.2 — 본인이 저장한 카드만 첨부 가능
        card = db.get(SavedCard, saved_card_id)
        if card is None or card.session_id != session.id:
            raise AppError("unknown_saved_card", "존재하지 않는 저장 카드입니다", 400)

    comment = RoomComment(
        room_id=room_id, session_id=session.id, body=body, saved_card_id=saved_card_id
    )
    db.add(comment)
    db.commit()
    return _to_item(db, comment)


def delete_comment(db: Session, session: UserSession, comment_id: int) -> bool:
    """C-7.3 — 본인만 삭제(멱등). 반환: 실제로 지웠는지."""
    from app.deps import utcnow

    comment = db.get(RoomComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        return False
    if comment.session_id != session.id:
        raise AppError("not_comment_owner", "본인 댓글만 삭제할 수 있습니다", 403)
    comment.deleted_at = utcnow()
    db.commit()
    return True
