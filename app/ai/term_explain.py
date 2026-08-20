"""F-5.4 — 드래그 용어 풀이. 사용자 요청 경로에서 LLM을 부르는 **유일한** 지점(불변식 1).

파이프라인: 용어 임베딩 → 지식베이스 코사인 top-3(하한 미달 미주입) → 정의 생성 →
가드레일(재생성 1회, 실패 시 빈 값). 근거 목록을 응답에 그대로 노출한다 — RAG 설명 가능성.
"""

import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.ai.guardrail import find_violations
from app.ai.llm_client import OpenAIClient
from app.ai.prompts import (
    RETRY_SUFFIX,
    TERM_EXPLAIN_SCHEMA,
    TERM_EXPLAIN_SYSTEM,
    TERM_EXPLAIN_TMPL,
)
from app.ai.rag import find_exact_term, search_knowledge

logger = logging.getLogger(__name__)

KNOWLEDGE_SOURCES = ("bok_700", "dart_doctype", "sec_formtype")
# 사용자가 기다리는 유일한 LLM 경로(F-5.4) — 추론 토큰을 끄면 6.8s → 2.3s, 근거 밀착은 유지 (#69)
REASONING_EFFORT = "minimal"
TAB_DESC = {
    "youtube": "유튜브 영상 목록",
    "disclosure": "기업 공시 카드",
    "regulation": "규제 동향 카드",
    "bok": "한국은행 기준금리 카드",
    "fed": "미국 기준금리 카드",
}


def _build_prompt(
    db: Session, client: OpenAIClient, *, term: str, tab: str, context: str | None
) -> tuple[str, list]:
    """RAG 근거 검색 + 프롬프트 조립 — 동기 응답과 SSE가 같은 입력을 쓴다. 반환: (user, hits)."""
    exact = find_exact_term(db, term)  # 표제어 일치가 있으면 그것이 정답 — 벡터 검색 생략
    if exact is not None:
        hits = [(exact, 1.0)]
    else:
        hits = search_knowledge(db, client.embed([term])[0], sources=KNOWLEDGE_SOURCES)
    grounds = (
        "\n\n".join(f"[{chunk.term}] {chunk.content[:800]}" for chunk, _sim in hits)
        or "(검색된 근거 없음)"
    )
    user = TERM_EXPLAIN_TMPL.format(
        term=term,
        tab_desc=TAB_DESC.get(tab, tab),
        context=(context or "(없음)")[:500],
        grounds=grounds,
    )
    return user, hits


def _sources(hits: list) -> list[dict]:
    return [
        {"term": chunk.term, "source": chunk.source, "similarity": round(sim, 3)}
        for chunk, sim in hits
    ]


def _regenerate(client: OpenAIClient, user: str, reasons: list[str]) -> str:
    """가드레일 위반 시 재생성 1회(스키마 강제) → 그래도 위반이면 빈 값 (F-5.5)."""
    out = client.generate_json(
        system=TERM_EXPLAIN_SYSTEM,
        user=user + RETRY_SUFFIX.format(reasons=", ".join(reasons)),
        schema=TERM_EXPLAIN_SCHEMA,
        reasoning_effort=REASONING_EFFORT,
    )
    explanation = (out.get("explanation") or "").strip()
    return "" if find_violations(explanation) else explanation


def generate_term_explanation(
    db: Session, client: OpenAIClient, *, term: str, tab: str, context: str | None
) -> dict:
    """반환: {explanation(실패 시 None), sources}."""
    user, hits = _build_prompt(db, client, term=term, tab=tab, context=context)
    out = client.generate_json(
        system=TERM_EXPLAIN_SYSTEM,
        user=user,
        schema=TERM_EXPLAIN_SCHEMA,
        reasoning_effort=REASONING_EFFORT,
    )
    explanation = (out.get("explanation") or "").strip()
    bad = find_violations(explanation)
    if bad:
        explanation = _regenerate(client, user, bad)
    return {"explanation": explanation or None, "sources": _sources(hits)}


# SSE(#71) — 스트리밍은 JSON 스키마를 못 쓰므로 출력 형식을 프롬프트로 고정한다
STREAM_SUFFIX = "\n\n설명 문장만 평문으로 답하라. 머리말·목록·JSON·마크다운 없이."


def stream_term_explanation(
    db: Session, client: OpenAIClient, *, term: str, tab: str, context: str | None
) -> Iterator[tuple[str, dict]]:
    """SSE용 이벤트 생성기 — (event, data) 순서: meta → delta* → done | replace.

    가드레일(F-5.5)은 **델타마다 누적 텍스트**에 건다. 위반 규칙은 부분 문자열 매칭이라
    접두사에서 걸리면 전체에서도 걸리므로 동기 응답과 판정이 같다. 위반 순간 스트림을 끊고
    비스트림 재생성 1회 → `replace`로 통째 교체(이미 나간 글자는 프론트가 덮어쓴다).
    캐시 저장은 호출자(라우터)가 done/replace의 explanation으로 한다.
    """
    user, hits = _build_prompt(db, client, term=term, tab=tab, context=context)
    yield "meta", {"term": term, "tab": tab, "cached": False, "sources": _sources(hits)}

    accumulated = ""
    violated: list[str] = []
    pieces = client.stream_text(
        system=TERM_EXPLAIN_SYSTEM, user=user + STREAM_SUFFIX, reasoning_effort=REASONING_EFFORT
    )
    try:
        for piece in pieces:
            accumulated += piece
            violated = find_violations(accumulated)
            if violated:
                break  # 위반 조각은 내보내지 않는다
            yield "delta", {"text": piece}
    finally:
        pieces.close()  # 중단 시 OpenAI 연결도 닫는다 (break·클라이언트 이탈 모두)

    if not violated:
        yield "done", {"explanation": accumulated.strip() or None}
        return
    logger.info("용어 풀이 스트림 가드레일 위반 → 재생성: %s", ", ".join(violated))
    yield "replace", {"explanation": _regenerate(client, user, violated) or None}
