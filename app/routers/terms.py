"""F-5.4 — POST /terms/explain. 무인증(세션 있으면 활용, F-1.3).

사용자 요청 경로 LLM 호출의 유일한 예외 — 레이트리밋 필수 (F-8.3).
캐시 히트는 LLM을 부르지 않으므로 한도에 세지 않는다.
"""

from fastapi import APIRouter, Request

from app.ai.llm_client import LLMError, get_llm_client
from app.ai.term_explain import TAB_DESC, generate_term_explanation
from app.deps import DbDep, OptionalSession
from app.errors import AppError
from app.models import TermCache
from app.schemas.terms import TERM_MAX_LENGTH, TermExplainRequest, TermExplainResponse, TermSource
from app.services import ratelimit

router = APIRouter(tags=["terms"])


@router.post("/terms/explain", response_model=TermExplainResponse)
def explain_term(
    body: TermExplainRequest, request: Request, session: OptionalSession, db: DbDep
) -> TermExplainResponse:
    term = " ".join(body.term.split())  # 드래그 시 섞이는 개행·연속 공백 정리
    if not term:
        raise AppError("invalid_term", "용어가 비어 있습니다")
    if len(term) > TERM_MAX_LENGTH:
        raise AppError(
            "term_too_long",
            f"용어는 {TERM_MAX_LENGTH}자 이내로 보내주세요",
            details={"max_length": TERM_MAX_LENGTH},
        )
    if body.tab not in TAB_DESC:
        raise AppError("invalid_tab", f"알 수 없는 탭: {body.tab}")

    cached = db.get(TermCache, (term, body.tab))
    if cached is not None:  # 캐시 우선 — LLM·레이트리밋 미소모 (F-5.4)
        return TermExplainResponse(
            term=term, tab=body.tab, explanation=cached.explanation, sources=[], cached=True
        )

    session_key = f"s:{session.id}" if session else None
    ip_key = f"ip:{request.client.host if request.client else 'unknown'}"
    if (
        session_key is not None
        and not ratelimit.allow(session_key, ratelimit.SESSION_LIMIT_PER_MINUTE)
    ) or not ratelimit.allow(ip_key, ratelimit.IP_LIMIT_PER_MINUTE):
        raise AppError(
            "rate_limited", "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요", status_code=429
        )

    try:
        client = get_llm_client()
        result = generate_term_explanation(
            db, client, term=term, tab=body.tab, context=body.context
        )
    except LLMError as e:
        raise AppError("llm_unavailable", "용어 설명 생성에 실패했습니다", status_code=503) from e

    if result["explanation"]:  # 실패(빈 값)는 캐시하지 않는다 — 다음 시도에 기회를 준다
        db.add(TermCache(term=term, tab=body.tab, explanation=result["explanation"]))
        db.commit()

    return TermExplainResponse(
        term=term,
        tab=body.tab,
        explanation=result["explanation"],
        sources=[TermSource(**s) for s in result["sources"]],
        cached=False,
    )
