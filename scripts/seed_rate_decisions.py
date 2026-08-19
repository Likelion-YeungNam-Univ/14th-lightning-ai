"""금리 결정문 시드 (확정사항 2절 B3). 멱등 — 결정 추가 시 JSON 수정 후 재실행.
실행: .venv/bin/python -m scripts.seed_rate_decisions"""

import json
import logging
from datetime import datetime
from pathlib import Path

from app.collectors.base import upsert_source_item
from app.db import SessionLocal, init_db
from app.models import MARKET_DOMESTIC

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "rate_decisions.json"


def main() -> None:
    init_db()
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    count = 0
    with SessionLocal() as db:
        for tab in ("bok", "fed"):
            for entry in raw.get(tab, []):
                upsert_source_item(
                    db,
                    tab=tab,
                    market=MARKET_DOMESTIC,  # 금리 탭은 tab 기준 조회 — 구분 공통
                    source_key=entry["source_key"],
                    title=entry["title"],
                    published_at=datetime.strptime(entry["published_at"], "%Y-%m-%d"),
                    origin_url=entry.get("origin_url"),
                    content=entry.get("content"),
                )
                count += 1
        db.commit()
    print(f"완료: 결정문 {count}건 시드")


if __name__ == "__main__":
    main()
