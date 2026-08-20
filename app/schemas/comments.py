"""C-7 — 베팅방 댓글 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CommentItem(BaseModel):
    id: int
    author_tag: str  # 세션ID를 직접 노출하지 않는 서버측 익명 태그(승래 리뷰 B-1)
    is_mine: bool  # 조회자 본인 댓글인지(로그인 세션 기준)
    side: str | None  # up|down — 베팅 안 했으면 None([미참여] 배지, C-7.1.1)
    body: str | None  # 삭제된 댓글은 None(플레이스홀더는 프론트가 표시, C-7.3)
    deleted: bool
    saved_card_snapshot: dict | None  # 첨부 시점 스냅샷 복사본(C-5.1.3) — 삭제된 댓글은 None
    created_at: datetime
    like_count: int  # 이슈 #80(L-1.2)
    liked_by_me: bool  # 조회자가 이미 눌렀는지(L-1.4) — 비로그인/미조회 세션이면 항상 False


class CommentListResponse(BaseModel):
    items: list[CommentItem]


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=300)  # C-7.1
    saved_card_id: int | None = None  # C-5.1.2 — 단일 선택

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:  # 승래 리뷰 권고 — 공백만으로는 통과시키지 않는다
            raise ValueError("본문은 공백만으로 채울 수 없습니다")
        return stripped


class CommentCreateResponse(BaseModel):
    item: CommentItem


class CommentDeleteResponse(BaseModel):
    removed: bool  # 멱등 — 이미 없었거나 이미 삭제됐으면 false


class CommentLikeResponse(BaseModel):
    """이슈 #80(L-6) — POST/DELETE 공용. 이미 같은 상태면 에러 아니라 현재 상태 그대로."""

    liked: bool
    like_count: int
