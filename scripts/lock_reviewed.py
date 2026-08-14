"""F-5.7 — 시연 종목 생성물 검수 잠금. 잠긴 행은 어떤 배치도 덮어쓰지 않는다.

전수 검수 완료 후 실행한다 (확정사항 2절 B4 — 시연 종목 전수 검수 후 잠금).
실행: .venv/bin/python -m scripts.lock_reviewed 005930 000660 ...   # 종목코드 나열
      .venv/bin/python -m scripts.lock_reviewed --unlock 005930     # 잠금 해제(수정 재생성용)
"""

import logging
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.models import (
    GeneratedContent,
    SourceItem,
    SourceItemStock,
    StockMaster,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def set_locked(db: Session, stock_codes: list[str], locked: bool) -> dict:
    """종목의 공시 생성물(scope=stock) + 해당 업종 규제 생성물(scope=industry) + 금리(global)."""
    stocks = db.query(StockMaster).filter(StockMaster.stock_code.in_(stock_codes)).all()
    industry_keys = {s.industry_code if s.market == "domestic" else s.sic_code for s in stocks} - {
        None
    }

    stock_rows = (
        db.query(GeneratedContent)
        .filter(GeneratedContent.scope == "stock", GeneratedContent.scope_key.in_(stock_codes))
        .all()
    )
    industry_rows = (
        db.query(GeneratedContent)
        .filter(
            GeneratedContent.scope == "industry",
            GeneratedContent.scope_key.in_(industry_keys),
        )
        .all()
        if industry_keys
        else []
    )
    global_rows = db.query(GeneratedContent).filter(GeneratedContent.scope == "global").all()

    count = 0
    for row in [*stock_rows, *industry_rows, *global_rows]:
        if row.locked != locked:
            row.locked = locked
            count += 1
    db.commit()
    return {
        "stocks": len(stocks),
        "changed": count,
        "stock_rows": len(stock_rows),
        "industry_rows": len(industry_rows),
        "global_rows": len(global_rows),
    }


def orphan_check(db: Session, stock_codes: list[str]) -> list[str]:
    """검수 누락 탐지 — 노출 대상인데 요약이 비어 있는 공시 제목을 알려준다."""
    from app.services.industry import displayed_form_codes

    missing = []
    for code in stock_codes:
        stock = db.get(StockMaster, code)
        if stock is None:
            missing.append(f"[없는 종목코드] {code}")
            continue
        market_key = "domestic" if stock.market == "domestic" else "overseas"
        rows = (
            db.query(SourceItem)
            .join(SourceItemStock, SourceItemStock.source_item_id == SourceItem.id)
            .outerjoin(
                GeneratedContent,
                (GeneratedContent.source_item_id == SourceItem.id)
                & (GeneratedContent.scope == "stock")
                & (GeneratedContent.scope_key == code),
            )
            .filter(
                SourceItemStock.stock_code == code,
                SourceItem.tab == "disclosure",
                SourceItem.doc_type.in_(displayed_form_codes(market_key)),
                GeneratedContent.summary_short.is_(None),
            )
            .all()
        )
        missing += [f"{code}: {r.title[:40]}" for r in rows]
    return missing


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--unlock"]
    unlock = "--unlock" in sys.argv
    if not args:
        print("사용법: python -m scripts.lock_reviewed [--unlock] 종목코드...")
        sys.exit(1)
    init_db()
    with SessionLocal() as db:
        if not unlock:
            gaps = orphan_check(db, args)
            if gaps:
                print("⚠️ 요약 미생성(검수 불가) 항목 — 먼저 generate_now 실행:")
                for g in gaps:
                    print("  -", g)
        stats = set_locked(db, args, locked=not unlock)
        print(("잠금" if not unlock else "해제") + f" 완료: {stats}")


if __name__ == "__main__":
    main()
