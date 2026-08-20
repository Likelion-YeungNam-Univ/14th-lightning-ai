"""#77 — 기존 생성물에 hard_terms 소급 + 운영 DB 컬럼 추가. 멱등(재실행 안전).

LLM 호출 0회 — 지식베이스 표제어와 문자열 대조뿐이라 수천 건도 수 초.
locked 행도 처리한다: F-5.7(잠긴 생성물은 배치가 덮지 않음)의 보호 대상은 검수된
'문장'이고, 이 스크립트는 배치가 아니라 사람이 명시적으로 1회 실행하는 도구이며
요약·라벨은 건드리지 않는다(확정사항 20절).

실행: docker compose exec app python -m scripts.backfill_hard_terms
"""

import logging

from sqlalchemy import inspect, text

from app.ai.hard_terms import load_terms, scan_hard_terms
from app.db import SessionLocal, engine, init_db
from app.models import GeneratedContent

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    columns = {c["name"] for c in inspect(engine).get_columns("generated_content")}
    if "hard_terms" not in columns:  # create_all은 기존 테이블에 컬럼을 못 붙인다
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE generated_content ADD COLUMN hard_terms JSON"))
        print("generated_content.hard_terms 컬럼 추가")

    with SessionLocal() as db:
        terms = load_terms(db)
        if not terms:
            print("지식베이스가 비어 있음 — scripts.seed_knowledge 먼저 실행")
            return
        rows = (
            db.query(GeneratedContent)
            .filter(GeneratedContent.hard_terms.is_(None))
            .filter(GeneratedContent.summary_short.isnot(None))
            .all()
        )
        for row in rows:
            row.hard_terms = scan_hard_terms(
                f"{row.summary_short or ''} {row.summary_full or ''}", terms
            )
        db.commit()
        print(f"완료: {len(rows)}건 소급 (표제어 {len(terms)}개 기준, LLM 0회)")


if __name__ == "__main__":
    main()
