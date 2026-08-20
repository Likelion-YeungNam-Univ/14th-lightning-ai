"""E-7 — 경제 상식 카드 조회. 무인증, 회전(E-5)이 미리 만들어둔 것만 읽는다."""

from fastapi import APIRouter

from app.deps import DbDep
from app.errors import AppError
from app.schemas.econ_cards import EconCardDetailResponse, EconCardItem, EconCardListResponse
from app.services import econ_cards as econ_service

router = APIRouter(tags=["econ-cards"])


@router.get("/econ-cards", response_model=EconCardListResponse)
def list_econ_cards(db: DbDep) -> EconCardListResponse:
    cards, rotated_at = econ_service.current_rotation(db)
    return EconCardListResponse(
        items=[EconCardItem(id=c.id, title=c.title) for c in cards], rotated_at=rotated_at
    )


@router.get("/econ-card/{card_id}", response_model=EconCardDetailResponse)
def get_econ_card(card_id: int, db: DbDep) -> EconCardDetailResponse:
    card = econ_service.get_approved_card(db, card_id)
    if card is None:
        raise AppError("unknown_econ_card", "존재하지 않는 카드입니다", 404)
    return EconCardDetailResponse(
        id=card.id,
        title=card.title,
        body=card.body,
        hard_terms=card.hard_terms or None,
        sources=card.sources,
    )
