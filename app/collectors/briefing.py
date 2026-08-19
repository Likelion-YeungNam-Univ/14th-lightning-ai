"""F-4.2 — 국내 규제 동향 수집 (정책브리핑 정책뉴스 API v2).

실측(2026-08-14, policyNewsService2 스웨거 + 실호출):
- 엔드포인트 `policyNewsService2/policyNewsList2` — 구 `policyNewsService`는 신규 키로 호출 불가
- 파라미터는 serviceKey·startDate·endDate(YYYYMMDD) **3개뿐** (페이지 파라미터 없음)
- **날짜 범위 최대 3일** (초과 시 오류 98) → 90일을 3일 청크 30회로 나눠 훑는다
- ApproveDate 형식 `MM/DD/YYYY HH24:MI:SS`
- 인증키는 포털이 URL 인코딩된 값을 주므로 디코딩 후 사용(이중 인코딩 방지)

산업 키워드로 1차 매칭한다 — 부처(MinisterCode)를 1차 필터로 쓰지 않는다(F-4.2.2, v3 갱신).
정부조직 개편으로 부처명이 바뀌면 부처 매핑이 먼저 깨져 탭이 조용히 빈다. 부처명은 매핑표
갱신 필요 여부를 진단하는 참고 정보로만 로그에 남긴다(V1 대조용). 이미지·사진 URL은
저장·노출하지 않는다(공공누리). 본문 텍스트는 요약 입력용으로만 content에 보관한다.
"""

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import unquote

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

LIST_URL = "https://apis.data.go.kr/1371000/policyNewsService2/policyNewsList2"
WINDOW_DAYS = 90  # 최근 3개월 (F-4.2)
CHUNK_DAYS = 3  # API 제한 — 날짜 범위 3일 초과 시 오류 98
SLEEP_BETWEEN = 0.2  # 초당 요청 제한(오류 23) 회피 — 테스트에서 0으로 패치

_TAG_RE = re.compile(r"<[^>]+>")


def _service_key() -> str:
    """포털 '일반 인증키'는 URL 인코딩된 형태 — 그대로 쓰면 이중 인코딩되어 인증 실패."""
    key = settings.briefing_api_key
    return unquote(key) if "%" in key else key


def _strip_html(raw: str | None) -> str:
    return html.unescape(_TAG_RE.sub(" ", raw or "")).strip()


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()[:19]
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _fetch_chunk(start: datetime, end: datetime) -> list[ET.Element]:
    resp = get_with_retry(
        LIST_URL,
        params={
            "serviceKey": _service_key(),
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        },
    )
    root = ET.fromstring(resp.text)
    gateway_err = root.findtext(".//returnReasonCode")
    if gateway_err:
        msg = root.findtext(".//returnAuthMsg")
        raise RuntimeError(f"정책브리핑 게이트웨이 오류 {gateway_err}: {msg}")
    result_code = (root.findtext(".//resultCode") or "0").strip()
    if result_code not in ("0", "00"):
        raise RuntimeError(f"정책브리핑 오류 {result_code}: {root.findtext('.//resultMsg')}")
    return list(root.iter("NewsItem"))


def sync_regulations(db: Session) -> dict:
    """산업 키워드로 업종 단위 적재. 부처는 필터가 아니라 매핑표 진단용 참고 정보(F-4.2.2)."""
    agencies = (
        db.query(IndustryAgency)
        .filter(IndustryAgency.market == MARKET_DOMESTIC, IndustryAgency.industry_key != "etc")
        .all()
    )
    known_ministries = {name for ind in agencies for name in ind.agencies}

    stats = {"chunks": 0, "scanned": 0, "matched": 0}
    unmatched: dict[str, int] = {}
    now = utcnow()

    try:
        chunk_start = now - timedelta(days=WINDOW_DAYS)
        while chunk_start <= now:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), now)
            if stats["chunks"]:
                time.sleep(SLEEP_BETWEEN)
            news_items = _fetch_chunk(chunk_start, chunk_end)
            stats["chunks"] += 1
            stats["scanned"] += len(news_items)

            for news in news_items:
                ministry = (news.findtext("MinisterCode") or "").strip()
                if ministry and ministry not in known_ministries:
                    # 필터링엔 안 쓴다 — 매핑표(data/*.json) 갱신 필요 여부만 진단(V1)
                    unmatched[ministry] = unmatched.get(ministry, 0) + 1

                title = _strip_html(news.findtext("Title"))
                body = _strip_html(news.findtext("DataContents"))
                text = f"{title} {body}"
                hit = [ind for ind in agencies if any(kw in text for kw in ind.keywords)]
                if not hit or not title:
                    continue  # 산업 키워드 무관 — 버린다

                item = upsert_source_item(
                    db,
                    tab="regulation",
                    market=MARKET_DOMESTIC,
                    source_key=(news.findtext("NewsItemId") or "").strip(),
                    title=title,  # 원문 그대로 (F-5.1.2)
                    published_at=_parse_date(news.findtext("ApproveDate")),
                    origin_url=(news.findtext("OriginalUrl") or "").strip() or None,
                    content=body[:8000],  # 요약 입력용 — 이미지 URL은 저장하지 않는다
                )
                for ind in hit:
                    ensure_industry_link(db, item.id, MARKET_DOMESTIC, ind.industry_key)
                stats["matched"] += 1

            chunk_start = chunk_end + timedelta(days=1)

        db.commit()
        mark_status(db, "regulation", "domestic", True, f"{stats['matched']}건")
    except Exception as e:
        db.rollback()
        mark_status(db, "regulation", "domestic", False, str(e))
        logger.warning("규제 동향 수집 실패: %s", e)
        stats["error"] = str(e)[:200]

    if unmatched:  # 부처명 개편 대조용 — 매핑표(data/)만 고치면 된다
        top = sorted(unmatched.items(), key=lambda kv: -kv[1])[:10]
        logger.info("매핑에 없는 부처명 상위(대조용): %s", top)
    return stats
