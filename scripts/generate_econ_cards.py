"""경제 상식 카드 배치 생성 (E-5.3 초기 적재 120건 / E-5.3a 보충 5~10건).
실행: .venv/bin/python -m scripts.generate_econ_cards [건수, 기본 10]"""

import logging
import sys

from app.ai.econ_cards import generate_batch
from app.ai.llm_client import get_llm_client
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL의 API 키가 로그에 남지 않게


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    init_db()
    client = get_llm_client()
    with SessionLocal() as db:
        stats = generate_batch(db, client, count)
    print(f"완료: {stats}")
    print(f"표본 검수는 scripts.review_econ_cards {stats['batch_id']} 로 진행하세요.")


if __name__ == "__main__":
    main()
