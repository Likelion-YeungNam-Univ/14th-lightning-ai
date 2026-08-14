"""확정사항 5절 — RAG 지식베이스 적재. OPENAI_API_KEY 필요.

소스 3종: ① 한국은행 경제금융용어 700선(data/bok_700.json — scripts/parse_bok700.py 산출)
② 국내 공시유형 해설 ③ 미국 폼 해설 (data/disclosure_form_types.json).
소스 단위로 지우고 다시 넣는다(멱등) — 임베딩 비용은 700건 기준 수백 원 수준.
실행: .venv/bin/python -m scripts.seed_knowledge
"""

import json
import logging
import time

from app.ai.llm_client import LLMError, get_llm_client
from app.db import SessionLocal, init_db
from app.models import KnowledgeChunk
from app.services.industry import DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL의 API 키가 로그에 남지 않게

BATCH = 50  # 임베딩 TPM 한도(40k) 고려 — 배치당 약 1.7만 토큰
RATE_RETRY_WAIT = 25.0


def _embed_with_retry(client, texts: list[str]) -> list[list[float]]:
    """429(분당 토큰 한도)면 대기 후 재시도 — 실측: 700건 전체가 한도의 수 배."""
    for _attempt in range(6):
        try:
            return client.embed(texts)
        except LLMError as e:
            if "429" not in str(e):
                raise
            print(f"  임베딩 한도 대기 {RATE_RETRY_WAIT}s ...")
            time.sleep(RATE_RETRY_WAIT)
    raise LLMError("임베딩 재시도 초과")


def load_chunks() -> list[dict]:
    chunks: list[dict] = []
    bok = json.loads((DATA_DIR / "bok_700.json").read_text(encoding="utf-8"))
    for e in bok["entries"]:
        chunks.append({"source": "bok_700", "term": e["term"][:128], "content": e["content"]})
    forms = json.loads((DATA_DIR / "disclosure_form_types.json").read_text(encoding="utf-8"))
    for row in forms["domestic"]:
        chunks.append(
            {"source": "dart_doctype", "term": row["form_code"], "content": row["description"]}
        )
    for row in forms["overseas"]:
        chunks.append(
            {"source": "sec_formtype", "term": row["form_code"], "content": row["description"]}
        )
    return chunks


def main() -> None:
    init_db()
    client = get_llm_client()
    chunks = load_chunks()
    print(f"적재 대상: {len(chunks)}건")

    with SessionLocal() as db:
        db.query(KnowledgeChunk).delete()  # 소스 전체 재적재(멱등)
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i : i + BATCH]
            # 검색 질의는 용어명이므로 용어명+정의를 함께 임베딩한다
            inputs = [f"{c['term']}\n{c['content'][:2000]}" for c in batch]
            vectors = _embed_with_retry(client, inputs)
            for chunk, vec in zip(batch, vectors, strict=True):
                db.add(KnowledgeChunk(embedding=vec, **chunk))
            db.commit()
            print(f"  {min(i + BATCH, len(chunks))}/{len(chunks)}")
    print("완료")


if __name__ == "__main__":
    main()
