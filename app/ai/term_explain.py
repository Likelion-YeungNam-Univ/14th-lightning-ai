"""F-5.4 — 드래그 용어 풀이. 사용자 요청 경로에서 LLM을 부르는 **유일한** 지점(불변식 1).

파이프라인: 용어 임베딩 → 지식베이스 코사인 top-3(하한 미달 미주입) → 정의 생성 →
가드레일(재생성 1회, 실패 시 빈 값). 근거 목록을 응답에 그대로 노출한다 — RAG 설명 가능성.
"""

import logging

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


def generate_term_explanation(
    db: Session, client: OpenAIClient, *, term: str, tab: str, context: str | None
) -> dict:
    """반환: {explanation(실패 시 None), sources}."""
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

    out = client.generate_json(
        system=TERM_EXPLAIN_SYSTEM,
        user=user,
        schema=TERM_EXPLAIN_SCHEMA,
        reasoning_effort=REASONING_EFFORT,
    )
    explanation = (out.get("explanation") or "").strip()
    bad = find_violations(explanation)
    if bad:  # 재생성 1회 → 그래도 위반이면 비운다 (F-5.5)
        reasons = ", ".join(bad)
        out = client.generate_json(
            system=TERM_EXPLAIN_SYSTEM,
            user=user + RETRY_SUFFIX.format(reasons=reasons),
            schema=TERM_EXPLAIN_SCHEMA,
            reasoning_effort=REASONING_EFFORT,
        )
        explanation = (out.get("explanation") or "").strip()
        if find_violations(explanation):
            explanation = ""

    return {
        "explanation": explanation or None,
        "sources": [
            {"term": chunk.term, "source": chunk.source, "similarity": round(sim, 3)}
            for chunk, sim in hits
        ],
    }
