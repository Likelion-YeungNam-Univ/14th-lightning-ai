"""수동 전체 수집 — 시연 직전 당일 자료 반영용. 06:00 배치와 동일 경로.
실행: .venv/bin/python -m scripts.collect_now"""

import logging

from app.collectors.runner import collect_all
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    with SessionLocal() as db:
        result = collect_all(db)
    print(f"완료: {result}")


if __name__ == "__main__":
    main()
