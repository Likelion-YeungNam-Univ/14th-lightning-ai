"""경제 상식 카드 표본 검수 (E-3.2). 배치 하나를 통째로 승인/반려한다.

실행:
  1) .venv/bin/python -m scripts.review_econ_cards <batch_id>          # 표본 출력만
  2) .venv/bin/python -m scripts.review_econ_cards <batch_id> pass 검수자이름
  3) .venv/bin/python -m scripts.review_econ_cards <batch_id> fail 검수자이름
"""

import sys

from app.ai.econ_cards import apply_batch_review, review_sample
from app.db import SessionLocal, init_db


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: review_econ_cards <batch_id> [pass|fail] [검수자이름]")
        return
    batch_id = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else None

    init_db()
    with SessionLocal() as db:
        if action not in ("pass", "fail"):
            sample = review_sample(db, batch_id)
            if not sample:
                print("이 배치에 filtered 상태 카드가 없습니다(이미 처리됐거나 잘못된 batch_id).")
                return
            print(f"표본 {len(sample)}건 — 각주 문장을 sources 링크와 대조하세요:\n")
            for c in sample:
                print(f"[{c.id}] {c.title}")
                print(c.body)
                for s in c.sources:
                    print(f"  ({s['number']}) {s['org']} · {s['doc_title']} · {s['url']}")
                print()
            return

        reviewer = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        result = apply_batch_review(db, batch_id, passed=(action == "pass"), reviewer=reviewer)
        print(f"완료: {result}")


if __name__ == "__main__":
    main()
