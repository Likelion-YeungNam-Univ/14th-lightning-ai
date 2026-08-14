"""해외 화이트리스트 마스터 동기화 (F-3.2). SEC_USER_AGENT 필요.
CIK·SIC 실시간 해석 + 업종(SIC)·폼 해설 시드까지 한 번에 갱신(멱등).
실행: .venv/bin/python -m scripts.sync_overseas_master"""

import logging

from app.collectors.sec import sync_overseas_master
from app.db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL의 API 키가 로그에 남지 않게


def main() -> None:
    init_db()
    with SessionLocal() as db:
        stats = sync_overseas_master(db)
    print(f"완료: {stats}")


if __name__ == "__main__":
    main()
