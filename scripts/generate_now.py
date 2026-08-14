"""수동 AI 생성 — 시연 직전 미생성분 처리용. 멱등(기존 생성물은 건너뜀).
실행: .venv/bin/python -m scripts.generate_now [건수 제한]"""

import logging
import sys

from app.ai.generate import generate_all
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL의 API 키가 로그에 남지 않게


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    init_db()
    with SessionLocal() as db:
        result = generate_all(db, limit=limit)
    print(f"완료: {result}")


if __name__ == "__main__":
    main()
