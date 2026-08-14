"""F-4.2 — 국내 규제 동향 수집 (정책브리핑 정책뉴스 API).

부처 1차 필터 → 산업 키워드 2차 필터. 조회 기간 3개월.
이미지·사진·영상 URL은 저장·노출하지 않는다(공공누리). 본문 텍스트는 요약 입력용으로만
content에 보관하고 화면에 직접 노출하지 않는다 (확정사항 6절).

주의: 응답은 XML. 부처명 필드(MinisterCode)가 2025.10 조직 개편을 반영했는지 첫 실행
로그로 대조한다(V1) — 미매핑 부처명 상위를 남기므로 매핑표(data/)만 고치면 된다.
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import (
    ensure_industry_link,
    get_with_retry,
    mark_status,
    upsert_source_item,
)
from app.config import settings
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, IndustryAgency

logger = logging.getLogger(__name__)

LIST_URL = "http://apis.data.go.kr/1371000/policyNewsService/policyNewsList"
WINDOW_DAYS = 90  # 최근 3개월 (F-4.2)
PAGE_SIZE = 100
MAX_PAGES = 40  # 3개월 전체 상한 — 일 트래픽(10,000) 대비 안전

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str | None) -> str:
    return html.unescape(_TAG_RE.sub(" ", raw or "")).strip()


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw.strip()[: len(fmt) + 2][:19], fmt)
        except ValueError:
            continue
    return None


def sync_regulations(db: Session) -> dict:
    """부처 발표를 업종 단위로 적재. 매핑 없는 업종은 조회하지 않는다(F-4.2.1)."""
    agencies = (
        db.query(IndustryAgency)
        .filter(IndustryAgency.market == MARKET_DOMESTIC, IndustryAgency.industry_key != "etc")
        .all()
    )
    # 부처명 → 업종 목록 역인덱스 (한 부처가 여러 업종 소관)
    by_ministry: dict[str, list[IndustryAgency]] = {}
    for ind in agencies:
        for name in ind.agencies:
            by_ministry.setdefault(name, []).append(ind)

    stats = {"pages": 0, "scanned": 0, "matched": 0}
    unmatched: dict[str, int] = {}
    start = (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    end = utcnow().strftime("%Y%m%d")

    try:
        for page in range(1, MAX_PAGES + 1):
            resp = get_with_retry(
                LIST_URL,
                params={
                    "serviceKey": settings.briefing_api_key,
                    "startDate": start,
                    "endDate": end,
                    "pageNo": page,
                    "numOfRows": PAGE_SIZE,
                },
            )
            root = ET.fromstring(resp.text)
            err = root.findtext(".//returnReasonCode")
            if err:
                raise RuntimeError(f"정책브리핑 오류 {err}: {root.findtext('.//returnAuthMsg')}")

            news_items = list(root.iter("NewsItem"))
            stats["pages"] = page
            stats["scanned"] += len(news_items)

            for news in news_items:
                ministry = (news.findtext("MinisterCode") or "").strip()
                candidates = by_ministry.get(ministry)
                if not candidates:
                    if ministry:
                        unmatched[ministry] = unmatched.get(ministry, 0) + 1
                    continue

                title = _strip_html(news.findtext("Title"))
                body = _strip_html(news.findtext("DataContents"))
                text = f"{title} {body}"
                hit = [ind for ind in candidates if any(kw in text for kw in ind.keywords)]
                if not hit or not title:
                    continue  # 부처는 소관이지만 산업 키워드 무관 — 버린다

                item = upsert_source_item(
                    db,
                    tab="regulation",
                    market=MARKET_DOMESTIC,
                    source_key=(news.findtext("NewsItemId") or "").strip(),
                    title=title,  # 원문 그대로 (F-5.1.2)
                    published_at=_parse_date(news.findtext("ApproveDate")),
                    origin_url=(
                        (news.findtext("OriginalUrl") or news.findtext("TitleUrl") or "").strip()
                        or None
                    ),
                    content=body[:8000],  # 요약 입력용 — 이미지 URL은 저장하지 않는다
                )
                for ind in hit:
                    ensure_industry_link(db, item.id, MARKET_DOMESTIC, ind.industry_key)
                stats["matched"] += 1

            if len(news_items) < PAGE_SIZE:
                break

        db.commit()
        mark_status(db, "regulation", "domestic", True, f"{stats['matched']}건")
    except Exception as e:
        db.rollback()
        mark_status(db, "regulation", "domestic", False, str(e))
        logger.warning("규제 동향 수집 실패: %s", e)
        stats["error"] = str(e)[:200]

    if unmatched:  # V1 — 부처명 문자열 대조 근거
        top = sorted(unmatched.items(), key=lambda kv: -kv[1])[:10]
        logger.info("매핑에 없는 부처명 상위(V1 대조용): %s", top)
    return stats
