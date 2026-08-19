"""F-4.7 — 미국 규제 동향 (Federal Register API). 키 불필요, 퍼블릭 도메인.

실측(2026-08-14): /api/v1/documents.json — conditions[type][]=RULE·PRORULE(확정),
conditions[agencies][]=슬러그, fields[]로 필요한 필드만. /api/v1/agencies가 유효
슬러그 목록을 준다 → 매핑표의 무효 슬러그는 자동 폐기 (F-4.7.1).
**기관 필터 없이 조회하지 않는다** — 연 8만 건 규모(F-4.7).
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import (
    ensure_industry_link,
    get_with_retry,
    mark_status,
    upsert_source_item,
)
from app.deps import utcnow
from app.models import MARKET_OVERSEAS, IndustryAgency

logger = logging.getLogger(__name__)

AGENCIES_URL = "https://www.federalregister.gov/api/v1/agencies"
DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
DOC_TYPES = ("RULE", "PRORULE")  # 확정사항 3절 — PRESDOCU 제외
WINDOW_DAYS = 90
PER_PAGE = 100
MAX_PAGES = 10  # 기관 필터 하에서 3개월이면 충분한 상한


def fetch_valid_agency_slugs() -> set[str]:
    data = get_with_retry(AGENCIES_URL).json()
    return {a["slug"] for a in data if a.get("slug")}


def sync_us_regulations(db: Session) -> dict:
    """기관 필터 → 산업 키워드 2차 필터 → SIC 업종 링크. 최종 관련성은 요약 단계(F-4.7.2)."""
    industries = db.query(IndustryAgency).filter(IndustryAgency.market == MARKET_OVERSEAS).all()
    if not industries:
        mark_status(db, "regulation", "overseas", True, "업종 매핑 없음 — 시드 필요")
        return {"scanned": 0, "matched": 0}

    stats = {"scanned": 0, "matched": 0, "dropped_slugs": 0}
    try:
        valid = fetch_valid_agency_slugs()
        # 슬러그 → 업종 역인덱스 (무효 슬러그는 폐기 + 로그, F-4.7.1)
        by_agency: dict[str, list[IndustryAgency]] = {}
        for ind in industries:
            for slug in ind.agencies:
                if slug not in valid:
                    stats["dropped_slugs"] += 1
                    logger.warning(
                        "유효 목록에 없는 기관 슬러그 폐기: %s (%s)", slug, ind.industry_key
                    )
                    continue
                by_agency.setdefault(slug, []).append(ind)

        gte = (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
        params: list[tuple[str, str]] = [
            ("conditions[publication_date][gte]", gte),
            ("per_page", str(PER_PAGE)),
            ("order", "newest"),
        ]
        params += [("conditions[type][]", t) for t in DOC_TYPES]
        params += [("conditions[agencies][]", slug) for slug in sorted(by_agency)]
        params += [
            ("fields[]", f)
            for f in (
                "title",
                "abstract",
                "agencies",
                "publication_date",
                "html_url",
                "document_number",
                "type",
            )
        ]

        page = 1
        url: str | None = DOCUMENTS_URL
        page_params: list[tuple[str, str]] | None = params
        while url and page <= MAX_PAGES:
            data = get_with_retry(url, params=page_params).json()
            results = data.get("results", [])
            stats["scanned"] += len(results)
            for doc in results:
                doc_slugs = {a.get("slug") for a in (doc.get("agencies") or [])}
                candidates = {
                    ind.industry_key: ind for slug in doc_slugs for ind in by_agency.get(slug, [])
                }
                title = (doc.get("title") or "").strip()
                abstract = (doc.get("abstract") or "").strip()
                text = f"{title} {abstract}".lower()
                hit = [
                    ind
                    for ind in candidates.values()
                    if any(kw.lower() in text for kw in ind.keywords)
                ]
                if not hit or not title:
                    continue  # 소관 기관이지만 산업 키워드 무관 — 버린다
                item = upsert_source_item(
                    db,
                    tab="regulation",
                    market=MARKET_OVERSEAS,
                    source_key=doc["document_number"],
                    title=title,  # 원문 영문 그대로 — 번역하지 않는다 (F-5.1.2)
                    doc_type=doc.get("type"),
                    published_at=datetime.strptime(doc["publication_date"], "%Y-%m-%d"),
                    origin_url=doc.get("html_url"),
                    content=abstract[:8000],  # 요약 입력용 초록
                )
                for ind in hit:
                    ensure_industry_link(db, item.id, MARKET_OVERSEAS, ind.industry_key)
                stats["matched"] += 1

            url = data.get("next_page_url")  # 다음 페이지 URL을 API가 준다
            page_params = None
            page += 1

        db.commit()
        mark_status(db, "regulation", "overseas", True, f"{stats['matched']}건")
    except Exception as e:
        db.rollback()
        mark_status(db, "regulation", "overseas", False, str(e))
        logger.warning("미국 규제 수집 실패: %s", e)
        stats["error"] = str(e)[:200]
    return stats
