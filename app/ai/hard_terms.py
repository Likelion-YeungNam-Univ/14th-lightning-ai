"""#77 — 요약 속 '어려운 단어' 스캔 (PM 확정: AI가 미리 표시, 탭하면 풀이).

LLM을 쓰지 않는다 — 지식베이스(한국은행 700선 + 공시 유형 해설) 표제어가 요약문에
등장하면 그 목록을 돌려준다. 표시된 단어는 전부 정확 일치 표제어라 탭 즉시(F-5.4
정확 일치 경로 → 캐시) 설명이 뜬다. 환각·가드레일 걱정이 없는 이유다.
"""

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk

MIN_TERM_LEN = 2  # 한 글자 표제어(예: '환')는 오탐이 많아 제외
MAX_TERMS = 8  # 카드 하나에 과도한 하이라이트 방지 — 등장 순서 앞쪽 우선


def load_terms(db: Session) -> list[str]:
    """지식베이스 표제어(긴 것 우선). 호출자가 배치당 1회 로드해 재사용한다."""
    rows = db.query(KnowledgeChunk.term).distinct().all()
    terms = {t.strip() for (t,) in rows if t and len(t.strip()) >= MIN_TERM_LEN}
    return sorted(terms, key=len, reverse=True)  # 긴 표제어 우선 — '가산금리' > '금리'


def scan_hard_terms(text: str | None, terms: list[str]) -> list[str]:
    """요약문에 등장하는 표제어를 등장 위치 순으로 반환 (최대 MAX_TERMS).

    긴 표제어가 차지한 구간은 마스킹해 겹침 방지 — '기준금리'가 잡히면 그 안의
    '금리'는 다시 잡지 않는다(프론트 하이라이트가 중첩되지 않게).
    """
    if not text:
        return []
    masked = text
    found: list[tuple[int, str]] = []  # (첫 등장 위치, 용어)
    for term in terms:  # 이미 긴 것 우선 정렬
        pos = masked.find(term)
        if pos < 0:
            continue
        found.append((pos, term))
        masked = masked.replace(term, "\x00" * len(term))  # 구간 마스킹
    found.sort()  # 등장 순서대로
    return [term for _pos, term in found[:MAX_TERMS]]
