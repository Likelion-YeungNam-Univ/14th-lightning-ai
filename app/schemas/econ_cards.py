"""E-7 — 경제 상식 카드 조회/관리 스키마."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EconCardSource(BaseModel):
    number: int
    org: str
    doc_title: str
    url: str


class EconCardItem(BaseModel):
    id: int
    title: str


class EconCardListResponse(BaseModel):
    items: list[EconCardItem]
    rotated_at: datetime | None  # 프론트가 다음 정각까지 캐시하는 기준(E-7)


class EconCardDetailResponse(BaseModel):
    id: int
    title: str
    body: str
    sources: list[EconCardSource]


class EconCardGenerateRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=20)  # 무제한 값이면 비용·시간이 통제 밖으로 나간다


class EconCardGenerateAcceptedResponse(BaseModel):
    """BackgroundTasks로 처리 — 결과 통계는 로그로 확인(202, 승래 리뷰 반영)."""

    batch_id: str
    status: Literal["accepted"] = "accepted"


class EconCardPatchRequest(BaseModel):
    action: Literal["approve", "reject", "lock", "unlock"]


class EconCardPatchResponse(BaseModel):
    id: int
    status: str
    locked: bool
