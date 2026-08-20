"""C-7 — 베팅방 댓글. 자료 카드 첨부는 저장 카드의 스냅샷을 복사해 붙인다(C-5.1.3).

이슈 #80 — 댓글 좋아요(L). 식별자는 명세의 user_id 대신 이 프로젝트의 기존 관례를 따라
session_id(로그인 시 주인 세션)를 쓴다(확정사항 21절)."""

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import utcnow
from app.errors import AppError
from app.models import BettingEntry, BettingRoom, CommentLike, RoomComment, SavedCard, UserSession

BODY_MAX_LEN = 300  # C-7.1


def _author_tag(session_id: str) -> str:
    """session_id를 응답에 직접 싣지 않기 위한 익명 태그(승래 리뷰 B-1).

    session_id는 곧 로그인 쿠키 값이라 그대로 노출하면 세션 탈취 경로가 된다 —
    HMAC 계열 해시라 되돌릴 수 없다. 전용 시크릿이 없으면 ADMIN_TOKEN으로 폴백한다.
    """
    secret = settings.session_tag_secret or settings.admin_token or "assit-dev-fallback"
    return hashlib.sha256(f"{secret}:{session_id}".encode()).hexdigest()[:6]


def _side_for(db: Session, room_id: int, session_id: str) -> str | None:
    entry = (
        db.query(BettingEntry)
        .filter(BettingEntry.room_id == room_id, BettingEntry.session_id == session_id)
        .one_or_none()
    )
    return entry.side if entry else None  # None → 프론트가 [미참여] 배지(C-7.1.1)


def _liked_by(db: Session, comment_id: int, viewer_session_id: str | None) -> bool:
    if viewer_session_id is None:
        return False
    return (
        db.query(CommentLike)
        .filter(CommentLike.comment_id == comment_id, CommentLike.session_id == viewer_session_id)
        .first()
        is not None
    )


def _to_item(db: Session, comment: RoomComment, viewer_session_id: str | None) -> dict:
    deleted = comment.deleted_at is not None
    return {
        "id": comment.id,
        "author_tag": _author_tag(comment.session_id),
        "is_mine": viewer_session_id is not None and viewer_session_id == comment.session_id,
        "side": _side_for(db, comment.room_id, comment.session_id),
        "body": None if deleted else comment.body,
        "deleted": deleted,
        "saved_card_snapshot": None if deleted else comment.snapshot_json,  # B-4
        "created_at": comment.created_at,
        "like_count": comment.like_count,
        "liked_by_me": _liked_by(db, comment.id, viewer_session_id),
    }


def list_comments(
    db: Session, room_id: int, viewer_session_id: str | None = None, sort: str = "likes"
) -> list[dict]:
    """C-7.1.2 + 이슈 #80(L-3) — 기본 좋아요순(동점 최신순), sort=recent면 최신순.

    무인증 조회 가능(C-1.4) — viewer_session_id는 있으면만 쓴다.
    """
    if db.get(BettingRoom, room_id) is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)
    query = db.query(RoomComment).filter(RoomComment.room_id == room_id)
    if sort == "recent":
        query = query.order_by(RoomComment.created_at.desc(), RoomComment.id.desc())
    else:
        query = query.order_by(
            RoomComment.like_count.desc(), RoomComment.created_at.desc(), RoomComment.id.desc()
        )
    return [_to_item(db, c, viewer_session_id) for c in query.all()]


def create_comment(
    db: Session, session: UserSession, room_id: int, *, body: str, saved_card_id: int | None
) -> dict:
    if db.get(BettingRoom, room_id) is None:
        raise AppError("unknown_room", "존재하지 않는 베팅방입니다", 404)

    snapshot = None
    if saved_card_id is not None:  # C-5.1.2 — 본인이 저장한 카드만 첨부 가능
        card = db.get(SavedCard, saved_card_id)
        if card is None or card.session_id != session.id:
            raise AppError("unknown_saved_card", "존재하지 않는 저장 카드입니다", 400)
        snapshot = card.snapshot_json  # 첨부 시점 복사(B-3) — 저장 해제돼도 안 바뀐다

    comment = RoomComment(
        room_id=room_id,
        session_id=session.id,
        body=body,
        saved_card_id=saved_card_id,
        snapshot_json=snapshot,
    )
    db.add(comment)
    db.commit()
    return _to_item(db, comment, session.id)


def delete_comment(db: Session, session: UserSession, comment_id: int) -> bool:
    """C-7.3 — 본인만 삭제(멱등). 반환: 실제로 지웠는지."""
    comment = db.get(RoomComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        return False
    if comment.session_id != session.id:
        raise AppError("not_comment_owner", "본인 댓글만 삭제할 수 있습니다", 403)
    comment.deleted_at = utcnow()
    db.commit()
    return True


def _get_likeable_comment(db: Session, comment_id: int) -> RoomComment:
    comment = db.get(RoomComment, comment_id)
    if comment is None:
        raise AppError("unknown_comment", "존재하지 않는 댓글입니다", 404)
    if comment.deleted_at is not None:
        raise AppError("comment_deleted", "이미 지워진 댓글이에요", 404)
    return comment


def like_comment(db: Session, session: UserSession, comment_id: int) -> dict:
    """이슈 #80(L-2) — 자기 댓글 차단(L-2.4), 중복은 에러 아니라 현재 상태 반환(L-6)."""
    comment = _get_likeable_comment(db, comment_id)
    if comment.session_id == session.id:
        raise AppError("self_like_blocked", "내 댓글에는 좋아요를 남길 수 없어요", 400)

    already = (
        db.query(CommentLike)
        .filter(CommentLike.comment_id == comment_id, CommentLike.session_id == session.id)
        .first()
    )
    if already is not None:
        return {"liked": True, "like_count": comment.like_count}

    db.add(CommentLike(comment_id=comment_id, session_id=session.id))
    comment.like_count += 1
    try:
        db.commit()
    except IntegrityError:  # 동시 요청이 유니크 제약(L-4.2)에 겹침 — 이긴 쪽 결과를 그대로 쓴다
        db.rollback()
        comment = _get_likeable_comment(db, comment_id)
    return {"liked": True, "like_count": comment.like_count}


def unlike_comment(db: Session, session: UserSession, comment_id: int) -> dict:
    """이슈 #80(L-2.2) — 이미 취소된 상태에서 다시 불러도 에러 아니라 현재 상태 반환."""
    comment = _get_likeable_comment(db, comment_id)
    deleted = (
        db.query(CommentLike)
        .filter(CommentLike.comment_id == comment_id, CommentLike.session_id == session.id)
        .delete()
    )
    if deleted:
        comment.like_count = max(0, comment.like_count - 1)
        db.commit()
    return {"liked": False, "like_count": comment.like_count}
