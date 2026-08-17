"""C-7 — 베팅방 댓글 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field


class CommentItem(BaseModel):
    id: int
    session_id: str  # 닉네임 대용 — 프론트에서 배지·마스킹 처리
    side: str | None  # up|down — 베팅 안 했으면 None([미참여] 배지, C-7.1.1)
    body: str | None  # 삭제된 댓글은 None(플레이스홀더는 프론트가 표시, C-7.3)
    deleted: bool
    saved_card_snapshot: dict | None  # 첨부한 저장 카드의 스냅샷(C-5.1.3)
    created_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentItem]


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=300)  # C-7.1
    saved_card_id: int | None = None  # C-5.1.2 — 단일 선택


class CommentCreateResponse(BaseModel):
    item: CommentItem


class CommentDeleteResponse(BaseModel):
    removed: bool  # 멱등 — 이미 없었거나 이미 삭제됐으면 false
