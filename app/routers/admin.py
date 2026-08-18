import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Response

from app.ai import econ_cards as econ_ai
from app.ai.llm_client import LLMError, get_llm_client
from app.config import settings
from app.deps import CurrentSession, DbDep
from app.errors import AppError
from app.models import EconCard
from app.schemas.econ_cards import (
    EconCardGenerateAcceptedResponse,
    EconCardGenerateRequest,
    EconCardPatchRequest,
    EconCardPatchResponse,
)
from app.schemas.sessions import ResetDemoResponse
from app.services.sessions import reset_demo

router = APIRouter(tags=["admin"])

AdminToken = Annotated[str | None, Header(alias="X-Admin-Token")]


def _check_admin_token(x_admin_token: str | None) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise AppError("admin_token_invalid", "관리자 토큰이 올바르지 않습니다", 401)


@router.post("/admin/reset-demo", response_model=ResetDemoResponse)
def reset_demo_endpoint(
    session: CurrentSession, db: DbDep, x_admin_token: AdminToken = None
) -> ResetDemoResponse:
    """F-1.5 — 리허설 후 첫 진입 화면 재현. ADMIN_TOKEN 헤더로 보호(확정사항 4절)."""
    _check_admin_token(x_admin_token)
    return ResetDemoResponse(**reset_demo(db, session))


@router.post("/admin/econ-cards/generate", response_model=EconCardGenerateAcceptedResponse)
def generate_econ_cards_endpoint(
    body: EconCardGenerateRequest,
    background: BackgroundTasks,
    response: Response,
    x_admin_token: AdminToken = None,
) -> EconCardGenerateAcceptedResponse:
    """E-7 — 배치 트리거. 120건 생성은 수 분 걸릴 수 있어 BackgroundTasks로 즉시 202를
    반환한다(승래 리뷰) — 관리자 조작이라 LLM 호출 자체는 허용된다(불변식 1 예외 아님,
    사용자 요청 경로가 아니므로).
    """
    _check_admin_token(x_admin_token)
    try:  # 설정 누락은 배치에 던지지 않고 여기서 즉시 503으로 알린다(승래 리뷰)
        get_llm_client()
    except LLMError as e:
        raise AppError("llm_unavailable", str(e), 503) from e
    batch_id = str(uuid.uuid4())
    background.add_task(econ_ai.generate_batch_background, body.count, batch_id)
    response.status_code = 202
    return EconCardGenerateAcceptedResponse(batch_id=batch_id)


@router.patch("/admin/econ-cards/{card_id}", response_model=EconCardPatchResponse)
def patch_econ_card_endpoint(
    card_id: int, body: EconCardPatchRequest, db: DbDep, x_admin_token: AdminToken = None
) -> EconCardPatchResponse:
    """E-7 — 승인·반려·잠금.

    표본 검수(E-3.2)의 배치 단위 처리와 별개로 개별 카드를 수동 조정할 때 쓴다.
    """
    _check_admin_token(x_admin_token)
    card = db.get(EconCard, card_id)
    if card is None:
        raise AppError("unknown_econ_card", "존재하지 않는 카드입니다", 404)
    if body.action == "approve":
        card.status = "approved"
        card.locked = True
        card.approved_by = "admin"
        card.approved_at = datetime.now()
    elif body.action == "reject":
        card.status = "rejected"
    elif body.action == "lock":
        card.locked = True
    elif body.action == "unlock":
        card.locked = False
    db.commit()
    return EconCardPatchResponse(id=card.id, status=card.status, locked=card.locked)
