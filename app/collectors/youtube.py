"""F-4.5 — 유튜브 영상 수집 (search.list 100유닛 + videos.list 1유닛).

쿼터 방어(F-4.5.1): 호출 전 quota_usage 카운터 확인, 일 한도 10,000의 80% 도달 시
신규 검색 중단. 국내·해외가 같은 쿼터를 쓴다. 캐시 TTL 24시간 — 같은 종목은 하루 1회만.
조회수순 상위는 수년 전 영상이 섞이므로 publishedAfter=90일을 함께 건다(실측 확정).
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import (
    ensure_stock_link,
    get_with_retry,
    is_fresh,
    mark_status,
    upsert_source_item,
)
from app.config import settings
from app.deps import utcnow
from app.models import QuotaUsage, StockMaster

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SEARCH_COST = 100  # search.list 고정 비용
VIDEOS_COST = 1
DAILY_LIMIT = 10_000
GUARD_RATIO = 0.8  # 80% 도달 시 신규 검색 중단 (F-4.5.1)
WINDOW_DAYS = 90
VIDEOS_PER_STOCK = 10
CACHE_TTL_HOURS = 24  # F-4.5 — 동일 종목 재검색 방지


def quota_used(db: Session) -> int:
    row = db.get(QuotaUsage, ("youtube", utcnow().date()))
    return row.units_used if row else 0


def add_quota(db: Session, units: int) -> None:
    """호출 직후 즉시 기록 — 이후 단계가 실패해도 사용량은 남는다."""
    key = ("youtube", utcnow().date())
    row = db.get(QuotaUsage, key)
    if row is None:
        row = QuotaUsage(api_name="youtube", usage_date=key[1], units_used=0)
        db.add(row)
    row.units_used += units
    db.commit()


def _search_query(stock: StockMaster) -> str:
    """해외 종목은 한글 별칭으로 검색해야 국내 투자자용 영상이 잡힌다 (확정사항 3절)."""
    if stock.aliases:
        return f"{stock.aliases[0]} 주식"
    return f"{stock.name} 주식"


def sync_youtube(db: Session, stocks: list[StockMaster]) -> dict:
    """고유 종목 단위 수집 — 세션 수와 무관하게 종목당 하루 1회 (F-4.5)."""
    stats = {"stocks": 0, "items": 0, "skipped_fresh": 0, "quota_stop": 0, "failed": 0}
    if not settings.youtube_api_key:
        logger.warning("YOUTUBE_API_KEY 미설정 — 유튜브 수집 건너뜀")
        return stats
    published_after = (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for stock in stocks:
        if is_fresh(db, "youtube", stock.stock_code, CACHE_TTL_HOURS):
            stats["skipped_fresh"] += 1
            continue
        if quota_used(db) + SEARCH_COST + VIDEOS_COST > DAILY_LIMIT * GUARD_RATIO:
            stats["quota_stop"] += 1
            mark_status(db, "youtube", stock.stock_code, False, "quota_guard")
            continue  # 카운터는 다음날 리셋 — 남은 종목도 기록만 남긴다
        try:
            resp = get_with_retry(
                SEARCH_URL,
                params={
                    "key": settings.youtube_api_key,
                    "part": "snippet",
                    "type": "video",
                    "q": _search_query(stock),
                    "order": "viewCount",
                    "publishedAfter": published_after,
                    "relevanceLanguage": "ko",
                    "maxResults": VIDEOS_PER_STOCK,
                },
            )
            add_quota(db, SEARCH_COST)
            found = resp.json().get("items", [])
            video_ids = [it["id"]["videoId"] for it in found if it.get("id", {}).get("videoId")]

            view_counts: dict[str, int] = {}
            if video_ids:
                vresp = get_with_retry(
                    VIDEOS_URL,
                    params={
                        "key": settings.youtube_api_key,
                        "part": "statistics",
                        "id": ",".join(video_ids),
                    },
                )
                add_quota(db, VIDEOS_COST)
                for v in vresp.json().get("items", []):
                    view_counts[v["id"]] = int(v["statistics"].get("viewCount", 0))

            for it in found:
                video_id = it.get("id", {}).get("videoId")
                if not video_id:
                    continue
                snippet = it["snippet"]
                item = upsert_source_item(
                    db,
                    tab="youtube",
                    market=stock.market,
                    source_key=video_id,
                    title=snippet["title"].strip(),  # 원문 그대로 (F-5.1.2)
                    published_at=datetime.fromisoformat(
                        snippet["publishedAt"].replace("Z", "+00:00")
                    ).replace(tzinfo=None),
                    origin_url=f"https://www.youtube.com/watch?v={video_id}",
                    thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    channel_name=snippet.get("channelTitle"),
                    view_count=view_counts.get(video_id),
                )
                ensure_stock_link(db, item.id, stock.stock_code)
            db.commit()
            mark_status(db, "youtube", stock.stock_code, True, f"{len(video_ids)}건")
            stats["stocks"] += 1
            stats["items"] += len(video_ids)
        except Exception as e:  # 종목 단위 격리 (F-4.9)
            db.rollback()
            mark_status(db, "youtube", stock.stock_code, False, str(e))
            stats["failed"] += 1
            logger.warning("유튜브 수집 실패 %s(%s): %s", stock.name, stock.stock_code, e)
    return stats
