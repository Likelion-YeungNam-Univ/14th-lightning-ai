"""F-5.4 — POST /terms/explain (동기) + GET /terms/explain/stream (SSE, #71).
무인증(세션 있으면 활용, F-1.3).

사용자 요청 경로 LLM 호출의 유일한 예외 — 레이트리밋 필수 (F-8.3).
캐시 히트는 LLM을 부르지 않으므로 한도에 세지 않는다.

SSE 엔드포인트가 GET인 이유: 브라우저 `EventSource`는 POST를 못 한다. 이벤트 계약은
`meta` → `delta`* → `done` | `replace` | `error` (docs/프론트연동가이드.md 3절).
"""

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.llm_client import LLMError, get_llm_client
from app.ai.term_explain import TAB_DESC, generate_term_explanation, stream_term_explanation
from app.db import SessionLocal
from app.deps import DbDep, OptionalSession
from app.errors import AppError
from app.models import TermCache, UserSession
from app.schemas.terms import TERM_MAX_LENGTH, TermExplainRequest, TermExplainResponse, TermSource
from app.services import ratelimit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terms"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",  # 프록시·브라우저 캐시 금지
    # nginx가 응답을 모아뒀다 한 번에 보내지 않게 (location의 proxy_buffering off와 동일 효과)
    "X-Accel-Buffering": "no",
}


def _normalize(term: str, tab: str) -> str:
    """드래그 시 섞이는 개행·연속 공백 정리 + 검증 — 동기·SSE 공통. 반환: 정리된 용어."""
    term = " ".join(term.split())
    if not term:
        raise AppError("invalid_term", "용어가 비어 있습니다")
    if len(term) > TERM_MAX_LENGTH:
        raise AppError(
            "term_too_long",
            f"용어는 {TERM_MAX_LENGTH}자 이내로 보내주세요",
            details={"max_length": TERM_MAX_LENGTH},
        )
    if tab not in TAB_DESC:
        raise AppError("invalid_tab", f"알 수 없는 탭: {tab}")
    return term


def _check_rate_limit(request: Request, session: UserSession | None) -> None:
    """F-8.3 — 세션 20/분, IP 60/분. 캐시 미스(= LLM 호출) 직전에만 센다."""
    session_key = f"s:{session.id}" if session else None
    ip_key = f"ip:{request.client.host if request.client else 'unknown'}"
    if (
        session_key is not None
        and not ratelimit.allow(session_key, ratelimit.SESSION_LIMIT_PER_MINUTE)
    ) or not ratelimit.allow(ip_key, ratelimit.IP_LIMIT_PER_MINUTE):
        raise AppError(
            "rate_limited", "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요", status_code=429
        )


def _save_cache(db: Session, term: str, tab: str, explanation: str) -> None:
    """실패(빈 값)는 호출자가 걸러서 넘긴다 — 다음 시도에 기회를 준다."""
    db.add(TermCache(term=term, tab=tab, explanation=explanation))
    try:
        db.commit()
    except IntegrityError:
        # 같은 (term, tab)을 동시에 두 요청이 캐시 미스로 판단해 동시에 LLM을 부른 경우 —
        # 먼저 커밋된 쪽이 이기고, 이 요청은 그 결과를 그대로 쓴다(둘 다 같은 값이라 무관)
        db.rollback()


@router.post("/terms/explain", response_model=TermExplainResponse)
def explain_term(
    body: TermExplainRequest, request: Request, session: OptionalSession, db: DbDep
) -> TermExplainResponse:
    term = _normalize(body.term, body.tab)

    cached = db.get(TermCache, (term, body.tab))
    if cached is not None:  # 캐시 우선 — LLM·레이트리밋 미소모 (F-5.4)
        return TermExplainResponse(
            term=term, tab=body.tab, explanation=cached.explanation, sources=[], cached=True
        )

    _check_rate_limit(request, session)
    try:
        client = get_llm_client()
        result = generate_term_explanation(
            db, client, term=term, tab=body.tab, context=body.context
        )
    except LLMError as e:
        raise AppError("llm_unavailable", "용어 설명 생성에 실패했습니다", status_code=503) from e

    if result["explanation"]:
        _save_cache(db, term, body.tab, result["explanation"])

    return TermExplainResponse(
        term=term,
        tab=body.tab,
        explanation=result["explanation"],
        sources=[TermSource(**s) for s in result["sources"]],
        cached=False,
    )


def _sse(event: str, data: dict) -> str:
    """SSE 와이어 포맷 한 이벤트 — `event:` 줄 + `data:` 줄 + 빈 줄(구분자)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_response(events: Iterator[str]) -> StreamingResponse:
    # 동기 제너레이터 — Starlette가 스레드풀에서 돌린다(불변식 2: 이벤트 루프를 막지 않음)
    return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/terms/explain/stream")
def explain_term_stream(
    request: Request,
    session: OptionalSession,
    db: DbDep,
    term: str = Query(..., description="드래그한 용어"),
    tab: str = Query(..., description="youtube|disclosure|regulation|bok|fed"),
    context: str | None = Query(None, description="해당 자료의 요약 본문(선택)"),
) -> StreamingResponse:
    """F-5.4 SSE 변형(#71) — 첫 글자를 ~0.5s에 띄우고 타자 치듯 채운다.

    검증·캐시·레이트리밋 판정은 **스트림을 열기 전**에 끝낸다(그래야 400/429가 평범한 JSON
    에러로 나간다). 캐시 히트는 meta+done 두 이벤트로 즉시 종료. 프론트는 done/replace/error를
    받으면 `EventSource.close()` — 안 닫으면 브라우저가 자동 재접속하는데, 재접속해도 캐시
    히트라 LLM은 다시 부르지 않는다.
    """
    term = _normalize(term, tab)

    cached = db.get(TermCache, (term, tab))
    if cached is not None:
        return _sse_response(
            iter(
                [
                    _sse("meta", {"term": term, "tab": tab, "cached": True, "sources": []}),
                    _sse("done", {"explanation": cached.explanation}),
                ]
            )
        )

    _check_rate_limit(request, session)
    client = get_llm_client()

    def events() -> Iterator[str]:
        # 요청 스코프 DB 세션(DbDep)은 응답이 흐르는 동안 닫혀 있을 수 있어 자체 세션을 쓴다
        with SessionLocal() as stream_db:
            final: str | None = None
            try:
                for event, data in stream_term_explanation(
                    stream_db, client, term=term, tab=tab, context=context
                ):
                    if event in ("done", "replace"):
                        final = data.get("explanation")
                    yield _sse(event, data)
            except LLMError as e:
                logger.warning("용어 풀이 스트림 실패 term=%s: %s", term, e)
                yield _sse(
                    "error", {"code": "llm_unavailable", "message": "용어 설명 생성에 실패했습니다"}
                )
                return
            if final:
                _save_cache(stream_db, term, tab, final)

    return _sse_response(events())
