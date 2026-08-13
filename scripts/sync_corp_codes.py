"""DART corp_code 수동 동기화 (F-2.1.1). DART_API_KEY 필요.
실행: .venv/bin/python -m scripts.sync_corp_codes"""

import logging

from app.collectors.dart import sync_corp_codes
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    with SessionLocal() as db:
        stats = sync_corp_codes(db)
    print(f"완료: {stats}")


if __name__ == "__main__":
    main()
