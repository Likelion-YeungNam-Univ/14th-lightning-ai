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
    result = {
        "summaries": generate_summaries(db, client, limit=limit),
        "link_sentences": generate_link_sentences(db, client, limit=limit),
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
