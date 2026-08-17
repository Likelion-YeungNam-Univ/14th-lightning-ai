"""E-7 — 경제 상식 카드 조회/관리 스키마."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
    count: int = 10


class EconCardGenerateResponse(BaseModel):
    batch_id: str
    requested: int
    filtered: int
    rejected: int
    discarded: int


class EconCardPatchRequest(BaseModel):
    action: Literal["approve", "reject", "lock", "unlock"]


class EconCardPatchResponse(BaseModel):
    id: int
    status: str
    locked: bool
