"""#95 — 커뮤니티 방 정원 컬럼(max_entrants) 운영 DB 반영. 멱등(재실행 안전).

기존 방은 NULL로 남고 코드가 4로 간주하므로, 이 스크립트를 안 돌려도 서비스는 500 없이
동작한다(신규 방 생성만 컬럼 부재로 실패). 배포 직후 1회 실행 권장.
실행: docker compose exec app python -m scripts.migrate_community
"""

import logging

from sqlalchemy import inspect, text

from app.db import engine, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    columns = {c["name"] for c in inspect(engine).get_columns("betting_room")}
    if "max_entrants" in columns:
        print("betting_room.max_entrants 이미 존재 — 변경 없음")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE betting_room ADD COLUMN max_entrants INTEGER"))
    print("완료: betting_room.max_entrants 추가 — 기존 방은 NULL(=정원 4로 간주), 무손실")


if __name__ == "__main__":
    main()
