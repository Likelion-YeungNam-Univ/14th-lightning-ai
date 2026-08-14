"""F-8.2 — POST /events. 무인증(세션 있으면 session_id 기록) — 홈 도달은 세션 발급 전일 수 있다.

payload는 허용 키만 저장한다 — 평단가·수량 등 금액 정보가 어떤 경로로도
들어오지 않게 스키마 수준에서 막는다 (F-8.6).
"""

from fastapi import APIRouter

from app.deps import DbDep, OptionalSession
from app.errors import AppError
from app.models import EventLog
from app.schemas.events import ALLOWED_PAYLOAD_KEYS, EVENT_NAMES, EventRequest, EventResponse

router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventResponse)
def record_event(body: EventRequest, session: OptionalSession, db: DbDep) -> EventResponse:
    if body.event_name not in EVENT_NAMES:
        raise AppError(
            "unknown_event",
            f"허용되지 않는 이벤트입니다: {body.event_name}",
            details={"allowed": sorted(EVENT_NAMES)},
        )
    payload = {k: v for k, v in (body.payload or {}).items() if k in ALLOWED_PAYLOAD_KEYS} or None
    db.add(
        EventLog(
            session_id=session.id if session else None,
            event_name=body.event_name,
            payload_json=payload,
        )
    )
    db.commit()
    return EventResponse(accepted=True)
