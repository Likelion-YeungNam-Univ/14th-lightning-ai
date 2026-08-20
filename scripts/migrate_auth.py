"""#74 실계정 — 운영 DB 무손실 마이그레이션.

init_db()의 create_all은 새 테이블(app_user)은 만들지만 기존 테이블(session)에
컬럼을 추가하지 못한다 — user_id만 ALTER로 직접 붙인다. 멱등(재실행 안전).
실행: docker compose exec app python -m scripts.migrate_auth
"""

import logging

from sqlalchemy import inspect, text

from app.db import engine, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    init_db()  # app_user 테이블 생성 (이미 있으면 no-op)
    columns = {c["name"] for c in inspect(engine).get_columns("session")}
    if "user_id" in columns:
        print("session.user_id 이미 존재 — 변경 없음")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE session ADD COLUMN user_id INTEGER"))
        conn.execute(text("CREATE INDEX ix_session_user_id ON session (user_id)"))
    print("완료: session.user_id 추가 + 인덱스. 기존 행은 NULL(익명) — 데이터 무손실")


if __name__ == "__main__":
    main()
