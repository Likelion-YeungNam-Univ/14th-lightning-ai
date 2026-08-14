"""확정사항 5절(F-5.8) — RAG 검색. 코사인 top-3, 유사도 하한 미달 시 미주입.

Postgres에서는 pgvector 코사인 연산자를 쓰고, 테스트(sqlite)에서는 파이썬 코사인으로
폴백한다 — 지식베이스가 700행 수준이라 폴백도 충분히 빠르며 동작이 동일하다.
"""

import logging
import math

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk

logger = logging.getLogger(__name__)

TOP_K = 3
MIN_SIMILARITY = 0.35  # 하한 미달 근거는 주입하지 않는다 (환각 유도 방지)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def search_knowledge(
    db: Session,
    query_embedding: list[float],
    *,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
    sources: tuple[str, ...] | None = None,
) -> list[tuple[KnowledgeChunk, float]]:
    """반환: [(청크, 유사도)] — 유사도 내림차순, 하한 이상만."""
    if db.get_bind().dialect.name == "postgresql":
        distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        query = db.query(KnowledgeChunk, distance.label("distance")).filter(
            KnowledgeChunk.embedding.isnot(None)
        )
        if sources:
            query = query.filter(KnowledgeChunk.source.in_(sources))
        rows = query.order_by(distance).limit(top_k).all()
        return [(chunk, 1 - dist) for chunk, dist in rows if 1 - dist >= min_similarity]

    # sqlite(테스트) 폴백 — 전량 로드 후 파이썬 코사인
    query = db.query(KnowledgeChunk).filter(KnowledgeChunk.embedding.isnot(None))
    if sources:
        query = query.filter(KnowledgeChunk.source.in_(sources))
    scored = [(chunk, _cosine(list(chunk.embedding), query_embedding)) for chunk in query.all()]
    scored.sort(key=lambda pair: -pair[1])
    return [(c, s) for c, s in scored[:top_k] if s >= min_similarity]
