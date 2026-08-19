"""종목 마스터 수동 동기화 (F-2.1). 실행: .venv/bin/python -m scripts.sync_stock_master"""

import logging

from app.collectors.krx import sync_stock_master
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    with SessionLocal() as db:
        stats = sync_stock_master(db)
    print(f"완료: {stats}")


if __name__ == "__main__":
    main()
