"""F-5.4 — 용어 풀이 요청/응답 스키마."""

from pydantic import BaseModel, Field

TERM_MAX_LENGTH = 50  # F-5.4 — 드래그 입력 상한


class TermExplainRequest(BaseModel):
    term: str = Field(min_length=1)
    tab: str
    context: str | None = None  # 해당 자료의 요약 본문 (프론트가 함께 보낸다)


class TermSource(BaseModel):
    """RAG 근거 — 어떤 지식으로 설명했는지 그대로 노출한다 (설명 가능성)."""

    term: str
    source: str  # bok_700 | dart_doctype | sec_formtype
    similarity: float


class TermExplainResponse(BaseModel):
    term: str
    tab: str
    explanation: str | None  # 가드레일 최종 실패 시 null — 프론트는 "설명 불가" 안내
    sources: list[TermSource]
    cached: bool
