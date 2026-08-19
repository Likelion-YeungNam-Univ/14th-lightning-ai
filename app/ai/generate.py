"""F-5.6 — 생성 배치 진입점. 수집(F-4) 직후 체인으로 돈다."""

import logging

from sqlalchemy.orm import Session

from app.ai.link_sentence import generate_link_sentences
from app.ai.llm_client import OpenAIClient, get_llm_client
from app.ai.summarize import generate_summaries
from app.models import SourceItem, SourceItemStock

logger = logging.getLogger(__name__)


def generate_all(db: Session, client: OpenAIClient | None = None, limit: int | None = None) -> dict:
    client = client or get_llm_client()
    # 연결 문장(업종×지표, 수십 회·1~2분)을 먼저 — 요약 밀린 양이 수백 건이면 몇 시간 걸려서
    # 그 뒤에 두면 재기동 한 번에 그날 "내 종목엔"이 통째로 빠진다 (#61). 서로 의존 없음.
    result = {
        "link_sentences": generate_link_sentences(db, client, limit=limit),
        "summaries": generate_summaries(db, client, limit=limit),
    }
    logger.info("generate_all: %s", result)
    return result


def generate_for_stock(db: Session, stock_code: str, client: OpenAIClient | None = None) -> dict:
    """온디맨드 — 방금 추가된 종목의 공시만. 규제·금리·연결 문장은 일 배치가 맡는다."""
    client = client or get_llm_client()
    items = (
        db.query(SourceItem)
        .join(SourceItemStock, SourceItemStock.source_item_id == SourceItem.id)
        .filter(SourceItemStock.stock_code == stock_code, SourceItem.tab == "disclosure")
        .all()
    )
    return generate_summaries(db, client, items=items)
